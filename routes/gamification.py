from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from database import get_db
from models import User, UserXP, UserStreak, Achievement, UserAchievement, UserStats
from .auth import get_current_user
import json

router = APIRouter(prefix="/gamification", tags=["gamification"])

# Уровни и звания
RANKS = [
    (1, "Основатель"),
    (2, "Новичок"),
    (3, "Путник"),
    (4, "Знакомый"),
    (5, "Понимающий"),
    (6, "Опытный"),
    (7, "Активный"),
    (8, "Мастер"),
    (9, "Эксперт"),
    (10, "Профи"),
    (11, "Виртуоз"),
    (12, "Легенда"),
    (13, "Гений"),
    (14, "Титан"),
    (15, "Бессмертный"),
    (16, "Хранитель"),
    (17, "Повелитель"),
    (18, "Абсолют"),
    (19, "Создатель"),
    (20, "Бог"),
]

XP_PER_ACTION = {
    "message": 10,
    "settings": 50,
    "quarkpay": 500,
    "call": 30,
    "photo": 20,
    "video": 25,
    "streak": 15,
    "achievement": 100,
}

DAILY_LIMITS = {
    "message": 150,
    "settings": 100,
    "default": 200,
}

def get_rank_for_level(level):
    for max_level, title in RANKS:
        if level <= max_level:
            return title
    return RANKS[-1][1]

def get_xp_for_level(level):
    return int(100 * (1.5 ** (level - 1)))

@router.get("/profile")
def get_gamification_profile(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    xp = db.query(UserXP).filter(UserXP.user_id == current_user.id).first()
    if not xp:
        xp = UserXP(user_id=current_user.id)
        db.add(xp)
        db.commit()
        db.refresh(xp)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    if xp.daily_xp_date != today:
        xp.daily_xp = 0
        xp.daily_xp_date = today
        db.commit()

    stats = db.query(UserStats).filter(UserStats.user_id == current_user.id).first()
    if not stats:
        stats = UserStats(user_id=current_user.id)
        db.add(stats)
        db.commit()
        db.refresh(stats)

    achievements = db.query(UserAchievement).filter(UserAchievement.user_id == current_user.id).all()
    achievement_ids = [ua.achievement_id for ua in achievements]

    return {
        "xp": xp.total_xp,
        "daily_xp": xp.daily_xp,
        "level": xp.level,
        "rank_title": xp.rank_title,
        "show_level": xp.show_level,
        "show_achievements": xp.show_achievements,
        "next_level_xp": get_xp_for_level(xp.level),
        "stats": {
            "messages_sent": stats.messages_sent,
            "photos_shared": stats.photos_shared,
            "videos_shared": stats.videos_shared,
            "calls_made": stats.calls_made,
            "chats_joined": stats.chats_joined,
        },
        "achievements_count": len(achievement_ids),
    }

@router.post("/add-xp")
def add_xp(data: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    action = data.get("action", "")
    amount = XP_PER_ACTION.get(action, 5)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    xp = db.query(UserXP).filter(UserXP.user_id == current_user.id).first()
    if not xp:
        xp = UserXP(user_id=current_user.id)
        db.add(xp)
        db.commit()
        db.refresh(xp)

    if xp.daily_xp_date != today:
        xp.daily_xp = 0
        xp.daily_xp_date = today

    is_premium = current_user.premium_until and current_user.premium_until > datetime.utcnow()
    daily_limit = DAILY_LIMITS.get(action, DAILY_LIMITS["default"])
    if is_premium:
        daily_limit *= 2

    if xp.daily_xp >= daily_limit:
        return {"added": 0, "daily_xp": xp.daily_xp, "limit": daily_limit, "level_up": False}

    added = min(amount, daily_limit - xp.daily_xp)
    xp.total_xp += added
    xp.daily_xp += added

    level_up = False
    while xp.total_xp >= get_xp_for_level(xp.level):
        xp.level += 1
        xp.rank_title = get_rank_for_level(xp.level)
        level_up = True

    db.commit()

    return {
        "added": added,
        "total_xp": xp.total_xp,
        "daily_xp": xp.daily_xp,
        "level": xp.level,
        "rank_title": xp.rank_title,
        "level_up": level_up,
    }

@router.get("/achievements")
def get_achievements(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    all_achievements = db.query(Achievement).all()
    user_achievements = db.query(UserAchievement).filter(UserAchievement.user_id == current_user.id).all()
    unlocked_ids = {ua.achievement_id for ua in user_achievements}

    result = []
    for a in all_achievements:
        result.append({
            "id": a.id,
            "name": a.name,
            "description": a.description,
            "icon": a.icon,
            "category": a.category,
            "xp_reward": a.xp_reward,
            "unlocked": a.id in unlocked_ids,
            "unlocked_at": next((ua.unlocked_at.isoformat() for ua in user_achievements if ua.achievement_id == a.id), None),
        })
    return result

@router.post("/achievements/check")
def check_achievements(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stats = db.query(UserStats).filter(UserStats.user_id == current_user.id).first()
    if not stats:
        return {"new_achievements": []}

    all_achievements = db.query(Achievement).all()
    user_achievements = db.query(UserAchievement).filter(UserAchievement.user_id == current_user.id).all()
    unlocked_ids = {ua.achievement_id for ua in user_achievements}

    new_achievements = []
    for a in all_achievements:
        if a.id in unlocked_ids:
            continue
        unlocked = False
        if a.category == "activity" and stats.messages_sent >= 100:
            unlocked = True
        elif a.category == "media" and stats.photos_shared >= 100:
            unlocked = True
        elif a.category == "calls" and stats.calls_made >= 10:
            unlocked = True

        if unlocked:
            ua = UserAchievement(user_id=current_user.id, achievement_id=a.id)
            db.add(ua)
            new_achievements.append({"id": a.id, "name": a.name, "xp_reward": a.xp_reward})

    if new_achievements:
        db.commit()

    return {"new_achievements": new_achievements}

@router.get("/streaks")
def get_streaks(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    streaks = db.query(UserStreak).filter(UserStreak.user_id == current_user.id).all()
    return [{
        "chat_id": s.chat_id,
        "streak_days": s.streak_days,
        "restores_used": s.restores_used,
    } for s in streaks]

@router.post("/streaks/restore")
def restore_streak(data: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    chat_id = data.get("chat_id")
    is_premium = current_user.premium_until and current_user.premium_until > datetime.utcnow()
    max_restores = 5 if is_premium else 3

    streak = db.query(UserStreak).filter(
        UserStreak.user_id == current_user.id,
        UserStreak.chat_id == chat_id
    ).first()

    if not streak:
        raise HTTPException(status_code=404, detail="Стрек не найден")

    today = datetime.utcnow().strftime("%Y-%m")
    if streak.restores_reset_date != today:
        streak.restores_used = 0
        streak.restores_reset_date = today

    if streak.restores_used >= max_restores:
        raise HTTPException(status_code=400, detail=f"Лимит восстановлений ({max_restores}/мес)")

    streak.streak_days = max(streak.streak_days, 1)
    streak.restores_used += 1
    db.commit()

    return {"streak_days": streak.streak_days, "restores_used": streak.restores_used}

@router.put("/settings")
def update_gamification_settings(data: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    xp = db.query(UserXP).filter(UserXP.user_id == current_user.id).first()
    if not xp:
        xp = UserXP(user_id=current_user.id)
        db.add(xp)
        db.commit()
        db.refresh(xp)

    if "show_level" in data:
        xp.show_level = data["show_level"]
    if "show_achievements" in data:
        xp.show_achievements = data["show_achievements"]

    db.commit()
    return {"status": "ok"}
