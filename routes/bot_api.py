import json, os, secrets, asyncio, logging, mimetypes
import httpx
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Bot, BotWebhook, BotCommand, User, Message, Chat, Membership, Poll, PollVote, Sticker

router = APIRouter(prefix="/bots/api", tags=["bot_api"])
logger = logging.getLogger(__name__)

UPLOAD_DIR = "uploads/bot_files"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# =====================================================================
# HELPERS
# =====================================================================

def tg_error(code: int, desc: str):
    return JSONResponse(status_code=code, content={"ok": False, "error_code": code, "description": desc})

def tg_success(data):
    return JSONResponse(content={"ok": True, "result": data})

async def get_bot(request: Request, db: Session):
    api_key = request.headers.get("X-Api-Key")
    if not api_key:
        return None, tg_error(401, "Unauthorized")
    bot = db.query(Bot).filter(Bot.api_key == api_key, Bot.is_active == True).first()
    if not bot:
        return None, tg_error(401, "Invalid API Key")
    return bot, None

async def get_body(request: Request) -> dict:
    ct = request.headers.get("content-type", "")
    if "application/json" in ct:
        return await request.json()
    form = await request.form()
    return {k: v for k, v in form.items()}

def tg_user(user):
    return {
        "id": user.id,
        "is_bot": getattr(user, "is_bot", False),
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "username": user.username,
        "profile_id": user.profile_id,
        "language_code": "en",
    }

def tg_chat(chat):
    d = {"id": chat.id, "type": chat.type}
    if chat.name:
        d["title"] = chat.name
    if chat.description:
        d["description"] = chat.description
    return d

def tg_message(msg, sender=None, chat=None):
    d = {
        "message_id": msg.id,
        "date": int(msg.timestamp.timestamp()) if msg.timestamp else 0,
        "text": msg.content,
    }
    if sender:
        d["from"] = tg_user(sender)
    else:
        d["from"] = {"id": 0, "is_bot": False, "first_name": "Unknown"}
    if chat:
        d["chat"] = tg_chat(chat)
    else:
        d["chat"] = {"id": msg.chat_id, "type": "private"}
    if msg.is_forwarded and msg.original_sender:
        d["forward_from"] = {"id": msg.original_sender, "is_bot": False}
    if msg.reply_to_id:
        d["reply_to_message"] = {"message_id": msg.reply_to_id}
    return d

def bot_send_message(db: Session, bot: Bot, chat_id: int, content: str, reply_to_id: int = None, disable_notification: bool = False):
    msg = Message(
        content=content,
        sender_id=bot.owner_id,
        bot_id=bot.id,
        chat_id=chat_id,
        reply_to_id=reply_to_id,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    _notify_ws(chat_id, msg, bot)
    return msg

def _notify_ws(chat_id: int, msg: Message, bot: Bot):
    try:
        from main import ws_manager
        import asyncio
        asyncio.ensure_future(ws_manager.send_to_chat(chat_id, {
            "type": "new_message",
            "message_id": msg.id,
            "sender_id": bot.owner_id,
            "chat_id": chat_id,
            "content": msg.content,
            "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
            "bot_id": bot.id,
        }))
    except Exception as e:
        logger.warning(f"WS notify failed: {e}")

def check_membership(db: Session, user_id: int, chat_id: int):
    return db.query(Membership).filter(
        Membership.user_id == user_id, Membership.chat_id == chat_id
    ).first()

async def forward_webhook(db: Session, bot: Bot, update: dict):
    wh = db.query(BotWebhook).filter(BotWebhook.bot_id == bot.id).first()
    if not wh:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(wh.url, json=update)
            if resp.status_code >= 400:
                wh.last_error_date = datetime.utcnow()
                wh.last_error_message = f"HTTP {resp.status_code}: {resp.text[:200]}"
                db.commit()
    except Exception as e:
        wh.last_error_date = datetime.utcnow()
        wh.last_error_message = str(e)[:200]
        db.commit()

# =====================================================================
# CORE MESSAGE METHODS
# =====================================================================

@router.post("/sendMessage")
async def api_sendMessage(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    text = body.get("text")
    if not chat_id or not text:
        return tg_error(400, "Bad Request: chat_id and text are required")
    chat_id = int(chat_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found or bot not in chat")
    msg = bot_send_message(db, bot, chat_id, text, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    update = {"update_id": msg.id, "message": tg_message(msg, sender, chat)}
    await forward_webhook(db, bot, update)
    return tg_success(tg_message(msg, sender, chat))

@router.post("/forwardMessage")
async def api_forwardMessage(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    from_chat_id = body.get("from_chat_id")
    message_id = body.get("message_id")
    if not chat_id or not from_chat_id or not message_id:
        return tg_error(400, "Bad Request: chat_id, from_chat_id, message_id required")
    chat_id, from_chat_id, message_id = int(chat_id), int(from_chat_id), int(message_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    original = db.query(Message).filter(Message.id == message_id, Message.chat_id == from_chat_id).first()
    if not original:
        return tg_error(400, "Bad Request: message not found")
    msg = Message(
        content=original.content,
        sender_id=bot.owner_id,
        chat_id=chat_id,
        bot_id=bot.id,
        is_forwarded=True,
        original_sender=original.sender_id,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    _notify_ws(chat_id, msg, bot)
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/copyMessage")
async def api_copyMessage(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    from_chat_id = body.get("from_chat_id")
    message_id = body.get("message_id")
    if not chat_id or not from_chat_id or not message_id:
        return tg_error(400, "Bad Request: chat_id, from_chat_id, message_id required")
    chat_id, from_chat_id, message_id = int(chat_id), int(from_chat_id), int(message_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    original = db.query(Message).filter(Message.id == message_id, Message.chat_id == from_chat_id).first()
    if not original:
        return tg_error(400, "Bad Request: message not found")
    msg = bot_send_message(db, bot, chat_id, original.content, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/sendPhoto")
async def api_sendPhoto(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    photo = body.get("photo")
    caption = body.get("caption", "")
    if not chat_id or not photo:
        return tg_error(400, "Bad Request: chat_id and photo required")
    chat_id = int(chat_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    content = json.dumps({"type": "photo", "file": str(photo), "caption": caption})
    msg = bot_send_message(db, bot, chat_id, content, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/sendAudio")
async def api_sendAudio(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    audio = body.get("audio")
    if not chat_id or not audio:
        return tg_error(400, "Bad Request: chat_id and audio required")
    chat_id = int(chat_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    content = json.dumps({"type": "audio", "file": str(audio), "caption": body.get("caption", "")})
    msg = bot_send_message(db, bot, chat_id, content, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/sendDocument")
async def api_sendDocument(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    document = body.get("document")
    if not chat_id or not document:
        return tg_error(400, "Bad Request: chat_id and document required")
    chat_id = int(chat_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    content = json.dumps({"type": "document", "file": str(document), "caption": body.get("caption", "")})
    msg = bot_send_message(db, bot, chat_id, content, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/sendVideo")
async def api_sendVideo(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    video = body.get("video")
    if not chat_id or not video:
        return tg_error(400, "Bad Request: chat_id and video required")
    chat_id = int(chat_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    content = json.dumps({"type": "video", "file": str(video), "caption": body.get("caption", "")})
    msg = bot_send_message(db, bot, chat_id, content, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/sendAnimation")
async def api_sendAnimation(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    animation = body.get("animation")
    if not chat_id or not animation:
        return tg_error(400, "Bad Request: chat_id and animation required")
    chat_id = int(chat_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    content = json.dumps({"type": "animation", "file": str(animation), "caption": body.get("caption", "")})
    msg = bot_send_message(db, bot, chat_id, content, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/sendVoice")
async def api_sendVoice(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    voice = body.get("voice")
    if not chat_id or not voice:
        return tg_error(400, "Bad Request: chat_id and voice required")
    chat_id = int(chat_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    content = json.dumps({"type": "voice", "file": str(voice)})
    msg = bot_send_message(db, bot, chat_id, content, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/sendVideoNote")
async def api_sendVideoNote(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    video_note = body.get("video_note")
    if not chat_id or not video_note:
        return tg_error(400, "Bad Request: chat_id and video_note required")
    chat_id = int(chat_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    content = json.dumps({"type": "video_note", "file": str(video_note)})
    msg = bot_send_message(db, bot, chat_id, content, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/sendMediaGroup")
async def api_sendMediaGroup(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    media = body.get("media")
    if not chat_id or not media:
        return tg_error(400, "Bad Request: chat_id and media required")
    chat_id = int(chat_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    if isinstance(media, str):
        media = json.loads(media)
    content = json.dumps({"type": "media_group", "media": media})
    msg = bot_send_message(db, bot, chat_id, content, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/sendLocation")
async def api_sendLocation(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    latitude = body.get("latitude")
    longitude = body.get("longitude")
    if not chat_id or latitude is None or longitude is None:
        return tg_error(400, "Bad Request: chat_id, latitude, longitude required")
    chat_id = int(chat_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    content = json.dumps({"type": "location", "latitude": float(latitude), "longitude": float(longitude)})
    msg = bot_send_message(db, bot, chat_id, content, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/sendVenue")
async def api_sendVenue(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    latitude = body.get("latitude")
    longitude = body.get("longitude")
    title = body.get("title")
    address = body.get("address")
    if not chat_id or latitude is None or longitude is None or not title or not address:
        return tg_error(400, "Bad Request: chat_id, latitude, longitude, title, address required")
    chat_id = int(chat_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    content = json.dumps({
        "type": "venue", "latitude": float(latitude), "longitude": float(longitude),
        "title": title, "address": address,
    })
    msg = bot_send_message(db, bot, chat_id, content, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/sendContact")
async def api_sendContact(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    phone_number = body.get("phone_number")
    first_name = body.get("first_name")
    if not chat_id or not phone_number or not first_name:
        return tg_error(400, "Bad Request: chat_id, phone_number, first_name required")
    chat_id = int(chat_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    content = json.dumps({"type": "contact", "phone_number": phone_number, "first_name": first_name, "last_name": body.get("last_name", "")})
    msg = bot_send_message(db, bot, chat_id, content, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/sendPoll")
async def api_sendPoll(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    question = body.get("question")
    options_raw = body.get("options")
    if not chat_id or not question or not options_raw:
        return tg_error(400, "Bad Request: chat_id, question, options required")
    chat_id = int(chat_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    if isinstance(options_raw, str):
        options_raw = json.loads(options_raw)
    if isinstance(options_raw, list) and all(isinstance(o, dict) for o in options_raw):
        options = [o.get("text", str(o)) for o in options_raw]
    else:
        options = [str(o) for o in options_raw]
    is_anonymous = body.get("is_anonymous", True)
    poll = Poll(chat_id=chat_id, question=question, options=json.dumps(options), is_anonymous=is_anonymous)
    db.add(poll)
    db.flush()
    content = json.dumps({"type": "poll", "poll_id": poll.id, "question": question, "options": options})
    msg = bot_send_message(db, bot, chat_id, content, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/sendDice")
async def api_sendDice(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    emoji = body.get("emoji", "")
    if not chat_id:
        return tg_error(400, "Bad Request: chat_id required")
    chat_id = int(chat_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    content = json.dumps({"type": "dice", "emoji": emoji, "value": secrets.randbelow(6) + 1})
    msg = bot_send_message(db, bot, chat_id, content, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/sendChatAction")
async def api_sendChatAction(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    action = body.get("action")
    if not chat_id or not action:
        return tg_error(400, "Bad Request: chat_id and action required")
    from main import ws_manager
    await ws_manager.send_to_chat(int(chat_id), {"type": "chat_action", "action": action, "bot_id": bot.id})
    return tg_success(True)

@router.post("/sendSticker")
async def api_sendSticker(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    sticker = body.get("sticker")
    if not chat_id or not sticker:
        return tg_error(400, "Bad Request: chat_id and sticker required")
    chat_id = int(chat_id)
    if not check_membership(db, bot.owner_id, chat_id):
        return tg_error(400, "Bad Request: chat not found")
    content = json.dumps({"type": "sticker", "file": str(sticker)})
    msg = bot_send_message(db, bot, chat_id, content, body.get("reply_to_message_id"))
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.get("/getFile")
async def api_getFile(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    file_id = request.query_params.get("file_id")
    if not file_id:
        return tg_error(400, "Bad Request: file_id required")
    safe_path = os.path.abspath(os.path.join(UPLOAD_DIR, os.path.basename(file_id)))
    upload_abs = os.path.abspath(UPLOAD_DIR)
    if not safe_path.startswith(upload_abs):
        return tg_error(400, "Bad Request: invalid file_id")
    if os.path.exists(safe_path):
        file_size = os.path.getsize(safe_path)
        return tg_success({
            "file_id": file_id,
            "file_unique_id": file_id,
            "file_size": file_size,
            "file_path": f"uploads/bot_files/{file_id}",
        })
    return tg_error(404, "File not found")

@router.post("/deleteMessage")
async def api_deleteMessage(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    message_id = body.get("message_id")
    if not chat_id or not message_id:
        return tg_error(400, "Bad Request: chat_id and message_id required")
    msg = db.query(Message).filter(Message.id == int(message_id), Message.chat_id == int(chat_id)).first()
    if not msg:
        return tg_error(400, "Bad Request: message not found")
    msg.is_deleted = True
    db.commit()
    return tg_success(True)

@router.post("/editMessageText")
async def api_editMessageText(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    message_id = body.get("message_id")
    text = body.get("text")
    if not chat_id or not message_id or not text:
        return tg_error(400, "Bad Request: chat_id, message_id, text required")
    msg = db.query(Message).filter(Message.id == int(message_id), Message.chat_id == int(chat_id)).first()
    if not msg:
        return tg_error(400, "Bad Request: message not found")
    msg.content = text
    msg.edited = True
    msg.edited_at = datetime.utcnow()
    db.commit()
    sender = db.query(User).filter(User.id == msg.sender_id).first()
    chat = db.query(Chat).filter(Chat.id == msg.chat_id).first()
    return tg_success(tg_message(msg, sender, chat))

@router.post("/editMessageCaption")
async def api_editMessageCaption(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    message_id = body.get("message_id")
    caption = body.get("caption", "")
    if not chat_id or not message_id:
        return tg_error(400, "Bad Request: chat_id, message_id required")
    msg = db.query(Message).filter(Message.id == int(message_id), Message.chat_id == int(chat_id)).first()
    if not msg:
        return tg_error(400, "Bad Request: message not found")
    try:
        existing = json.loads(msg.content)
        if isinstance(existing, dict):
            existing["caption"] = caption
            msg.content = json.dumps(existing)
    except (json.JSONDecodeError, TypeError):
        pass
    msg.edited = True
    msg.edited_at = datetime.utcnow()
    db.commit()
    return tg_success(True)

@router.post("/editMessageMedia")
async def api_editMessageMedia(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    message_id = body.get("message_id")
    media = body.get("media")
    if not chat_id or not message_id or not media:
        return tg_error(400, "Bad Request: chat_id, message_id, media required")
    msg = db.query(Message).filter(Message.id == int(message_id), Message.chat_id == int(chat_id)).first()
    if not msg:
        return tg_error(400, "Bad Request: message not found")
    if isinstance(media, str):
        media = json.loads(media)
    try:
        existing = json.loads(msg.content)
        if isinstance(existing, dict):
            existing.update(media)
            msg.content = json.dumps(existing)
    except (json.JSONDecodeError, TypeError):
        msg.content = json.dumps(media)
    msg.edited = True
    msg.edited_at = datetime.utcnow()
    db.commit()
    return tg_success(True)

@router.post("/editMessageReplyMarkup")
async def api_editMessageReplyMarkup(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    message_id = body.get("message_id")
    reply_markup = body.get("reply_markup")
    if not chat_id or not message_id:
        return tg_error(400, "Bad Request: chat_id, message_id required")
    msg = db.query(Message).filter(Message.id == int(message_id), Message.chat_id == int(chat_id)).first()
    if not msg:
        return tg_error(400, "Bad Request: message not found")
    if reply_markup:
        try:
            existing = json.loads(msg.content)
            if isinstance(existing, dict):
                existing["reply_markup"] = json.loads(reply_markup) if isinstance(reply_markup, str) else reply_markup
                msg.content = json.dumps(existing)
        except (json.JSONDecodeError, TypeError):
            pass
    msg.edited = True
    msg.edited_at = datetime.utcnow()
    db.commit()
    return tg_success(True)

@router.post("/stopPoll")
async def api_stopPoll(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    message_id = body.get("message_id")
    if not chat_id or not message_id:
        return tg_error(400, "Bad Request: chat_id, message_id required")
    msg = db.query(Message).filter(Message.id == int(message_id), Message.chat_id == int(chat_id)).first()
    if not msg:
        return tg_error(400, "Bad Request: message not found")
    try:
        existing = json.loads(msg.content)
        poll_id = existing.get("poll_id")
    except (json.JSONDecodeError, TypeError):
        return tg_error(400, "Bad Request: not a poll message")
    if poll_id:
        poll = db.query(Poll).filter(Poll.id == poll_id).first()
        if poll:
            poll.is_closed = True
            db.commit()
    return tg_success(True)

@router.post("/inlineQuery")
async def api_inlineQuery(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    query = body.get("query", "")
    if not query:
        return tg_error(400, "Bad Request: query required")
    results = db.query(Sticker).filter(Sticker.url.ilike(f"%{query}%")).limit(50).all()
    return tg_success({
        "results": [{"type": "sticker", "id": str(s.id), "sticker_file_id": s.url} for s in results],
        "query": query
    })

@router.post("/answerCallbackQuery")
async def api_answerCallbackQuery(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    callback_query_id = body.get("callback_query_id")
    if not callback_query_id:
        return tg_error(400, "Bad Request: callback_query_id required")
    return tg_success(True)

# =====================================================================
# CHAT METHODS
# =====================================================================

@router.get("/getChat")
async def api_getChat(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    chat_id = request.query_params.get("chat_id")
    if not chat_id:
        return tg_error(400, "Bad Request: chat_id required")
    chat = db.query(Chat).filter(Chat.id == int(chat_id)).first()
    if not chat:
        return tg_error(400, "Bad Request: chat not found")
    membership_count = db.query(Membership).filter(Membership.chat_id == chat.id).count()
    d = tg_chat(chat)
    d["members_count"] = membership_count
    return tg_success(d)

@router.get("/getChatAdministrators")
async def api_getChatAdministrators(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    chat_id = request.query_params.get("chat_id")
    if not chat_id:
        return tg_error(400, "Bad Request: chat_id required")
    admins = db.query(Membership).filter(
        Membership.chat_id == int(chat_id),
        Membership.role.in_(["admin", "owner"]),
    ).all()
    result = []
    for m in admins:
        user = db.query(User).filter(User.id == m.user_id).first()
        if user:
            result.append({**tg_user(user), "status": m.role})
    return tg_success(result)

@router.get("/getChatMembersCount")
async def api_getChatMembersCount(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    chat_id = request.query_params.get("chat_id")
    if not chat_id:
        return tg_error(400, "Bad Request: chat_id required")
    count = db.query(Membership).filter(Membership.chat_id == int(chat_id)).count()
    return tg_success(count)

@router.get("/getChatMember")
async def api_getChatMember(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    chat_id = request.query_params.get("chat_id")
    user_id = request.query_params.get("user_id")
    if not chat_id or not user_id:
        return tg_error(400, "Bad Request: chat_id and user_id required")
    membership = db.query(Membership).filter(
        Membership.chat_id == int(chat_id), Membership.user_id == int(user_id)
    ).first()
    if not membership:
        return tg_error(400, "Bad Request: user not found in chat")
    user = db.query(User).filter(User.id == membership.user_id).first()
    if not user:
        return tg_error(400, "Bad Request: user not found")
    return tg_success({**tg_user(user), "status": membership.role})

@router.post("/leaveChat")
async def api_leaveChat(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    if not chat_id:
        return tg_error(400, "Bad Request: chat_id required")
    membership = db.query(Membership).filter(
        Membership.chat_id == int(chat_id), Membership.user_id == bot.owner_id
    ).first()
    if membership:
        db.delete(membership)
        db.commit()
    return tg_success(True)

@router.post("/setChatTitle")
async def api_setChatTitle(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    title = body.get("title")
    if not chat_id or not title:
        return tg_error(400, "Bad Request: chat_id and title required")
    chat = db.query(Chat).filter(Chat.id == int(chat_id)).first()
    if not chat:
        return tg_error(400, "Bad Request: chat not found")
    chat.name = title
    db.commit()
    return tg_success(True)

@router.post("/setChatDescription")
async def api_setChatDescription(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    description = body.get("description", "")
    if not chat_id:
        return tg_error(400, "Bad Request: chat_id required")
    chat = db.query(Chat).filter(Chat.id == int(chat_id)).first()
    if not chat:
        return tg_error(400, "Bad Request: chat not found")
    chat.description = description
    db.commit()
    return tg_success(True)

@router.post("/setChatPhoto")
async def api_setChatPhoto(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    form = await request.form()
    chat_id = form.get("chat_id")
    photo = form.get("photo")
    if not chat_id or not photo:
        return tg_error(400, "Bad Request: chat_id and photo required")
    chat = db.query(Chat).filter(Chat.id == int(chat_id)).first()
    if not chat:
        return tg_error(400, "Bad Request: chat not found")
    if not hasattr(photo, "read"):
        return tg_error(400, "Bad Request: invalid photo")
    os.makedirs("uploads/chat_photos", exist_ok=True)
    ext = os.path.splitext(getattr(photo, "filename", "photo.jpg"))[1] or ".jpg"
    filename = f"chat_{chat_id}{ext}"
    filepath = os.path.join("uploads/chat_photos", filename)
    with open(filepath, "wb") as f:
        f.write(await photo.read())
    chat.avatar_url = f"/uploads/chat_photos/{filename}"
    db.commit()
    return tg_success(True)

@router.post("/pinChatMessage")
async def api_pinChatMessage(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    message_id = body.get("message_id")
    if not chat_id or not message_id:
        return tg_error(400, "Bad Request: chat_id and message_id required")
    chat = db.query(Chat).filter(Chat.id == int(chat_id)).first()
    if not chat:
        return tg_error(400, "Bad Request: chat not found")
    chat.pinned_message_id = int(message_id)
    db.commit()
    return tg_success(True)

@router.post("/unpinChatMessage")
async def api_unpinChatMessage(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    if not chat_id:
        return tg_error(400, "Bad Request: chat_id required")
    chat = db.query(Chat).filter(Chat.id == int(chat_id)).first()
    if not chat:
        return tg_error(400, "Bad Request: chat not found")
    chat.pinned_message_id = None
    db.commit()
    return tg_success(True)

# =====================================================================
# BOT INFO & UPDATES
# =====================================================================

@router.get("/getMe")
async def api_getMe(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    owner = db.query(User).filter(User.id == bot.owner_id).first()
    return tg_success({
        "id": bot.id,
        "is_bot": True,
        "first_name": bot.name,
        "username": bot.username,
        "can_join_groups": True,
        "can_read_all_group_messages": True,
        "supports_inline_queries": False,
    })

@router.get("/getUpdates")
async def api_getUpdates(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    offset = request.query_params.get("offset")
    limit = int(request.query_params.get("limit", 100))
    timeout = int(request.query_params.get("timeout", 0))
    limit = min(limit, 100)

    chat_ids = [m.chat_id for m in db.query(Membership).filter(Membership.user_id == bot.owner_id).all()]
    query = db.query(Message).filter(
        Message.chat_id.in_(chat_ids),
        Message.id > (int(offset) if offset else 0),
    ).order_by(Message.id).limit(limit)

    messages = query.all()
    if not messages and timeout > 0:
        for _ in range(timeout):
            await asyncio.sleep(1)
            messages = query.all()
            if messages:
                break

    updates = []
    for msg in messages:
        sender = db.query(User).filter(User.id == msg.sender_id).first() if msg.sender_id else None
        chat = db.query(Chat).filter(Chat.id == msg.chat_id).first() if msg.chat_id else None
        updates.append({
            "update_id": msg.id,
            "message": tg_message(msg, sender, chat),
        })
    return tg_success(updates)

@router.post("/setWebhook")
async def api_setWebhook(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    body = await get_body(request)
    url = body.get("url")
    if not url:
        return tg_error(400, "Bad Request: url required")
    max_connections = int(body.get("max_connections", 40))
    allowed_updates = body.get("allowed_updates", "[]")
    if isinstance(allowed_updates, list):
        allowed_updates = json.dumps(allowed_updates)
    wh = db.query(BotWebhook).filter(BotWebhook.bot_id == bot.id).first()
    if wh:
        wh.url = url
        wh.max_connections = max_connections
        wh.allowed_updates = allowed_updates
        wh.last_error_date = None
        wh.last_error_message = None
    else:
        wh = BotWebhook(bot_id=bot.id, url=url, max_connections=max_connections, allowed_updates=allowed_updates)
        db.add(wh)
    db.commit()
    return tg_success(True)

@router.get("/getWebhookInfo")
async def api_getWebhookInfo(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    wh = db.query(BotWebhook).filter(BotWebhook.bot_id == bot.id).first()
    if wh:
        return tg_success({
            "url": wh.url,
            "has_custom_certificate": False,
            "pending_update_count": 0,
            "max_connections": wh.max_connections,
            "last_error_date": int(wh.last_error_date.timestamp()) if wh.last_error_date else None,
            "last_error_message": wh.last_error_message,
        })
    return tg_success({"url": "", "has_custom_certificate": False, "pending_update_count": 0})

@router.delete("/deleteWebhook")
async def api_deleteWebhook(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err:
        return err
    wh = db.query(BotWebhook).filter(BotWebhook.bot_id == bot.id).first()
    if wh:
        db.delete(wh)
        db.commit()
    return tg_success(True)

@router.post("/banChatMember")
async def api_banChatMember(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err: return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    user_id = body.get("user_id")
    if not chat_id or not user_id:
        return tg_error(400, "Bad Request: chat_id and user_id required")
    membership = db.query(Membership).filter(Membership.user_id == user_id, Membership.chat_id == chat_id).first()
    if membership:
        membership.is_blocked = True
        db.commit()
    return tg_success(True)

@router.post("/kickChatMember")
async def api_kickChatMember(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err: return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    user_id = body.get("user_id")
    if not chat_id or not user_id:
        return tg_error(400, "Bad Request: chat_id and user_id required")
    membership = db.query(Membership).filter(Membership.user_id == user_id, Membership.chat_id == chat_id).first()
    if membership:
        db.delete(membership)
        db.commit()
    return tg_success(True)

@router.post("/restrictChatMember")
async def api_restrictChatMember(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err: return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    user_id = body.get("user_id")
    until_date = body.get("until_date")
    if not chat_id or not user_id:
        return tg_error(400, "Bad Request: chat_id and user_id required")
    membership = db.query(Membership).filter(Membership.user_id == user_id, Membership.chat_id == chat_id).first()
    if membership:
        membership.is_muted = True
        if until_date:
            from datetime import datetime
            membership.muted_until = datetime.fromisoformat(until_date)
        db.commit()
    return tg_success(True)

@router.post("/promoteChatMember")
async def api_promoteChatMember(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err: return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    user_id = body.get("user_id")
    if not chat_id or not user_id:
        return tg_error(400, "Bad Request: chat_id and user_id required")
    membership = db.query(Membership).filter(Membership.user_id == user_id, Membership.chat_id == chat_id).first()
    if membership:
        membership.role = "admin"
        db.commit()
    return tg_success(True)

@router.post("/sendInvoice")
async def api_sendInvoice(request: Request, db: Session = Depends(get_db)):
    bot, err = await get_bot(request, db)
    if err: return err
    body = await get_body(request)
    chat_id = body.get("chat_id")
    title = body.get("title", "")
    description = body.get("description", "")
    amount = body.get("amount", 0)
    if not chat_id or not amount:
        return tg_error(400, "Bad Request: chat_id and amount required")
    invoice_msg = Message(
        content=json.dumps({"type": "invoice", "title": title, "description": description, "amount": amount, "currency": "RUB"}),
        sender_id=bot.owner_id,
        chat_id=chat_id,
        bot_id=bot.id
    )
    db.add(invoice_msg)
    db.commit()
    db.refresh(invoice_msg)
    return tg_success({"message_id": invoice_msg.id, "chat_id": chat_id})
