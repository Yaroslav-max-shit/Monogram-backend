from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from database import get_db
from models import User, Bot, BotCommand
from .auth import get_current_user
import secrets, os, json
from datetime import datetime

router = APIRouter(prefix="/bots", tags=["bot_management"])

UPLOAD_DIR = "uploads/bot_avatars"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@router.get("/myBots")
def list_my_bots(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    bots = db.query(Bot).filter(Bot.owner_id == current_user.id).all()
    return [{
        "id": b.id,
        "name": b.name,
        "username": b.username,
        "description": b.description,
        "avatar_url": b.avatar_url,
        "is_active": b.is_active,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    } for b in bots]

@router.post("/regenerateKey")
def regenerate_bot_key(
    bot_id: int = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    bot = db.query(Bot).filter(Bot.id == bot_id, Bot.owner_id == current_user.id).first()
    if not bot:
        raise HTTPException(404, "Bot not found")
    bot.api_key = secrets.token_hex(32)
    db.commit()
    return {"api_key": bot.api_key}

@router.post("/setBotAvatar")
async def set_bot_avatar(
    bot_id: int = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    bot = db.query(Bot).filter(Bot.id == bot_id, Bot.owner_id == current_user.id).first()
    if not bot:
        raise HTTPException(404, "Bot not found")
    ext = os.path.splitext(file.filename or "avatar.png")[1] or ".png"
    filename = f"bot_{bot_id}_{secrets.token_hex(8)}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)
    content = await file.read()
    with open(filepath, "wb") as f:
        f.write(content)
    bot.avatar_url = f"/uploads/bot_avatars/{filename}"
    db.commit()
    return {"avatar_url": bot.avatar_url}

@router.post("/setBotCommands")
def set_bot_commands(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    bot_id = data.get("bot_id")
    commands = data.get("commands", [])
    if not bot_id:
        raise HTTPException(400, "bot_id required")
    bot = db.query(Bot).filter(Bot.id == bot_id, Bot.owner_id == current_user.id).first()
    if not bot:
        raise HTTPException(404, "Bot not found")
    db.query(BotCommand).filter(BotCommand.bot_id == bot.id).delete()
    for cmd in commands:
        db.add(BotCommand(
            bot_id=bot.id,
            command=cmd.get("command", "").lstrip("/"),
            description=cmd.get("description", ""),
        ))
    db.commit()
    return {"status": "ok", "commands": commands}
