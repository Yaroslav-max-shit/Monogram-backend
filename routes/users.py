from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
router = APIRouter(prefix="/users", tags=["users"])

from sqlalchemy.orm import Session
from typing import Annotated, Optional
from database import get_db
from models import User, Report, Profile, Message, Membership, Chat, UserSettings
from schemas import UserResponse
from .auth import get_current_user
from models import BlockedUser
import os
import shutil
from pathlib import Path
from datetime import datetime, timedelta


# ============================================
# Бизнес-профиль
# ============================================

@router.post("/business/update")
def update_business_profile(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    profile = db.query(Profile).filter(Profile.user_id == current_user.id).first()
    if not profile:
        profile = Profile(user_id=current_user.id)
        db.add(profile)
    profile.bio = data.get("bio", profile.bio or "")
    profile.company = data.get("company", profile.company or "")
    profile.position = data.get("position", profile.position or "")
    profile.working_hours = data.get("working_hours", profile.working_hours or "")
    profile.website = data.get("website", profile.website or "")
    db.commit()
    return {"status": "updated"}

# ============================================
# Emoji статус
# ============================================

@router.post("/status")
def set_emoji_status(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    emoji = data.get("emoji", "")
    expires_in = data.get("expires_in", 3600)
    current_user.emoji_status = emoji
    current_user.emoji_status_expires = datetime.utcnow() + timedelta(seconds=expires_in) if emoji else None
    db.commit()
    return {"status": "ok", "emoji": emoji}

@router.delete("/status")
def clear_emoji_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    current_user.emoji_status = None
    current_user.emoji_status_expires = None
    db.commit()
    return {"status": "cleared"}

AVATARS_DIR = Path("uploads/avatars")
AVATARS_DIR.mkdir(parents=True, exist_ok=True)

@router.get("/search")
def search_users(
    q: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    users = db.query(User).filter(
        (User.username.ilike(f"%{q}%")) | 
        (User.first_name.ilike(f"%{q}%")) | 
        (User.last_name.ilike(f"%{q}%"))
    ).limit(20).all()
    return [{"id": u.id, "username": u.username, "first_name": u.first_name, "last_name": u.last_name, "avatar_url": u.avatar_url} for u in users]

@router.get("/public/{username}")
def get_public_profile(username: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    profile = db.query(Profile).filter(Profile.user_id == user.id).first()
    return {
        "id": user.id,
        "profile_id": user.profile_id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "avatar_url": user.avatar_url,
        "bio": profile.bio if profile else None,
        "is_premium": user.premium_until is not None and user.premium_until > datetime.utcnow()
    }

@router.get("/avatar/{user_id}")
def get_user_avatar(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return {"avatar_url": user.avatar_url}

@router.post("/avatar", response_model=UserResponse)
async def upload_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Можно загружать только изображения")
    
    file_extension = file.filename.split(".")[-1] if file.filename and "." in file.filename else "png"
    avatar_filename = f"user_{current_user.id}.{file_extension}"
    avatar_path = AVATARS_DIR / avatar_filename
    
    with avatar_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    avatar_url = f"/uploads/avatars/{avatar_filename}"
    current_user.avatar_url = avatar_url
    db.commit()
    db.refresh(current_user)
    
    return current_user

@router.put("/profile", response_model=UserResponse)
def update_profile(
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    bio: Optional[str] = None,
    custom_status: Optional[str] = None,
    custom_status_emoji: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if first_name is not None:
        current_user.first_name = first_name
    if last_name is not None:
        current_user.last_name = last_name
    
    profile = db.query(Profile).filter(Profile.user_id == current_user.id).first()
    if not profile:
        profile = Profile(user_id=current_user.id)
        db.add(profile)
    
    if bio is not None:
        profile.bio = bio
    if custom_status is not None:
        profile.custom_status = custom_status
    if custom_status_emoji is not None:
        profile.custom_status_emoji = custom_status_emoji
    
    db.commit()
    db.refresh(current_user)
    
    return current_user

@router.get("/profile")
def get_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    profile = db.query(Profile).filter(Profile.user_id == current_user.id).first()
    return {
        "id": current_user.id,
        "username": current_user.username,
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "avatar_url": current_user.avatar_url,
        "email": current_user.email,
        "bio": profile.bio if profile else None,
        "custom_status": profile.custom_status if profile else None,
        "custom_status_emoji": profile.custom_status_emoji if profile else None,
        "created_at": current_user.created_at
    }

@router.post("/report/{user_id}")
def report_user(
    user_id: int,
    reason: str,
    message_id: int = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if user_id == current_user.id:
        raise HTTPException(400, "Нельзя жаловаться на себя")
    
    reported_user = db.query(User).filter(User.id == user_id).first()
    if not reported_user:
        raise HTTPException(404, "Пользователь не найден")
    
    report = Report(
        reporter_id=current_user.id,
        reported_user_id=user_id,
        reason=reason,
        message_id=message_id
    )
    db.add(report)
    db.commit()
    
    return {"status": "reported", "report_id": report.id}

@router.get("/block/{user_id}")
def block_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if user_id == current_user.id:
        raise HTTPException(400, "Cannot block yourself")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "User not found")
    existing = db.query(BlockedUser).filter(
        BlockedUser.user_id == current_user.id,
        BlockedUser.blocked_user_id == user_id
    ).first()
    if not existing:
        db.add(BlockedUser(user_id=current_user.id, blocked_user_id=user_id))
        db.commit()
    return {"status": "blocked"}


# ============================================
# Account Stats
# ============================================

@router.get("/stats")
def get_user_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    message_count = db.query(Message).filter(Message.sender_id == current_user.id).count()
    chat_count = db.query(Membership).filter(Membership.user_id == current_user.id).count()
    media_count = db.query(Message).filter(
        Message.sender_id == current_user.id,
        Message.content.like("%[file:%")
    ).count()
    days_since_registration = (datetime.utcnow() - current_user.created_at).days if current_user.created_at else 0
    settings = db.query(UserSettings).filter(UserSettings.user_id == current_user.id).first()
    return {
        "messages_sent": message_count,
        "chats_count": chat_count,
        "media_sent": media_count,
        "days_on_platform": days_since_registration,
        "show_in_profile": settings.show_stats if settings else True
    }

