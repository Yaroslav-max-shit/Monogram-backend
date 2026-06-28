from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import User, Bot, Message, Chat, Membership
from .auth import get_current_user
import secrets

router = APIRouter(prefix="/bots", tags=["bots"])

# ============================================
# СОЗДАНИЕ БОТА
# ============================================
@router.post("/create")
def create_bot(name: str, username: str, description: str = "", db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not (username.endswith("Bot") or username.endswith("_bot")):
        raise HTTPException(400, "Юзернейм должен заканчиваться на Bot или _bot")
    if db.query(Bot).filter(Bot.username == username).first():
        raise HTTPException(400, "Бот с таким юзернеймом уже существует")
    
    bot = Bot(name=name, username=username, api_key=secrets.token_hex(32), owner_id=current_user.id, description=description)
    db.add(bot)
    db.commit()
    db.refresh(bot)
    return {"id": bot.id, "name": bot.name, "username": bot.username, "api_key": bot.api_key}

# ============================================
# ОБЯЗАТЕЛЬНЫЕ МЕТОДЫ
# ============================================
@router.post("/send")
def bot_send(bot_id: int, chat_id: int, content: str, api_key: str, db: Session = Depends(get_db)):
    bot = db.query(Bot).filter(Bot.id == bot_id, Bot.api_key == api_key, Bot.is_active == True).first()
    if not bot: raise HTTPException(403, "Неверный API ключ")
    
    msg = Message(content=content, sender_id=bot.owner_id, chat_id=chat_id, bot_id=bot.id)
    db.add(msg)
    db.commit()
    return {"status": "sent", "message_id": msg.id}

@router.get("/updates")
def bot_updates(bot_id: int, api_key: str, offset: int = 0, db: Session = Depends(get_db)):
    bot = db.query(Bot).filter(Bot.id == bot_id, Bot.api_key == api_key, Bot.is_active == True).first()
    if not bot: raise HTTPException(403, "Неверный API ключ")
    
    msgs = db.query(Message).filter(Message.id > offset, Message.chat_id.in_(db.query(Membership.chat_id).filter(Membership.user_id == bot.owner_id))).order_by(Message.id).all()
    return [{"update_id": m.id, "message": {"message_id": m.id, "chat_id": m.chat_id, "content": m.content, "sender_id": m.sender_id}} for m in msgs]

# ============================================
# ДОПОЛНИТЕЛЬНЫЕ МЕТОДЫ
# ============================================
@router.get("/info")
def bot_info(bot_id: int, api_key: str, db: Session = Depends(get_db)):
    bot = db.query(Bot).filter(Bot.id == bot_id, Bot.api_key == api_key).first()
    if not bot: raise HTTPException(403)
    return {"id": bot.id, "name": bot.name, "username": bot.username, "description": bot.description}

@router.get("/chats")
def bot_chats(bot_id: int, api_key: str, db: Session = Depends(get_db)):
    bot = db.query(Bot).filter(Bot.id == bot_id, Bot.api_key == api_key).first()
    if not bot: raise HTTPException(403)
    chats = db.query(Chat).join(Membership).filter(Membership.user_id == bot.owner_id).all()
    return [{"id": c.id, "name": c.name, "type": c.type} for c in chats]

@router.post("/edit")
def bot_edit(bot_id: int, api_key: str, name: str = None, description: str = None, db: Session = Depends(get_db)):
    bot = db.query(Bot).filter(Bot.id == bot_id, Bot.api_key == api_key).first()
    if not bot: raise HTTPException(403)
    if name: bot.name = name
    if description: bot.description = description
    db.commit()
    return {"status": "updated"}

@router.get("/me")
def bot_me(bot_id: int, api_key: str, db: Session = Depends(get_db)):
    bot = db.query(Bot).filter(Bot.id == bot_id, Bot.api_key == api_key, Bot.is_active == True).first()
    if not bot: raise HTTPException(403)
    return {"id": bot.id, "name": bot.name, "username": bot.username}