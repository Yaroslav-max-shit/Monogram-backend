from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from database import get_db
from models import User, Story, StoryReaction
from .auth import get_current_user
import json
import os
import secrets

router = APIRouter(prefix="/stories", tags=["stories"])

STORIES_LIMIT_FREE = 3
STORIES_LIMIT_PREMIUM = 999
STORIES_EXPIRY_HOURS = 24

@router.get("/")
def get_stories(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Получить истории (от друзей и своих)"""
    now = datetime.utcnow()
    stories = db.query(Story).filter(Story.expires_at > now).order_by(Story.created_at.desc()).limit(100).all()

    result = []
    for s in stories:
        user = db.query(User).filter(User.id == s.user_id).first()
        reactions = db.query(StoryReaction).filter(StoryReaction.story_id == s.id).all()
        reaction_counts = {}
        for r in reactions:
            reaction_counts[r.emoji] = reaction_counts.get(r.emoji, 0) + 1

        result.append({
            "id": s.id,
            "user_id": s.user_id,
            "username": user.username if user else "unknown",
            "avatar_url": user.avatar_url if user else None,
            "content_type": s.content_type,
            "content_url": s.content_url,
            "text_content": s.text_content,
            "bg_color": s.bg_color,
            "font_color": s.font_color,
            "poll_question": s.poll_question,
            "poll_options": json.loads(s.poll_options) if s.poll_options else None,
            "reactions": reaction_counts,
            "created_at": s.created_at.isoformat(),
            "expires_at": s.expires_at.isoformat(),
        })
    return result

@router.post("/")
def create_story(data: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Создать историю"""
    now = datetime.utcnow()
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    is_premium = current_user.premium_until and current_user.premium_until > now
    limit = STORIES_LIMIT_PREMIUM if is_premium else STORIES_LIMIT_FREE

    week_stories = db.query(Story).filter(
        Story.user_id == current_user.id,
        Story.created_at >= week_start
    ).count()

    if week_stories >= limit:
        raise HTTPException(status_code=400, detail=f"Лимит историй ({limit}/неделю)")

    content_type = data.get("content_type", "text")
    if content_type not in ["photo", "video", "text", "poll"]:
        raise HTTPException(status_code=400, detail="Неверный тип контента")

    story = Story(
        user_id=current_user.id,
        content_type=content_type,
        content_url=data.get("content_url"),
        text_content=data.get("text_content"),
        bg_color=data.get("bg_color", "#667eea"),
        font_color=data.get("font_color", "#ffffff"),
        poll_question=data.get("poll_question"),
        poll_options=json.dumps(data.get("poll_options", [])) if data.get("poll_options") else None,
        expires_at=datetime.utcnow() + timedelta(hours=STORIES_EXPIRY_HOURS),
    )
    db.add(story)
    db.commit()
    db.refresh(story)

    return {"id": story.id, "status": "created"}

@router.post("/{story_id}/react")
def react_to_story(story_id: int, data: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Отреагировать на историю"""
    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise HTTPException(status_code=404, detail="История не найдена")

    existing = db.query(StoryReaction).filter(
        StoryReaction.story_id == story_id,
        StoryReaction.user_id == current_user.id,
        StoryReaction.emoji == data.get("emoji")
    ).first()

    if existing:
        db.delete(existing)
        db.commit()
        return {"status": "removed"}

    reaction = StoryReaction(
        story_id=story_id,
        user_id=current_user.id,
        emoji=data.get("emoji", "❤️"),
    )
    db.add(reaction)
    db.commit()
    return {"status": "added"}

@router.delete("/{story_id}")
def delete_story(story_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Удалить свою историю"""
    story = db.query(Story).filter(Story.id == story_id, Story.user_id == current_user.id).first()
    if not story:
        raise HTTPException(status_code=404, detail="История не найдена")

    db.delete(story)
    db.commit()
    return {"status": "deleted"}
