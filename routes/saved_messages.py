from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from database import get_db
from models import User, SavedMessage, Message, Membership
from .auth import get_current_user

router = APIRouter(prefix="/saved", tags=["saved"])

@router.post("/add")
def save_message(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    message_id = data.get("message_id")
    if not message_id:
        raise HTTPException(status_code=400, detail="message_id обязателен")
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == msg.chat_id
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Нет доступа к сообщению")
    existing = db.query(SavedMessage).filter(SavedMessage.user_id == current_user.id, SavedMessage.message_id == message_id).first()
    if existing:
        return {"status": "already_saved"}
    db.add(SavedMessage(user_id=current_user.id, message_id=message_id))
    db.commit()
    return {"status": "saved"}

@router.delete("/remove/{message_id}")
def unsave_message(
    message_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    db.query(SavedMessage).filter(SavedMessage.user_id == current_user.id, SavedMessage.message_id == message_id).delete()
    db.commit()
    return {"status": "removed"}

@router.get("/list")
def list_saved(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    saved = db.query(SavedMessage).filter(SavedMessage.user_id == current_user.id).order_by(SavedMessage.saved_at.desc()).all()
    message_ids = [s.message_id for s in saved]
    messages = db.query(Message).filter(Message.id.in_(message_ids)).all()
    return {"saved": [{"id": m.id, "content": m.content, "created_at": m.timestamp.isoformat() if m.timestamp else None} for m in messages]}
