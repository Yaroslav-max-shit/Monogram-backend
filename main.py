from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from routes.e2ee import router as e2ee_router
from routes.premium import router as premium_router
from routes.stickers import router as stickers_router
from routes.search import router as search_router
from routes.calls import router as calls_router
import os
from pathlib import Path
from typing import Dict, Set
import json
import asyncio
from dotenv import load_dotenv

#   
load_dotenv()

backend_dir = Path(__file__).parent.absolute()
os.chdir(backend_dir)

from database import engine, Base, SessionLocal
from models import User, Profile, Chat, Membership, Message
from routes import auth_router, users_router, messages_router, chats_router, admin_router, bots_router
from routes.bot_api import router as bot_api_router
from routes.bot_management import router as bot_management_router
from routes.settings import router as settings_router
from routes.payment import router as payment_router
from routes.drafts import router as drafts_router
from routes.gamification import router as gamification_router
from routes.ai import router as ai_router
from routes.stories import router as stories_router
from logging_config import logger
from middleware.rate_limiter import RateLimiterMiddleware
from middleware.metrics import MetricsMiddleware
from metrics import router as metrics_router
from config import settings
import secrets

# ============================================
#  FastAPI приложение
# ============================================

app = FastAPI(title="Monogram Messenger API", version="1.0.0")

async def auto_delete_scheduler():
    while True:
        try:
            db = SessionLocal()
            from models import Message as Msg
            now = datetime.utcnow()
            deleted = db.query(Msg).filter(Msg.auto_delete_at != None, Msg.auto_delete_at <= now).delete()
            if deleted:
                db.commit()
                logger.info(f"Auto-deleted {deleted} messages")
            db.close()
        except Exception as e:
            logger.error(f"Auto-delete error: {e}")
        await asyncio.sleep(30)

async def scheduled_message_sender():
    while True:
        try:
            db = SessionLocal()
            from models import Message as Msg
            now = datetime.utcnow()
            pending = db.query(Msg).filter(Msg.scheduled_for != None, Msg.scheduled_for <= now, Msg.is_deleted == False).all()
            for msg in pending:
                msg.scheduled_for = None
                db.commit()
                logger.info(f"Sent scheduled message {msg.id}")
            db.close()
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
        await asyncio.sleep(30)

@app.on_event("startup")
async def startup_admin():
    create_tables()
    apply_migrations()
    create_system_chats()
    ensure_admin()
    asyncio.create_task(auto_delete_scheduler())
    asyncio.create_task(scheduled_message_sender())
    logger.info("Startup complete: tables, migrations, admin ensured")

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = secrets.token_hex(8)
    logger.error(f"[{request_id}] Unhandled error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Внутренняя ошибка сервера", "request_id": request_id}
    )

# ============================================
# MIDDLEWARE (порядок важен)
# ============================================

# 1. CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_origin_regex=r"https?://.*\.devtunnels\.ms(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RateLimiterMiddleware)
app.add_middleware(MetricsMiddleware)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        nonce = secrets.token_hex(16)
        request.state.csp_nonce = nonce
        response = await call_next(request)
        
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}'; "
            f"style-src 'self' 'unsafe-inline'; "
            f"img-src 'self' data: blob: https:; "
            f"connect-src 'self' ws: wss: http: https:; "
            f"font-src 'self' data:; "
            f"media-src 'self' blob:; "
            f"frame-ancestors 'none'; "
            f"form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# CSRF отключен — авторизация через JWT Bearer token (безопасна от CSRF)

# ============================================
# WebSocket Manager (управление подключениями)
# ============================================

class WSManager:
    def __init__(self):
        self.user_connections: Dict[int, Set[WebSocket]] = {}
        self.ws_to_user: Dict[WebSocket, int] = {}
        self.user_chats: Dict[int, Set[int]] = {}
        self._lock = asyncio.Lock()  # для потокобезопасности

    async def connect(self, user_id: int, ws: WebSocket):
        await ws.accept()
        
        async with self._lock:
            if user_id not in self.user_connections:
                self.user_connections[user_id] = set()
            self.user_connections[user_id].add(ws)
            self.ws_to_user[ws] = user_id
        
        await self.load_user_chats(user_id)
        
        logger.info(f"Пользователь {user_id} подключился. Всего: {sum(len(v) for v in self.user_connections.values())}")

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            user_id = self.ws_to_user.pop(ws, None)
            if user_id and user_id in self.user_connections:
                self.user_connections[user_id].discard(ws)
                if not self.user_connections[user_id]:
                    del self.user_connections[user_id]
                    self.user_chats.pop(user_id, None)
                logger.info(f"Пользователь {user_id} отключился")

    async def load_user_chats(self, user_id: int):
        db = SessionLocal()
        try:
            memberships = db.query(Membership).filter(Membership.user_id == user_id).all()
            async with self._lock:
                self.user_chats[user_id] = {m.chat_id for m in memberships}
        except Exception as e:
            logger.error(f"Failed to load chats for user {user_id}: {e}")
            async with self._lock:
                self.user_chats[user_id] = set()
        finally:
            db.close()

    async def send_to_chat(self, chat_id: int, message: dict, exclude_user_id: int | None = None):
        sent_count = 0
        async with self._lock:
            # Отправка приветственного события
            user_chats_copy = self.user_chats.copy()
        
        for user_id, chat_set in user_chats_copy.items():
            if exclude_user_id and user_id == exclude_user_id:
                continue
            
            if chat_id in chat_set:
                dead_ws = set()
                for ws in self.user_connections.get(user_id, set()):
                    try:
                        await ws.send_json(message)
                        sent_count += 1
                    except Exception:
                        dead_ws.add(ws)
                
                for ws in dead_ws:
                    await self.disconnect(ws)
        
        logger.info(f"Отправлено в чат {chat_id}: {sent_count} получателей")

    async def broadcast_to_user(self, user_id: int, message: dict):
        dead_ws = set()
        for ws in self.user_connections.get(user_id, set()):
            try:
                await ws.send_json(message)
            except Exception:
                dead_ws.add(ws)
        
        for ws in dead_ws:
            await self.disconnect(ws)

ws_manager = WSManager()

# ============================================
# SSE Subscribers (подписчики событий)
# ============================================

sse_subscribers: Dict[int, asyncio.Queue] = {}
sse_lock = asyncio.Lock()

async def send_sse_event(user_id: int, event: dict):
    async with sse_lock:
        if user_id in sse_subscribers:
            try:
                await sse_subscribers[user_id].put(event)
            except Exception as e:
                logger.error(f"SSE send error to user {user_id}: {e}")

# ============================================
# WEBSOCKET ENDPOINT (с аутентификацией)
# ============================================

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int, token: str = ""):
    if not token:
        token = websocket.query_params.get("token", "")
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return
    try:
        from jose import jwt, JWTError
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        token_user_id = int(payload.get("user_id") or payload.get("sub", 0))
        if token_user_id != user_id:
            await websocket.close(code=4003, reason="User mismatch")
            return
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    await ws_manager.connect(user_id, websocket)
    
    async def keepalive():
        while True:
            try:
                await asyncio.sleep(30)
                if websocket.client_state.name == "CONNECTED":
                    await websocket.send_json({"type": "ping"})
            except Exception:
                break
    
    keepalive_task = asyncio.create_task(keepalive())
    
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=120.0)
            except asyncio.TimeoutError:
                continue
            
            chat_id = data.get("chat_id")
            content = data.get("content", "")
            target_user_id = data.get("target_user_id")

            if data.get("type") in ["call-start", "call-accept", "call-reject", "call-end", "offer", "answer", "ice-candidate"]:
                if target_user_id:
                    data["sender_id"] = user_id
                    await ws_manager.broadcast_to_user(target_user_id, data)
                continue

            if data.get("type") == "typing":
                if chat_id:
                    await ws_manager.send_to_chat(chat_id, {"type": "typing", "user_id": user_id, "chat_id": chat_id}, exclude_user_id=user_id)
                continue

            if chat_id is None:
                continue

            def _db_write():
                db = SessionLocal()
                try:
                    membership = db.query(Membership).filter(Membership.user_id == user_id, Membership.chat_id == chat_id).first()
                    if not membership:
                        return None, "not_member"
                    db_chat = db.query(Chat).filter(Chat.id == chat_id).first()
                    if not db_chat:
                        return None, "no_chat"
                    db_message = Message(content=content, sender_id=user_id, chat_id=chat_id)
                    db.add(db_message)
                    db.commit()
                    db.refresh(db_message)
                    return db_message, None
                except Exception as e:
                    db.rollback()
                    raise e
                finally:
                    db.close()

            try:
                db_message, error = await asyncio.to_thread(_db_write)
            except Exception as e:
                logger.error(f"WS db error: {e}")
                continue

            if error:
                continue

            data["timestamp"] = db_message.timestamp.isoformat()
            data["sender_id"] = user_id
            data["type"] = "new_message"

            await ws_manager.send_to_chat(chat_id, data, exclude_user_id=user_id)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WS error: {e}")
    finally:
        keepalive_task.cancel()
        await ws_manager.disconnect(websocket)
        await ws_manager.disconnect(websocket)

# ============================================
# SSE ENDPOINT (с аутентификацией)
# ============================================

@app.get("/api/sse/{user_id}")
async def sse_endpoint(user_id: int, token: str = ""):
    # Authenticate SSE via query param token
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")
    try:
        from jose import jwt, JWTError
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        token_user_id = int(payload.get("user_id") or payload.get("sub", 0))
        if token_user_id != user_id:
            raise HTTPException(status_code=403, detail="Token user_id mismatch")
    except (JWTError, ValueError, Exception):
        raise HTTPException(status_code=401, detail="Invalid authentication token")
    
    queue = asyncio.Queue()
    async with sse_lock:
        sse_subscribers[user_id] = queue
    
    async def event_generator():
        try:
            #   
            yield f"data: {json.dumps({'type': 'connected', 'user_id': user_id})}\n\n"
            
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield f": ping\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            async with sse_lock:
                sse_subscribers.pop(user_id, None)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

# ============================================
# API ENDPOINTS
# ============================================

@app.get("/api/wallpapers")
async def get_wallpapers():
    wallpaper_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "public", "assets", "wallpapers")
    wallpapers = []
    if os.path.exists(wallpaper_dir):
        for f in os.listdir(wallpaper_dir):
            if f.endswith(('.png', '.jpg', '.jpeg')):
                name = os.path.splitext(f)[0]
                wallpapers.append(name)
    return wallpapers

@app.post("/api/settings/wallpaper")
async def save_wallpaper(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    wallpaper = data.get("wallpaper", "default")
    logger.info(f"Сохранение обоев: {wallpaper}")
    return {"status": "ok", "wallpaper": wallpaper}

@app.get("/api/health")
async def health():
    return JSONResponse({
        "status": "ok",
        "connections": sum(len(v) for v in ws_manager.user_connections.values()),
        "users_online": len(ws_manager.user_connections),
        "version": "1.0.0"
    })

@app.get("/bots/docs", include_in_schema=False)
async def bot_docs():
    docs_path = os.path.join(backend_dir, "bot_docs.html")
    if os.path.exists(docs_path):
        with open(docs_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Bot API Documentation</h1><p>Documentation file not found.</p>")

@app.get("/")
async def root():
    return {"message": "Monogram Messenger API", "version": "1.0.0", "status": "running"}

# ============================================
#  
# ============================================

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(messages_router)
app.include_router(chats_router)
app.include_router(admin_router)
app.include_router(bots_router)
app.include_router(bot_api_router)
app.include_router(bot_management_router)
app.include_router(settings_router)
app.include_router(e2ee_router)
app.include_router(premium_router)
app.include_router(payment_router)
app.include_router(stickers_router)
app.include_router(search_router)
app.include_router(calls_router)
app.include_router(metrics_router)
app.include_router(drafts_router)
app.include_router(gamification_router)
app.include_router(ai_router)
app.include_router(stories_router)

# ============================================
# Статические файлы
# ============================================

# Создание директорий для загрузок
UPLOAD_DIRS = ["uploads", "uploads/avatars", "uploads/files", "uploads/stickers", "uploads/wallpapers", "uploads/audio"]
for dir_path in UPLOAD_DIRS:
    os.makedirs(dir_path, exist_ok=True)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# ============================================
# Миграции и инициализация БД
# ============================================

def create_tables():
    """Создание таблиц"""
    Base.metadata.create_all(bind=engine)
    logger.info("Таблицы созданы")

def apply_migrations():
    """    """
    db = SessionLocal()
    try:
        from sqlalchemy import inspect, text
        
        inspector = inspect(engine)
        
        #   bot_id  messages
        msg_columns = [col['name'] for col in inspector.get_columns('messages')]
        if 'bot_id' not in msg_columns:
            db.execute(text("ALTER TABLE messages ADD COLUMN bot_id INTEGER DEFAULT NULL"))
            db.commit()
        
        #   is_bot  users
        user_columns = [col['name'] for col in inspector.get_columns('users')]
        if 'is_bot' not in user_columns:
            db.execute(text("ALTER TABLE users ADD COLUMN is_bot BOOLEAN DEFAULT 0"))
            db.commit()
        
        #   premium_until  users
        if 'premium_until' not in user_columns:
            db.execute(text("ALTER TABLE users ADD COLUMN premium_until TIMESTAMP DEFAULT NULL"))
            db.commit()
        
        #   last_login  users
        if 'last_login' not in user_columns:
            db.execute(text("ALTER TABLE users ADD COLUMN last_login TIMESTAMP DEFAULT NULL"))
            db.commit()
        
        #   is_deleted  messages
        if 'is_deleted' not in msg_columns:
            db.execute(text("ALTER TABLE messages ADD COLUMN is_deleted BOOLEAN DEFAULT 0"))
            db.commit()
        
        #   is_forwarded  messages
        if 'is_forwarded' not in msg_columns:
            db.execute(text("ALTER TABLE messages ADD COLUMN is_forwarded BOOLEAN DEFAULT 0"))
            db.commit()
        
        #   scheduled_for  messages
        if 'scheduled_for' not in msg_columns:
            db.execute(text("ALTER TABLE messages ADD COLUMN scheduled_for TIMESTAMP DEFAULT NULL"))
            db.commit()
        
        #   reply_to_id, read_at, delivered_at, edited_at, reactions_json  messages
        if 'reply_to_id' not in msg_columns:
            db.execute(text("ALTER TABLE messages ADD COLUMN reply_to_id INTEGER DEFAULT NULL"))
            db.commit()
        if 'read_at' not in msg_columns:
            db.execute(text("ALTER TABLE messages ADD COLUMN read_at TIMESTAMP DEFAULT NULL"))
            db.commit()
        if 'delivered_at' not in msg_columns:
            db.execute(text("ALTER TABLE messages ADD COLUMN delivered_at TIMESTAMP DEFAULT NULL"))
            db.commit()
        if 'edited_at' not in msg_columns:
            db.execute(text("ALTER TABLE messages ADD COLUMN edited_at TIMESTAMP DEFAULT NULL"))
            db.commit()
        if 'reactions_json' not in msg_columns:
            db.execute(text("ALTER TABLE messages ADD COLUMN reactions_json TEXT DEFAULT '{}'"))
            db.commit()
        if 'is_pinned' not in msg_columns:
            db.execute(text("ALTER TABLE messages ADD COLUMN is_pinned BOOLEAN DEFAULT 0"))
            db.commit()
        if 'auto_delete_at' not in msg_columns:
            db.execute(text("ALTER TABLE messages ADD COLUMN auto_delete_at TIMESTAMP DEFAULT NULL"))
            db.commit()
        
        #   emoji_status, emoji_status_expires  users
        user_columns = [col['name'] for col in inspector.get_columns('users')]
        if 'emoji_status' not in user_columns:
            db.execute(text("ALTER TABLE users ADD COLUMN emoji_status VARCHAR(20) DEFAULT NULL"))
            db.execute(text("ALTER TABLE users ADD COLUMN emoji_status_expires TIMESTAMP DEFAULT NULL"))
            db.commit()
        
        #   pinned_message_id, slow_mode_delay  chats
        chat_columns = [col['name'] for col in inspector.get_columns('chats')]
        if 'pinned_message_id' not in chat_columns:
            db.execute(text("ALTER TABLE chats ADD COLUMN pinned_message_id INTEGER DEFAULT NULL"))
            db.execute(text("ALTER TABLE chats ADD COLUMN slow_mode_delay INTEGER DEFAULT 0"))
            db.commit()
        
        #   company, position, working_hours, website  profiles
        profile_columns = [col['name'] for col in inspector.get_columns('profiles')]
        for col_name in ['company', 'position', 'working_hours', 'website']:
            if col_name not in profile_columns:
                db.execute(text(f"ALTER TABLE profiles ADD COLUMN {col_name} VARCHAR(500) DEFAULT NULL"))
                db.commit()
                logger.info(f"  {col_name}  profiles")
        
        #   wallpaper  chats
        chat_columns = [col['name'] for col in inspector.get_columns('chats')]
        if 'wallpaper' not in chat_columns:
            db.execute(text("ALTER TABLE chats ADD COLUMN wallpaper VARCHAR(500) DEFAULT NULL"))
            db.commit()
            logger.info("  wallpaper  chats")
        
        #   scheduled_messages
        tables = inspector.get_table_names()
        if 'scheduled_messages' not in tables:
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS scheduled_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    sender_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    scheduled_for TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status VARCHAR(20) DEFAULT 'pending',
                    FOREIGN KEY (sender_id) REFERENCES users(id),
                    FOREIGN KEY (chat_id) REFERENCES chats(id)
                )
            """))
            db.commit()
            logger.info("   scheduled_messages")
        
        #   sticker_packs
        if 'sticker_packs' not in tables:
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS sticker_packs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(100) NOT NULL,
                    code VARCHAR(8) UNIQUE NOT NULL,
                    author_id INTEGER NOT NULL,
                    is_public BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (author_id) REFERENCES users(id)
                )
            """))
            db.commit()
            logger.info("   sticker_packs")
        
        #   stickers
        if 'stickers' not in tables:
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS stickers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pack_id INTEGER NOT NULL,
                    type VARCHAR(20) DEFAULT 'image',
                    url VARCHAR(500) NOT NULL,
                    "order" INTEGER DEFAULT 0,
                    FOREIGN KEY (pack_id) REFERENCES sticker_packs(id)
                )
            """))
            db.commit()
            logger.info("   stickers")
        
        #   reports
        if 'reports' not in tables:
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reporter_id INTEGER NOT NULL,
                    reported_user_id INTEGER NOT NULL,
                    reason VARCHAR(200) NOT NULL,
                    message_id INTEGER DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status VARCHAR(20) DEFAULT 'pending',
                    FOREIGN KEY (reporter_id) REFERENCES users(id),
                    FOREIGN KEY (reported_user_id) REFERENCES users(id),
                    FOREIGN KEY (message_id) REFERENCES messages(id)
                )
            """))
            db.commit()
            logger.info("   reports")
        
        #   bot_webhooks
        if 'bot_webhooks' not in tables:
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS bot_webhooks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id INTEGER NOT NULL UNIQUE,
                    url VARCHAR(500) NOT NULL,
                    max_connections INTEGER DEFAULT 40,
                    allowed_updates TEXT DEFAULT '[]',
                    last_error_date TIMESTAMP DEFAULT NULL,
                    last_error_message TEXT DEFAULT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (bot_id) REFERENCES bots(id)
                )
            """))
            db.commit()
            logger.info("   bot_webhooks")
        
        #   bot_commands
        if 'bot_commands' not in tables:
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS bot_commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id INTEGER NOT NULL,
                    command VARCHAR(32) NOT NULL,
                    description VARCHAR(256) NOT NULL,
                    FOREIGN KEY (bot_id) REFERENCES bots(id)
                )
            """))
            db.commit()
            logger.info("   bot_commands")
        
        #   user_settings
        if 'user_settings' not in tables:
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE NOT NULL,
                    theme VARCHAR(20) DEFAULT 'dark',
                    language VARCHAR(10) DEFAULT 'ru',
                    fontSize INTEGER DEFAULT 14,
                    notifications_enabled BOOLEAN DEFAULT 1,
                    sound_enabled BOOLEAN DEFAULT 1,
                    wallpaper VARCHAR(100) DEFAULT 'default',
                    who_can_see_photo VARCHAR(20) DEFAULT 'all',
                    who_can_see_bio VARCHAR(20) DEFAULT 'all',
                    who_can_see_last_seen VARCHAR(20) DEFAULT 'all',
                    who_can_add_to_groups VARCHAR(20) DEFAULT 'all',
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """))
            db.commit()
            logger.info("   user_settings")
        
        #   invite_link  chats
        chat_columns = [col['name'] for col in inspector.get_columns('chats')]
        if 'invite_link' not in chat_columns:
            db.execute(text("ALTER TABLE chats ADD COLUMN invite_link VARCHAR(100) DEFAULT NULL"))
            db.commit()
            logger.info("  invite_link  chats")
        
        #   show_stats  user_settings
        if 'user_settings' in tables:
            us_columns = [col['name'] for col in inspector.get_columns('user_settings')]
            if 'show_stats' not in us_columns:
                db.execute(text("ALTER TABLE user_settings ADD COLUMN show_stats BOOLEAN DEFAULT 1"))
                db.commit()
                logger.info("  show_stats  user_settings")
            
            if 'mute_when_online' not in us_columns:
                db.execute(text("ALTER TABLE user_settings ADD COLUMN mute_when_online BOOLEAN DEFAULT 0"))
                db.commit()
                logger.info("  mute_when_online  user_settings")
            
            if 'smart_notifications' not in us_columns:
                db.execute(text("ALTER TABLE user_settings ADD COLUMN smart_notifications BOOLEAN DEFAULT 0"))
                db.commit()
                logger.info("  smart_notifications  user_settings")
        
        #   auto_delete_after  chats
        chat_columns = [col['name'] for col in inspector.get_columns('chats')]
        if 'auto_delete_after' not in chat_columns:
            db.execute(text("ALTER TABLE chats ADD COLUMN auto_delete_after INTEGER DEFAULT 0"))
            db.commit()

        #   transfers_enabled  chats
        if 'transfers_enabled' not in chat_columns:
            db.execute(text("ALTER TABLE chats ADD COLUMN transfers_enabled BOOLEAN DEFAULT 0"))
            db.commit()
            logger.info("  transfers_enabled  chats")

        # Добавление profile_id в users (6-значный публичный ID)
        user_columns = [col['name'] for col in inspector.get_columns('users')]
        if 'profile_id' not in user_columns:
            db.execute(text("ALTER TABLE users ADD COLUMN profile_id INTEGER DEFAULT NULL"))
            db.commit()
            logger.info("Добавлена колонка profile_id в users")

        #   is_invisible  users
        if 'is_invisible' not in user_columns:
            db.execute(text("ALTER TABLE users ADD COLUMN is_invisible BOOLEAN DEFAULT 0"))
            db.commit()

        #   birth_date  users
        if 'birth_date' not in user_columns:
            db.execute(text("ALTER TABLE users ADD COLUMN birth_date VARCHAR(10) DEFAULT NULL"))
            db.commit()
            logger.info("Добавлена колонка birth_date в users")

        #   forwarded_from_message_id, original_chat_id  messages
        msg_columns = [col['name'] for col in inspector.get_columns('messages')]
        if 'forwarded_from_message_id' not in msg_columns:
            db.execute(text("ALTER TABLE messages ADD COLUMN forwarded_from_message_id INTEGER DEFAULT NULL"))
            db.commit()
        if 'original_chat_id' not in msg_columns:
            db.execute(text("ALTER TABLE messages ADD COLUMN original_chat_id INTEGER DEFAULT NULL"))
            db.commit()

        #   custom_sounds  user_settings
        if 'user_settings' in tables:
            us_columns = [col['name'] for col in inspector.get_columns('user_settings')]
            if 'custom_sounds' not in us_columns:
                db.execute(text("ALTER TABLE user_settings ADD COLUMN custom_sounds TEXT DEFAULT '{}'"))
                db.commit()

        #   notifications
        if 'notifications' not in tables:
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    type VARCHAR(50) NOT NULL,
                    message TEXT NOT NULL,
                    from_user_id INTEGER DEFAULT NULL,
                    chat_id INTEGER DEFAULT NULL,
                    is_read BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (from_user_id) REFERENCES users(id),
                    FOREIGN KEY (chat_id) REFERENCES chats(id)
                )
            """))
            db.commit()
            logger.info("   notifications")
        
    except Exception as e:
        logger.error(f" : {e}")
    finally:
        db.close()

            # ============================================
            # Отправка сообщения
            # ============================================

def create_system_chats():
    """   (, Monogram)"""
    db = SessionLocal()
    try:
        #  "" (id = 999999)
        favorite = db.query(Chat).filter(Chat.id == 999999).first()
        if not favorite:
            favorite = Chat(
                id=999999,
                type="private",
                name="",
                description=" "
            )
            db.add(favorite)
            logger.info("   ''")
        
        #  "Monogram" (id = 999998)
        monogram = db.query(Chat).filter(Chat.id == 999998).first()
        if not monogram:
            monogram = Chat(
                id=999998,
                type="channel",
                name="Monogram",
                description=" "
            )
            db.add(monogram)
            logger.info("   'Monogram'")
            
            #   
            from models import Message, Admin
            user = db.query(User).filter(User.username == "Yar").first()
            if user:
                admin = db.query(Admin).filter(Admin.user_id == user.id).first()
                if not admin:
                    db.add(Admin(user_id=user.id))
                    logger.info("  Yar  ")
            
            db.commit()
            
            #  
            msgs = [
                "    Monogram! ",
                "    :      .",
                "      .",
                "   Telegram, WhatsApp  ...",
                "  QuarkPay    !",
                "   !",
            ]
            for text in msgs:
                db.add(Message(content=text, sender_id=1, chat_id=999998))
            db.commit()
            logger.info("    Monogram")
        
        db.commit()
    except Exception as e:
        logger.error(f"   : {e}")
    finally:
        db.close()

def ensure_admin():
    """Автоматическое назначение Yar/Yarik админом"""
    from models import Admin
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username.in_(['Yarik', 'Yar'])).first()
        if user:
            existing = db.query(Admin).filter(Admin.user_id == user.id).first()
            if not existing:
                db.add(Admin(user_id=user.id, added_by=user.id))
                db.commit()
                logger.info(f"{user.username} (id={user.id}) назначен админом")
            else:
                logger.info(f"{user.username} уже админ")
    except Exception as e:
        logger.error(f"Ошибка назначения админа: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    create_tables()
    apply_migrations()
    create_system_chats()
    ensure_admin()
    import uvicorn
    logger.info("=" * 50)
    logger.info(" ЗАПУСК MONOGRAM СЕРВЕРА")
    logger.info("=" * 50)
    logger.info(f" Путь: {backend_dir}")
    logger.info(f" API: https://monogram-backend-dxv4.onrender.com/")
    logger.info(f" Health: https://monogram-backend-dxv4.onrender.com/api/health")
    logger.info("=" * 50)
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )


