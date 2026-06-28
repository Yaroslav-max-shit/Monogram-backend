from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from typing import List
import os
import shutil
import uuid
import json

from database import get_db
from models import User, UserSettings
from .auth import get_current_user

router = APIRouter(prefix="/settings", tags=["settings"])

DEFAULT_SETTINGS = {
    "theme": "dark",
    "language": "ru",
    "fontSize": 14,
    "notificationsEnabled": True,
    "soundEnabled": True,
    "wallpaper": "default",
}

@router.get("/")
def get_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    settings = db.query(UserSettings).filter(UserSettings.user_id == current_user.id).first()
    if not settings:
        return {
            **DEFAULT_SETTINGS,
            "muteWhenOnline": False,
            "smartNotifications": False,
            "whoCanSeePhoto": "all",
            "whoCanSeeBio": "all",
            "whoCanSeeLastSeen": "all",
            "whoCanAddToGroups": "all",
            "showStats": True,
            "customSounds": {},
        }
    return {
        "theme": settings.theme,
        "language": settings.language,
        "fontSize": settings.fontSize,
        "notificationsEnabled": settings.notifications_enabled,
        "soundEnabled": settings.sound_enabled,
        "wallpaper": settings.wallpaper,
        "muteWhenOnline": settings.mute_when_online,
        "smartNotifications": settings.smart_notifications,
        "whoCanSeePhoto": settings.who_can_see_photo,
        "whoCanSeeBio": settings.who_can_see_bio,
        "whoCanSeeLastSeen": settings.who_can_see_last_seen,
        "whoCanAddToGroups": settings.who_can_add_to_groups,
        "showStats": settings.show_stats,
        "customSounds": json.loads(settings.custom_sounds) if settings.custom_sounds else {},
    }

@router.put("/")
def update_settings(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    settings = db.query(UserSettings).filter(UserSettings.user_id == current_user.id).first()
    if not settings:
        settings = UserSettings(user_id=current_user.id)
        db.add(settings)

    if "theme" in data:
        settings.theme = data["theme"]
    if "language" in data:
        settings.language = data["language"]
    if "fontSize" in data:
        settings.fontSize = data["fontSize"]
    if "notificationsEnabled" in data:
        settings.notifications_enabled = data["notificationsEnabled"]
    if "soundEnabled" in data:
        settings.sound_enabled = data["soundEnabled"]
    if "wallpaper" in data:
        settings.wallpaper = data["wallpaper"]
    if "whoCanSeePhoto" in data:
        settings.who_can_see_photo = data["whoCanSeePhoto"]
    if "whoCanSeeBio" in data:
        settings.who_can_see_bio = data["whoCanSeeBio"]
    if "whoCanSeeLastSeen" in data:
        settings.who_can_see_last_seen = data["whoCanSeeLastSeen"]
    if "whoCanAddToGroups" in data:
        settings.who_can_add_to_groups = data["whoCanAddToGroups"]
    if "showStats" in data:
        settings.show_stats = data["showStats"]

    db.commit()
    return {"status": "ok"}

SOUND_DIR = "uploads/sounds"
os.makedirs(SOUND_DIR, exist_ok=True)

@router.post("/sound/{sound_type}")
def set_custom_sound(sound_type: str, file: UploadFile = File(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if sound_type not in ("message", "group", "call", "notification"):
        raise HTTPException(400, "Invalid sound type")
    path = os.path.join(SOUND_DIR, f"{current_user.id}_{sound_type}.wav")
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    settings = db.query(UserSettings).filter(UserSettings.user_id == current_user.id).first()
    if not settings:
        settings = UserSettings(user_id=current_user.id)
        db.add(settings)
    if not settings.custom_sounds:
        settings.custom_sounds = "{}"
    sounds = json.loads(settings.custom_sounds)
    sounds[sound_type] = path
    settings.custom_sounds = json.dumps(sounds)
    db.commit()
    return {"path": path}

@router.post("/smart-notifications")
def set_smart_notifications(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    settings = db.query(UserSettings).filter(UserSettings.user_id == current_user.id).first()
    if not settings:
        settings = UserSettings(user_id=current_user.id)
        db.add(settings)
    settings.mute_when_online = data.get("mute_when_online", False)
    settings.smart_notifications = data.get("smart_notifications", False)
    db.commit()
    return {"status": "ok"}

@router.post("/wallpaper")
def save_wallpaper(data: dict, current_user: User = Depends(get_current_user)):
    wallpaper = data.get("wallpaper", "default")
    return {"status": "ok", "wallpaper": wallpaper}

@router.get("/wallpapers")
def get_wallpapers():
    import os
    wallpaper_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "frontend", "public", "assets", "wallpapers")
    wallpapers = []
    if os.path.exists(wallpaper_dir):
        for f in os.listdir(wallpaper_dir):
            if f.endswith(('.png', '.jpg', '.jpeg')):
                name = os.path.splitext(f)[0]
                wallpapers.append(name)
    return wallpapers

@router.get("/cache-size")
def get_cache_size():
    return {"size": 0, "unit": "MB"}

@router.post("/sound")
def set_sound(data: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    settings = db.query(UserSettings).filter(UserSettings.user_id == current_user.id).first()
    if not settings:
        settings = UserSettings(user_id=current_user.id)
        db.add(settings)
    settings.sound_enabled = data.get("enabled", True)
    db.commit()
    return {"status": "ok"}

@router.post("/vibration")
def set_vibration(data: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    settings = db.query(UserSettings).filter(UserSettings.user_id == current_user.id).first()
    if not settings:
        settings = UserSettings(user_id=current_user.id)
        db.add(settings)
    db.commit()
    return {"status": "ok"}

@router.post("/animations")
def set_animations(data: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"status": "ok"}

@router.post("/font-size")
def set_font_size(data: dict, current_user: User = Depends(get_current_user)):
    return {"status": "ok"}