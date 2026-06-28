import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
from database import get_db
from models import User, Chat, Membership, Admin, Profile, BlacklistedToken, UserSession
from schemas import UserCreate, UserResponse, Token
from jose import jwt, JWTError
from datetime import datetime, timedelta
import os
import logging
from pydantic import BaseModel, field_validator
from passlib.context import CryptContext
from fastapi.security import OAuth2PasswordBearer
import secrets
import re
from config import settings

FRONTEND_URL = settings.FRONTEND_URL.rstrip('/')

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# ============================================
# КОНФИГУРАЦИЯ
# ============================================

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

JWT_COOKIE_SECURE = settings.JWT_COOKIE_SECURE
JWT_COOKIE_MAX_AGE = settings.JWT_COOKIE_MAX_AGE

# Rate limiting: 3 registrations per IP per day
_registration_ips: dict[str, list[datetime]] = {}
MAX_REGISTRATIONS_PER_DAY = 3

def generate_profile_id(db: Session) -> int:
    import random
    while True:
        pid = random.randint(100000, 999999)
        if not db.query(User).filter(User.profile_id == pid).first():
            return pid

def set_auth_cookie(response: Response, token: str):
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="strict",
        secure=JWT_COOKIE_SECURE,
        max_age=JWT_COOKIE_MAX_AGE,
        path="/"
    )

def clear_auth_cookie(response: Response):
    response.delete_cookie("access_token", path="/")

async def get_token(
    request: Request,
    authorization: str = Depends(oauth2_scheme)
) -> str:
    token = authorization
    if not token:
        token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=401, detail="Не авторизован")
    return token

# Хранилища (в памяти, для продакшена использовать Redis)
user_sessions = {}
user_activity_days = {}
email_verification_tokens = {}
qr_sessions = {}
password_reset_tokens = {}

# Email конфигурация
EMAIL_CONFIG = {
    "smtp_server": settings.SMTP_HOST,
    "smtp_port": settings.SMTP_PORT,
    "username": settings.SMTP_USER,
    "password": settings.SMTP_PASSWORD or ""
}

# ============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def validate_password_strength(password: str):
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Пароль должен содержать не менее 8 символов")
    if not re.search(r"[a-zA-Z]", password):
        raise HTTPException(status_code=400, detail="Пароль должен содержать хотя бы одну латинскую букву")
    if not re.search(r"\d", password):
        raise HTTPException(status_code=400, detail="Пароль должен содержать хотя бы одну цифру")

def get_password_hash(password: str) -> str:
    validate_password_strength(password)
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        if len(plain_password) > 72:
            plain_password = plain_password[:72]
        return pwd_context.verify(plain_password, hashed_password)
    except ValueError:
        return False

async def get_current_user(
    db: Session = Depends(get_db), 
    token: str = Depends(get_token)
):
    if not token:
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    blacklisted = db.query(BlacklistedToken).filter(BlacklistedToken.token == token).first()
    if blacklisted:
        if (datetime.utcnow() - blacklisted.created_at).days > 1:
            db.delete(blacklisted)
            db.commit()
        else:
            raise HTTPException(status_code=401, detail="Сессия завершена")
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("user_id") or payload.get("sub")
        if not user_id_str:
            raise HTTPException(status_code=401, detail="Неверный токен")
        user_id = int(user_id_str)
    except JWTError:
        raise HTTPException(status_code=401, detail="Неверный токен")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    
    return user

def send_email(to_email: str, subject: str, body: str):
    """Отправка email через Яндекс SMTP"""
    import smtplib
    from email.mime.text import MIMEText
    
    try:
        msg = MIMEText(body, 'html', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = EMAIL_CONFIG['username']
        msg['To'] = to_email
        
        with smtplib.SMTP_SSL(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port']) as server:
            server.login(EMAIL_CONFIG['username'], EMAIL_CONFIG['password'])
            server.send_message(msg)
        return True
    except Exception as e:
        logger.error(f"Email error: {e}")
        return False

def add_system_chats(user_id: int, db: Session):
    """Добавление системных чатов для нового пользователя"""
    # Избранное (id = 999999)
    favorite = db.query(Chat).filter(Chat.id == 999999).first()
    if not favorite:
        favorite = Chat(id=999999, type="private", name="Избранное", description="Сохранённые сообщения")
        db.add(favorite)
        db.flush()
    
    existing = db.query(Membership).filter(
        Membership.user_id == user_id, 
        Membership.chat_id == favorite.id
    ).first()
    if not existing:
        db.add(Membership(user_id=user_id, chat_id=favorite.id, role="owner"))
    
    # Канал Monogram (id = 999998)
    monogram = db.query(Chat).filter(Chat.id == 999998).first()
    if not monogram:
        monogram = Chat(id=999998, type='channel', name='Monogram', description='Новости мессенджера')
        db.add(monogram)
        db.flush()
    
    existing = db.query(Membership).filter(
        Membership.user_id == user_id, 
        Membership.chat_id == monogram.id
    ).first()
    if not existing:
        db.add(Membership(user_id=user_id, chat_id=monogram.id, role="member"))
    
    db.commit()

# ============================================
# PYDANTIC МОДЕЛИ
# ============================================

class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    first_name: str
    last_name: str = ""

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if len(v) < 3:
            raise ValueError("Имя пользователя должно содержать не менее 3 символов")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Пароль должен содержать не менее 8 символов")
        if len(v) > 72:
            raise ValueError("Пароль не может быть длиннее 72 символов")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Пароль должен содержать хотя бы одну заглавную букву")
        if not re.search(r"[a-z]", v):
            raise ValueError("Пароль должен содержать хотя бы одну строчную букву")
        if not re.search(r"\d", v):
            raise ValueError("Пароль должен содержать хотя бы одну цифру")
        if not re.search(r"[!@#$%^&*]", v):
            raise ValueError("Пароль должен содержать хотя бы один специальный символ (!@#$%^&*)")
        return v

class EmailVerificationRequest(BaseModel):
    email: str
    password: str
    first_name: str
    last_name: str = ""
    username: str = ""
    avatar_url: str = ""

class CompleteGoogleRegistrationRequest(BaseModel):
    username: str
    email: str
    google_id: str
    first_name: str
    last_name: str = ""
    bio: str = ""
    avatar_url: str = ""

# ============================================
# РЕГИСТРАЦИЯ С ПОДТВЕРЖДЕНИЕМ EMAIL
# ============================================

@router.post("/register/init", response_model=dict)
def register_init(data: EmailVerificationRequest, db: Session = Depends(get_db)):
    """Начало регистрации - отправка письма с подтверждением"""
    
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=400, detail="Этот email уже зарегистрирован")
    
    if data.username and db.query(User).filter(User.username == data.username).first():
        raise HTTPException(status_code=400, detail="Это имя пользователя уже занято")
    
    validate_password_strength(data.password)
    
    token = secrets.token_urlsafe(32)
    email_verification_tokens[token] = {
        "email": data.email,
        "password": get_password_hash(data.password),
        "first_name": data.first_name,
        "last_name": data.last_name,
        "avatar_url": data.avatar_url,
        "expires": datetime.utcnow() + timedelta(minutes=20)
    }
    
    verification_url = f"{FRONTEND_URL}/verify/{token}"
    
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Подтверждение регистрации Monogram</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                background-color: #f5f7fa;
                margin: 0;
                padding: 40px;
            }}
            .container {{
                max-width: 500px;
                margin: 0 auto;
                background: white;
                border-radius: 16px;
                padding: 40px;
                box-shadow: 0 4px 20px rgba(0,0,0,0.1);
                text-align: center;
            }}
            h1 {{ color: #667eea; margin-bottom: 20px; }}
            .btn {{
                display: inline-block;
                padding: 14px 28px;
                background: linear-gradient(135deg, #667eea, #764ba2);
                color: #ffffff !important;
                text-decoration: none;
                border-radius: 8px;
                margin: 20px 0;
                font-weight: bold;
            }}
            .footer {{
                color: #999;
                font-size: 12px;
                margin-top: 20px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Monogram</h1>
            <h2>Подтверждение регистрации</h2>
            <p>Перейдите по ссылке для подтверждения email:</p>
            <a href="{verification_url}" class="btn" style="color: white !important;">Подтвердить регистрацию</a>
            <p class="footer">Ссылка действительна 24 часа.<br>Если вы не регистрировались в Monogram, просто проигнорируйте это письмо.</p>
        </div>
    </body>
    </html>
    """
    
    success = send_email(data.email, "Подтверждение регистрации в Monogram", html_body)
    
    if not success:
        raise HTTPException(status_code=500, detail="Не удалось отправить письмо")
    
    return {"message": "Письмо с подтверждением отправлено"}

@router.get("/verify/{token}")
def verify_email(token: str, response: Response, db: Session = Depends(get_db)):
    """Подтверждение email — возвращает токен для шага 3"""
    
    verification = email_verification_tokens.get(token)
    if not verification:
        raise HTTPException(status_code=400, detail="Неверный или истекший токен")
    
    if verification["expires"] < datetime.utcnow():
        del email_verification_tokens[token]
        raise HTTPException(status_code=400, detail="Токен истёк. Запросите новое письмо.")
    
    verification_token = secrets.token_urlsafe(32)
    email_verification_tokens[verification_token] = {
        **verification,
        "email_verified": True,
        "expires": datetime.utcnow() + timedelta(minutes=20)
    }
    del email_verification_tokens[token]
    
    return {"verification_token": verification_token, "email_verified": True}

# ============================================
# ШАГ 3: ВЫБОР USERNAME
# ============================================

@router.post("/register/choose-username")
def choose_username(data: dict, response: Response, db: Session = Depends(get_db)):
    """Шаг 3: выбор username после подтверждения email"""
    
    verification_token = data.get("verification_token")
    username = data.get("username", "").strip()
    
    if not verification_token:
        raise HTTPException(status_code=400, detail="Токен верификации не указан")
    
    if not username or len(username) < 3 or len(username) > 32:
        raise HTTPException(status_code=400, detail="Username должен быть от 3 до 32 символов")
    
    if not re.match(r'^[a-zA-Z0-9_]+$', username):
        raise HTTPException(status_code=400, detail="Username может содержать только латиницу, цифры и _")
    
    verification = email_verification_tokens.get(verification_token)
    if not verification or not verification.get("email_verified"):
        raise HTTPException(status_code=400, detail="Неверный или истёкший токен верификации")
    
    if verification["expires"] < datetime.utcnow():
        del email_verification_tokens[verification_token]
        raise HTTPException(status_code=400, detail="Токен верификации истёк")
    
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Это имя пользователя уже занято")
    
    db_user = User(
        username=username,
        email=verification["email"],
        hashed_password=verification["password"],
        first_name=verification["first_name"],
        last_name=verification["last_name"],
        is_active=True,
        is_bot=False,
        avatar_url=verification.get("avatar_url", "") or None,
        profile_id=generate_profile_id(db)
    )
    db.add(db_user)
    db.flush()
    
    add_system_chats(db_user.id, db)
    
    db.commit()
    db.refresh(db_user)
    
    del email_verification_tokens[verification_token]
    
    jwt_token = create_access_token(data={"sub": str(db_user.id), "user_id": db_user.id})
    set_auth_cookie(response, jwt_token)
    
    return {"access_token": jwt_token, "token_type": "bearer", "user": {
        "id": db_user.id,
        "username": db_user.username,
        "email": db_user.email,
        "first_name": db_user.first_name,
    }}

# ============================================
# ПРОСТАЯ РЕГИСТРАЦИЯ (БЕЗ ПОДТВЕРЖДЕНИЯ)
# ============================================

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(user: RegisterRequest, response: Response, db: Session = Depends(get_db), request: Request = None):
    """Быстрая регистрация без подтверждения email"""
    
    # Rate limiting: 3 registrations per IP per day
    client_ip = request.client.host if request else "unknown"
    now = datetime.utcnow()
    if client_ip not in _registration_ips:
        _registration_ips[client_ip] = []
    _registration_ips[client_ip] = [t for t in _registration_ips[client_ip] if (now - t).total_seconds() < 86400]
    if len(_registration_ips[client_ip]) >= MAX_REGISTRATIONS_PER_DAY:
        raise HTTPException(status_code=429, detail="Превышен лимит регистраций. Попробуйте завтра.")
    _registration_ips[client_ip].append(now)
    
    # Валидация имени пользователя
    username = user.username.strip().lower()
    if len(username) < 3 or len(username) > 32:
        raise HTTPException(status_code=400, detail="Имя пользователя должно быть от 3 до 32 символов")
    if not re.match(r'^[a-z0-9_]+$', username):
        raise HTTPException(status_code=400, detail="Имя пользователя может содержать только латинские буквы, цифры и подчёркивание")
    
    reserved = ['admin', 'moderator', 'support', 'bot', 'system', 'config', 'connect', 'api',
                'help', 'settings', 'premium', 'pay', 'transfer', 'monogram', 'quarkpay',
                'null', 'undefined', 'true', 'false', 'test', 'root', 'superuser', 'user',
                'owner', 'creator', 'server', 'official', 'team', 'staff', 'dev', 'developers',
                'welcome', 'start', 'home', 'main', 'index', 'login', 'register', 'signup',
                'dashboard', 'profile', 'account', 'wallet', 'bank', 'money', 'payment',
                'chat', 'message', 'group', 'channel', 'contact', 'friend', 'family',
                'chats', 'users', 'messages', 'admin', 'bots', 'e2ee', 'stickers', 'search',
                'calls', 'drafts', 'gamification', 'ai', 'stories', 'archive', 'saved',
                'folders', 'notifications', 'google', 'yandex', 'oauth', 'callback',
                'verify', 'reset', 'install', 'share', 'assets', 'public', 'static']
    if username in reserved:
        raise HTTPException(status_code=400, detail="Это имя пользователя зарезервировано")
    
    offensive = ['nigga', 'nigger', 'niger', 'faggot', 'fag', 'retard', 'retarded',
                 'pidor', 'pidr', 'pider', 'pedor',
                 'moron', 'stupid', 'idiot', 'dick', 'cock',
                 'pussy', 'fuck', 'fucker', 'fucking', 'shit', 'shitty', 'damn',
                 'slut', 'whore', 'bitch', 'bastard', 'asshole', 'dickhead',
                 'durak', 'duren', 'suka', 'huy', 'pizda', 'blyad', 'yob',
                 'ebat', 'pidar', 'mudak', 'urod', 'dolboyob', 'ublyudok']
    for word in offensive:
        if word in username:
            raise HTTPException(status_code=400, detail="Это имя пользователя содержит запрещённые слова")
    
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Это имя пользователя уже занято")
    if db.query(User).filter(User.email == user.email).first():
        raise HTTPException(status_code=400, detail="Этот email уже зарегистрирован")
    
    db_user = User(
        username=user.username,
        email=user.email,
        hashed_password=get_password_hash(user.password),
        first_name=user.first_name,
        last_name=user.last_name or "",
        is_active=True,
        is_bot=False,
        profile_id=generate_profile_id(db)
    )
    db.add(db_user)
    db.flush()
    
    add_system_chats(db_user.id, db)
    
    db.commit()
    db.refresh(db_user)

    token = create_access_token(data={"sub": str(db_user.id), "user_id": db_user.id})
    set_auth_cookie(response, token)

    return {"access_token": token, "token_type": "bearer", "user": {
        "id": db_user.id,
        "username": db_user.username,
        "email": db_user.email,
        "first_name": db_user.first_name,
        "last_name": db_user.last_name,
        "avatar_url": db_user.avatar_url,
    }}

# ============================================
# CSRF TOKEN
# ============================================

@router.get("/csrf-token")
def get_csrf_token(response: Response):
    token = secrets.token_hex(32)
    response.set_cookie(key="csrf_token", value=token, httponly=False, samesite="strict", secure=JWT_COOKIE_SECURE)
    return {"csrf_token": token}

# ============================================
# ЛОГИН И ЛОГАУТ
# ============================================

@router.post("/login")
def login(user: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if not db_user or not verify_password(user.password, db_user.hashed_password):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    
    db_user.last_login = datetime.utcnow()
    db.commit()
    
    # Создаём сессию
    session_id = secrets.token_urlsafe(16)
    device_info = get_device_info(request)
    
    if db_user.id not in user_sessions:
        user_sessions[db_user.id] = []
    
    # Добавляем сессию только если её нет
    existing = False
    for s in user_sessions[db_user.id]:
        if s.get("is_current"):
            existing = True
            break
    
    if not existing:
        user_sessions[db_user.id].append({
            "id": session_id,
            "device": device_info["device"],
            "browser": device_info["browser"],
            "os": device_info["os"],
            "ip": device_info["ip"],
            "location": "Unknown",
            "lastActive": datetime.utcnow().isoformat(),
            "created_at": datetime.utcnow().isoformat(),
            "is_current": True,
            "is_new": False
        })
    
    token = create_access_token(data={"sub": str(db_user.id), "user_id": db_user.id})
    set_auth_cookie(response, token)
    return {"access_token": token, "token_type": "bearer", "status": "ok"}

@router.post("/refresh")
def refresh_token(response: Response, token: str = Depends(get_token), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("user_id") or payload.get("sub")
        if not user_id_str:
            raise HTTPException(status_code=401, detail="Неверный токен")
        user_id = int(user_id_str)
    except JWTError:
        raise HTTPException(status_code=401, detail="Токен истёк или неверен")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    
    new_token = create_access_token(data={"sub": str(user.id), "user_id": user.id})
    set_auth_cookie(response, new_token)
    return {"access_token": new_token, "token_type": "bearer"}

@router.post("/logout")
def logout(response: Response, token: str = Depends(get_token), db: Session = Depends(get_db)):
    db.add(BlacklistedToken(token=token))
    db.commit()
    cutoff = datetime.utcnow() - timedelta(days=1)
    db.query(BlacklistedToken).filter(BlacklistedToken.created_at < cutoff).delete()
    db.commit()
    clear_auth_cookie(response)
    return {"message": "Выход выполнен"}

# ============================================
# ПОЛЬЗОВАТЕЛЬ
# ============================================

@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user

@router.get("/check-username")
def check_username(username: str, db: Session = Depends(get_db)):
    username = username.strip().lower()
    reserved = ['admin', 'moderator', 'support', 'bot', 'system', 'config', 'connect', 'api',
                'help', 'settings', 'premium', 'pay', 'transfer', 'monogram', 'quarkpay',
                'null', 'undefined', 'true', 'false', 'test', 'root', 'superuser']
    if username in reserved:
        return {"exists": True, "reason": "reserved"}
    offensive = ['niger', 'nigga', 'nigger', 'faggot', 'fag', ' retard', 'retard',
                 'хуй', 'пизда', 'блять', 'блядь', 'сука', 'ёб', 'еба', 'ебл',
                 'pidor', 'pidr', 'durak', 'moron', 'fuck', 'shit', 'damn',
                 'хуесос', 'пидор', 'даун', 'мудак', 'урод', 'ублюдок']
    for word in offensive:
        if word in username:
            return {"exists": True, "reason": "offensive"}
    exists = db.query(User).filter(User.username == username).first() is not None
    return {"exists": exists}

@router.get("/admin/check")
def check_admin_status(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    admin = db.query(Admin).filter(Admin.user_id == current_user.id).first()
    return {"is_admin": admin is not None}

# ============================================
# GOOGLE AUTH
# ============================================

@router.post("/google")
async def google_login(request: Request, db: Session = Depends(get_db)):
    """Login via Google access_token (from @react-oauth/google)"""
    body = await request.json()
    access_token = body.get("access_token")
    if not access_token:
        raise HTTPException(400, "access_token required")

    async with httpx.AsyncClient(timeout=15.0) as client:
        user_res = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        if user_res.status_code != 200:
            raise HTTPException(401, "Invalid Google token")
        user_data = user_res.json()

    email = user_data.get("email")
    google_id = user_data.get("id")
    if not email:
        raise HTTPException(400, "Email not found in Google profile")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        username = user_data.get("email", "").split("@")[0]
        if db.query(User).filter(User.username == username).first():
            username = username + "_" + str(google_id)
        user = User(
            username=username,
            email=email,
            first_name=user_data.get("given_name", ""),
            last_name=user_data.get("family_name", ""),
            hashed_password=f"google_{google_id}",
            is_active=True,
            is_bot=False,
            avatar_url=user_data.get("picture"),
            profile_id=generate_profile_id(db),
        )
        db.add(user)
        db.flush()
        add_system_chats(user.id, db)
        db.commit()
        db.refresh(user)

    jwt_token = create_access_token(data={"sub": str(user.id), "user_id": user.id})
    return {"access_token": jwt_token, "token_type": "bearer"}

@router.get("/google/callback")
async def google_callback(code: str, request: Request, db: Session = Depends(get_db)):
    base_url = FRONTEND_URL
    
    token_url = "https://oauth2.googleapis.com/token"
    token_data = {
        "code": code,
        "client_id": os.getenv("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ""),
        "redirect_uri": f"{os.getenv('BACKEND_PUBLIC_URL', 'https://f1w6ggb2-8000.euw.devtunnels.ms').rstrip('/')}/auth/google/callback",
        "grant_type": "authorization_code"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            token_res = await client.post(token_url, data=token_data)
            
            if token_res.status_code != 200:
                logger.info(f"Token error: {token_res.text}")
                return RedirectResponse(url=f"{base_url}?error=google_token")
            
            token_json = token_res.json()
            access_token = token_json.get("access_token")
            
            user_res = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            
            if user_res.status_code != 200:
                return RedirectResponse(url=f"{base_url}?error=google_userinfo")
            
            user_data = user_res.json()
            email = user_data.get("email")
            google_id = user_data.get("id")
            
            user = db.query(User).filter(User.email == email).first()
            
            if user:
                jwt_token = create_access_token(data={"sub": str(user.id), "user_id": user.id})
                response = RedirectResponse(url=f"{base_url}/google-success?token={jwt_token}")
                response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
                set_auth_cookie(response, jwt_token)
                return response
            else:
                params = f"?email={email}&google_id={google_id}&first_name={user_data.get('given_name', '')}&last_name={user_data.get('family_name', '')}&avatar={user_data.get('picture', '')}"
                response = RedirectResponse(url=f"{base_url}/google-register{params}")
                response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
                return response
                
    except Exception as e:
        logger.error(f"Google callback error: {e}")
        return RedirectResponse(url=f"{base_url}?error=google_exception")

@router.post("/google/complete", response_model=Token)
def complete_google_registration(data: CompleteGoogleRegistrationRequest, db: Session = Depends(get_db)):
    """Завершение регистрации через Google"""
    
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(status_code=400, detail="Имя пользователя уже занято")
    
    user = db.query(User).filter(User.email == data.email).first()
    
    if not user:
        user = User(
            username=data.username,
            email=data.email,
            first_name=data.first_name,
            last_name=data.last_name,
            hashed_password=f"google_{data.google_id}",
            is_active=True,
            is_bot=False,
            avatar_url=data.avatar_url if data.avatar_url else None,
            profile_id=generate_profile_id(db)
        )
        db.add(user)
        db.flush()
        
        # Добавляем bio
        if data.bio:
            profile = db.query(Profile).filter(Profile.user_id == user.id).first()
            if not profile:
                profile = Profile(user_id=user.id)
                db.add(profile)
            profile.bio = data.bio
        
        add_system_chats(user.id, db)
        db.commit()
        db.refresh(user)
        logger.info(f"Updated user via Google: {user.username}")
    else:
        user.username = data.username
        user.first_name = data.first_name
        user.last_name = data.last_name
        if data.avatar_url:
            user.avatar_url = data.avatar_url
        db.commit()
        logger.info(f"Updated user via Google: {user.username}")
    
    jwt_token = create_access_token(data={"sub": str(user.id), "user_id": user.id})
    response = JSONResponse({"access_token": jwt_token, "token_type": "bearer"})
    set_auth_cookie(response, jwt_token)
    return response

# ============================================
# YANDEX AUTH
# ============================================

@router.get("/yandex")
async def yandex_login(request: Request):
    """Redirect to Yandex OAuth"""
    yandex_client_id = os.getenv("YANDEX_CLIENT_ID", "")
    if not yandex_client_id:
        raise HTTPException(500, "YANDEX_CLIENT_ID not configured")
    backend_url = os.getenv('BACKEND_PUBLIC_URL', 'https://f1w6ggb2-8000.euw.devtunnels.ms').rstrip('/')
    callback_url = f"{backend_url}/auth/yandex/callback"
    yandex_url = f"https://oauth.yandex.ru/authorize?response_type=code&client_id={yandex_client_id}&redirect_uri={callback_url}"
    return RedirectResponse(url=yandex_url)

@router.get("/yandex/callback")
async def yandex_callback(code: str, request: Request, db: Session = Depends(get_db)):
    logger.info("Yandex callback called")
    
    base_url = FRONTEND_URL
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        token_res = await client.post("https://oauth.yandex.ru/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": os.getenv("YANDEX_CLIENT_ID", ""),
            "client_secret": os.getenv("YANDEX_CLIENT_SECRET", ""),
        })

        if token_res.status_code != 200:
            return RedirectResponse(url=f"{base_url}?error=yandex_token")

        yandex_token = token_res.json().get("access_token")
        user_res = await client.get("https://login.yandex.ru/info", headers={"Authorization": f"OAuth {yandex_token}"})

        if user_res.status_code != 200:
            return RedirectResponse(url=f"{base_url}?error=yandex_userinfo")

        data = user_res.json()
        email = data.get("default_email", "")

    user = db.query(User).filter(User.email == email).first()

    if not user:
        username = data.get("login", "yandex_" + str(data.get("id")))
        if db.query(User).filter(User.username == username).first():
            username = username + "_" + str(data.get("id"))

        user = User(
            username=username,
            email=email,
            first_name=data.get("first_name", ""),
            last_name=data.get("last_name", ""),
            hashed_password="yandex_oauth",
            avatar_url=f"https://avatars.yandex.net/get-yapic/{data.get('default_avatar_id', '')}/islands-200" if data.get('default_avatar_id') else None,
            profile_id=generate_profile_id(db)
        )
        db.add(user)
        db.flush()

        add_system_chats(user.id, db)
        db.commit()
        db.refresh(user)

    jwt_token = create_access_token(data={"sub": str(user.id), "user_id": user.id})
    response = RedirectResponse(url=f"{base_url}/ya-success?token={jwt_token}")
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    set_auth_cookie(response, jwt_token)
    return response

# ============================================
# УПРАВЛЕНИЕ СЕССИЯМИ
# ============================================

@router.get("/sessions")
def get_sessions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    sessions = user_sessions.get(current_user.id, [])
    
    # Убираем кнопку завершения для текущей сессии
    for s in sessions:
        s["can_terminate"] = not s.get("is_current", False)
    
    return sessions

@router.delete("/sessions/{session_id}")
def terminate_session(session_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    sessions = user_sessions.get(current_user.id, [])
    user_sessions[current_user.id] = [s for s in sessions if s.get("id") != session_id]
    return {"status": "ok"}

@router.delete("/sessions/all")
def terminate_all_sessions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Убираем проверку на дни активности
    sessions = user_sessions.get(current_user.id, [])
    user_sessions[current_user.id] = [s for s in sessions if s.get("is_current")]
    return {"status": "ok"}

@router.get("/user-active-days")
def get_user_active_days(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    days = user_activity_days.get(current_user.id, 0)
    return {"days": days}

@router.get("/sessions/check-new")
def check_new_device(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    sessions = user_sessions.get(current_user.id, [])
    new_device = any(s.get("is_new", False) for s in sessions)
    days = user_activity_days.get(current_user.id, 0)
    return {
        "new_device": new_device,
        "user_active_days": days,
        "device": "Unknown",
        "location": "Unknown",
        "ip": "0.0.0.0",
        "time": datetime.utcnow().isoformat(),
        "session_id": "new_session"
    }

@router.post("/sessions/confirm")
def confirm_device(session_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    sessions = user_sessions.get(current_user.id, [])
    for s in sessions:
        if s.get("id") == session_id:
            s["is_new"] = False
    return {"status": "ok"}

# ============================================
# QR ЛОГИН
# ============================================

@router.post("/qr/create")
def create_qr_session():
    session_id = secrets.token_urlsafe(16)
    expires_at = datetime.utcnow() + timedelta(minutes=5)
    
    qr_sessions[session_id] = {
        "status": "waiting",
        "expires_at": expires_at,
        "user_id": None,
        "device_name": None
    }
    
    qr_link = f"{FRONTEND_URL}/qr/register/{session_id}"
    return {"session_id": session_id, "qr_link": qr_link}

@router.get("/qr/status/{session_id}")
def get_qr_status(session_id: str):
    session = qr_sessions.get(session_id)
    if not session:
        return {"status": "expired"}
    if datetime.utcnow() > session["expires_at"]:
        session["status"] = "expired"
    return {"status": session["status"], "user_id": session.get("user_id")}

@router.post("/qr/confirm")
def confirm_qr_login(session_id: str, device_name: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    session = qr_sessions.get(session_id)
    if not session or datetime.utcnow() > session["expires_at"]:
        raise HTTPException(400, "Сессия устарела")
    
    session["status"] = "confirmed"
    session["user_id"] = current_user.id
    session["device_name"] = device_name
    
    # Добавляем новое устройство в список сессий
    if current_user.id not in user_sessions:
        user_sessions[current_user.id] = []
    
    # Проверяем, нет ли уже такого устройства
    existing = False
    for s in user_sessions[current_user.id]:
        if s.get("device") == device_name:
            existing = True
            break
    
    if not existing:
        user_sessions[current_user.id].append({
            "id": session_id,
            "device": device_name,
            "browser": "Mobile",
            "os": "Mobile",
            "ip": "0.0.0.0",
            "location": "Unknown",
            "lastActive": datetime.utcnow().isoformat(),
            "created_at": datetime.utcnow().isoformat(),
            "is_current": False,
            "is_new": True
        })
    
    token = create_access_token(data={"sub": str(current_user.id), "user_id": current_user.id})
    return {"status": "ok", "token": token}

@router.post("/register/set-username")
def set_username(data: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    username = data.get("username", "").strip()
    if len(username) < 3:
        raise HTTPException(status_code=400, detail="Имя пользователя должно содержать не менее 3 символов")
    if db.query(User).filter(User.username == username, User.id != current_user.id).first():
        raise HTTPException(status_code=400, detail="Это имя пользователя уже занято")
    current_user.username = username
    db.commit()
    return {"status": "ok", "username": username}


# ============================================
# 2FA
# ============================================

@router.post("/2fa/enable")
def enable_2fa(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    import pyotp
    import json
    secret = pyotp.random_base32()
    current_user.totp_secret = secret
    current_user.is_2fa_enabled = True

    backup_codes = [secrets.token_hex(5).upper() for _ in range(8)]
    current_user.backup_codes = json.dumps(backup_codes)

    db.commit()
    return {
        "secret": secret,
        "uri": pyotp.totp.TOTP(secret).provisioning_uri(name=current_user.email, issuer_name="Monogram"),
        "backup_codes": backup_codes
    }

@router.post("/2fa/verify")
def verify_2fa(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    import pyotp
    code = data.get("code", "")
    if not current_user.totp_secret:
        raise HTTPException(status_code=400, detail="2FA не настроен")
    totp = pyotp.TOTP(current_user.totp_secret)
    if totp.verify(code):
        return {"status": "ok"}
    raise HTTPException(status_code=400, detail="Неверный код")

@router.post("/2fa/disable")
def disable_2fa(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    current_user.totp_secret = None
    current_user.is_2fa_enabled = False
    current_user.backup_codes = None
    db.commit()
    return {"status": "ok"}

# ============================================
# СМЕНА ПАРОЛЯ / EMAIL
# ============================================

@router.post("/change-password")
def change_password(
    data: dict,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    token: str = Depends(get_token)
):
    old_password = data.get("old_password", "")
    new_password = data.get("new_password", "")
    if not verify_password(old_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Неверный текущий пароль")
    validate_password_strength(new_password)
    current_user.hashed_password = get_password_hash(new_password)

    db.add(BlacklistedToken(token=token))

    db.query(UserSession).filter(UserSession.user_id == current_user.id, UserSession.is_current == False).delete()

    db.commit()

    new_token = create_access_token(data={"sub": str(current_user.id), "user_id": current_user.id})
    set_auth_cookie(response, new_token)
    return {"access_token": new_token, "token_type": "bearer", "status": "ok"}

@router.post("/change-email")
def change_email(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    new_email = data.get("email", "").strip()
    if not new_email or "@" not in new_email:
        raise HTTPException(status_code=400, detail="Неверный email")
    if db.query(User).filter(User.email == new_email, User.id != current_user.id).first():
        raise HTTPException(status_code=400, detail="Email уже используется")
    current_user.email = new_email
    db.commit()
    return {"status": "ok"}

@router.post("/qr/confirm-mobile")
def confirm_qr_login_mobile(session_id: str, token: str, db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("user_id") or payload.get("sub")
        if not user_id_str:
            raise HTTPException(status_code=401, detail="Неверный токен")
        user_id = int(user_id_str)
    except JWTError:
        raise HTTPException(401, "Неверный токен")
    
    session = qr_sessions.get(session_id)
    if not session or datetime.utcnow() > session["expires_at"]:
        raise HTTPException(400, "Сессия устарела")
    
    session["status"] = "confirmed"
    session["user_id"] = user_id
    
    new_token = create_access_token(data={"sub": str(user_id), "user_id": user_id})
    return {"status": "ok", "token": new_token}

# ============================================
# ВОССТАНОВЛЕНИЕ ПАРОЛЯ
# ============================================

@router.post("/reset-password")
def reset_password(email: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    
    if not user:
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Попытка восстановления пароля Monogram</title>
            <style>
                body {{ font-family: Arial, sans-serif; background: #f5f7fa; margin: 0; padding: 40px; }}
                .container {{ max-width: 500px; margin: 0 auto; background: white; border-radius: 16px; padding: 40px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); text-align: center; }}
                h1 {{ color: #ef4444; }}
                .warning {{ background: #fef3cd; border: 1px solid #ffc107; border-radius: 8px; padding: 16px; margin: 20px 0; text-align: left; font-size: 0.9rem; color: #856404; }}
                .hint {{ color: #999; font-size: 0.85rem; margin-top: 20px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>⚠️ Попытка восстановления пароля</h1>
                <p>Кто-то попытался использовать ваш email <strong>{email}</strong> для восстановления пароля в Monogram.</p>
                <div class="warning">
                    <strong>Важно:</strong> К этой почте не привязан аккаунт Monogram. Если это были не вы — просто проигнорируйте это письмо. Никаких действий предпринимать не нужно.
                </div>
                <p class="hint">Если вы не запрашивали восстановление пароля — проигнорируйте это письмо.</p>
            </div>
        </body>
        </html>
        """
        send_email(email, "Попытка восстановления пароля Monogram", html_body)
        return {"message": "На вашей почте не привязан аккаунт Monogram. Если это были не вы — проигнорируйте это письмо."}
    
    reset_token = secrets.token_urlsafe(32)
    password_reset_tokens[reset_token] = {
        "user_id": user.id,
        "expires": datetime.utcnow() + timedelta(hours=1)
    }
    
    reset_url = f"{FRONTEND_URL}/reset-password?token={reset_token}"
    
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Восстановление пароля Monogram</title>
        <style>
            body {{ font-family: Arial, sans-serif; background: #f5f7fa; margin: 0; padding: 40px; }}
            .container {{ max-width: 500px; margin: 0 auto; background: white; border-radius: 16px; padding: 40px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); text-align: center; }}
            h1 {{ color: #667eea; }}
            .btn {{ display: inline-block; padding: 14px 28px; background: linear-gradient(135deg, #667eea, #764ba2); color: white; text-decoration: none; border-radius: 12px; font-weight: 600; }}
            .account-info {{ background: #f8f9fa; border-radius: 12px; padding: 16px; margin: 20px 0; text-align: left; font-size: 0.9rem; }}
            .account-info p {{ margin: 6px 0; }}
            .hint {{ color: #999; font-size: 0.85rem; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Восстановление пароля</h1>
            <p>Запрос на смену пароля для вашего аккаунта:</p>
            
            <div class="account-info">
                <p><strong>Имя пользователя:</strong> @{user.username}</p>
                <p><strong>Имя:</strong> {user.first_name or 'Не указано'} {user.last_name or ''}</p>
                <p><strong>Email:</strong> {user.email}</p>
                <p><strong>Дата регистрации:</strong> {user.created_at.strftime('%d.%m.%Y') if user.created_at else 'Неизвестно'}</p>
                <p><strong>Последний вход:</strong> {user.last_login.strftime('%d.%m.%Y %H:%M') if user.last_login else 'Неизвестно'}</p>
            </div>
            
            <a href="{reset_url}" class="btn">Сменить пароль</a>
            <p class="hint">Ссылка действительна 1 час. Если вы не запрашивали сброс пароля — проигнорируйте это письмо.</p>
        </div>
    </body>
    </html>
    """
    
    send_email(email, "Восстановление пароля Monogram", html_body)
    return {"message": "Письмо отправлено"}

@router.post("/reset-password/confirm")
def confirm_reset_password(token: str, new_password: str, db: Session = Depends(get_db)):
    reset_data = password_reset_tokens.get(token)
    if not reset_data:
        raise HTTPException(status_code=400, detail="Неверный или истекший токен")
    if reset_data["expires"] < datetime.utcnow():
        del password_reset_tokens[token]
        raise HTTPException(status_code=400, detail="Токен истек")
    validate_password_strength(new_password)
    
    user = db.query(User).filter(User.id == reset_data["user_id"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    user.hashed_password = pwd_context.hash(new_password)
    db.commit()
    del password_reset_tokens[token]
    return {"message": "Пароль успешно изменён"}


def get_device_info(request: Request) -> dict:
    """Определяет информацию об устройстве из заголовков"""
    user_agent = request.headers.get("user-agent", "Unknown")
    
    # Простое определение
    if "iPhone" in user_agent:
        device = "iPhone"
        os = "iOS"
    elif "Android" in user_agent:
        device = "Android Phone"
        os = "Android"
    elif "Windows" in user_agent:
        device = "Windows PC"
        os = "Windows"
    elif "Mac" in user_agent:
        device = "Mac"
        os = "macOS"
    else:
        device = "Unknown Device"
        os = "Unknown"
    
    # Определяем браузер
    if "Chrome" in user_agent and "Edg" not in user_agent:
        browser = "Chrome"
    elif "Firefox" in user_agent:
        browser = "Firefox"
    elif "Safari" in user_agent and "Chrome" not in user_agent:
        browser = "Safari"
    elif "Edg" in user_agent:
        browser = "Edge"
    elif "Yandex" in user_agent:
        browser = "Yandex"
    else:
        browser = "Unknown"
    
    return {
        "device": device,
        "browser": browser,
        "os": os,
        "ip": request.client.host if request.client else "127.0.0.1"
    }

# ============================================
# 2FA — EMAIL КОД
# ============================================

two_fa_codes: dict[str, dict] = {}

@router.post("/2fa/send-code")
def send_2fa_code(current_user: User = Depends(get_current_user)):
    """Отправка кода 2FA на email"""
    code = f"{secrets.randbelow(900000) + 100000}"
    two_fa_codes[str(current_user.id)] = {
        "code": code,
        "expires": datetime.utcnow() + timedelta(minutes=10),
        "attempts": 0,
    }
    
    html_body = f"""
    <html>
    <body style="font-family: Arial; padding: 40px; text-align: center;">
        <h1 style="color: #667eea;">Код подтверждения</h1>
        <p>Ваш код для входа:</p>
        <div style="font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #333; margin: 20px 0;">{code}</div>
        <p style="color: #999; font-size: 12px;">Код действителен 10 минут</p>
    </body>
    </html>
    """
    send_email(current_user.email, "Код подтверждения Monogram", html_body)
    return {"message": "Код отправлен"}

@router.post("/2fa/verify-code")
def verify_2fa_code(data: dict, current_user: User = Depends(get_current_user)):
    """Проверка кода 2FA"""
    code = data.get("code", "")
    stored = two_fa_codes.get(str(current_user.id))
    
    if not stored:
        raise HTTPException(status_code=400, detail="Код не отправлен")
    
    if stored["expires"] < datetime.utcnow():
        del two_fa_codes[str(current_user.id)]
        raise HTTPException(status_code=400, detail="Код истёк")
    
    if stored["attempts"] >= 5:
        del two_fa_codes[str(current_user.id)]
        raise HTTPException(status_code=429, detail="Превышен лимит попыток")
    
    stored["attempts"] += 1
    
    if stored["code"] != code:
        raise HTTPException(status_code=400, detail="Неверный код")
    
    del two_fa_codes[str(current_user.id)]
    return {"status": "verified"}
