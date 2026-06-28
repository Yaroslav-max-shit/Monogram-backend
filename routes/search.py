from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from models import User, Chat, Message, Membership
from datetime import datetime
from .auth import get_current_user

router = APIRouter(prefix="/search", tags=["search"])

@router.get("/global")
def global_search(
    q: str,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Глобальный поиск по чатам и сообщениям"""
    
    # Получаем ID чатов, в которых состоит пользователь
    user_chat_ids = [
        m.chat_id for m in db.query(Membership).filter(Membership.user_id == current_user.id).all()
    ]
    
    # Поиск по чатам (только те, где пользователь участник)
    chats = db.query(Chat).filter(
        Chat.name.ilike(f"%{q}%"),
        Chat.id.in_(user_chat_ids)
    ).limit(limit).all()
    
    # Поиск по сообщениям (только в чатах пользователя)
    messages = db.query(Message).filter(
        Message.content.ilike(f"%{q}%"),
        Message.chat_id.in_(user_chat_ids),
        Message.is_deleted == False
    ).order_by(Message.timestamp.desc()).limit(limit).all()
    
    # Поиск по пользователям
    users = db.query(User).filter(
        User.username.ilike(f"%{q}%") | 
        User.first_name.ilike(f"%{q}%") |
        User.last_name.ilike(f"%{q}%")
    ).limit(limit).all()
    
    return {
        "chats": [
            {
                "id": c.id,
                "name": c.name,
                "type": c.type,
                "type_label": "Группа" if c.type == "group" else "Канал" if c.type == "channel" else "Личный"
            }
            for c in chats
        ],
        "messages": [
            {
                "id": m.id,
                "content": m.content[:100],
                "chat_id": m.chat_id,
                "sender_id": m.sender_id,
                "timestamp": m.timestamp.isoformat()
            }
            for m in messages
        ],
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "avatar_url": u.avatar_url
            }
            for u in users
        ]
    }

@router.get("/messages")
def search_messages(
    q: str,
    chat_id: int = None,
    from_date: str = None,
    to_date: str = None,
    sender_id: int = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Расширенный поиск по сообщениям с фильтрами"""
    
    user_chat_ids = [
        m.chat_id for m in db.query(Membership).filter(Membership.user_id == current_user.id).all()
    ]
    
    query = db.query(Message).filter(
        Message.content.ilike(f"%{q}%"),
        Message.chat_id.in_(user_chat_ids)
    )
    
    if chat_id:
        query = query.filter(Message.chat_id == chat_id)
    
    if from_date:
        query = query.filter(Message.timestamp >= datetime.fromisoformat(from_date))
    
    if to_date:
        query = query.filter(Message.timestamp <= datetime.fromisoformat(to_date))
    
    if sender_id:
        query = query.filter(Message.sender_id == sender_id)
    
    messages = query.order_by(Message.timestamp.desc()).limit(limit).all()
    
    return [
        {
            "id": m.id,
            "content": m.content,
            "chat_id": m.chat_id,
            "sender_id": m.sender_id,
            "timestamp": m.timestamp.isoformat()
        }
        for m in messages
    ]

