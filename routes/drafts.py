from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from database import get_db
from models import User, Draft, Chat, Membership
from .auth import get_current_user

router = APIRouter(prefix="", tags=["drafts"])

@router.post("/drafts/save")
def save_draft(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    chat_id = data.get("chat_id")
    content = data.get("content", "")
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id обязателен")
    draft = db.query(Draft).filter(Draft.user_id == current_user.id, Draft.chat_id == chat_id).first()
    if draft:
        draft.content = content
        draft.updated_at = datetime.utcnow()
    else:
        db.add(Draft(user_id=current_user.id, chat_id=chat_id, content=content))
    db.commit()
    return {"status": "ok"}

@router.get("/drafts/{chat_id}")
def get_draft(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    draft = db.query(Draft).filter(Draft.user_id == current_user.id, Draft.chat_id == chat_id).first()
    return {"content": draft.content if draft else ""}

@router.delete("/drafts/{chat_id}")
def delete_draft(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    db.query(Draft).filter(Draft.user_id == current_user.id, Draft.chat_id == chat_id).delete()
    db.commit()
    return {"status": "deleted"}
