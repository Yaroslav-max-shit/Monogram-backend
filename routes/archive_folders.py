from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
import json
from database import get_db
from models import User, Archive, Folder, Chat, Membership
from .auth import get_current_user

router = APIRouter(prefix="", tags=["archive_folders"])

@router.post("/archive/add")
def archive_chat(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    chat_id = data.get("chat_id")
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id обязателен")
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Нет доступа к чату")
    existing = db.query(Archive).filter(Archive.user_id == current_user.id, Archive.chat_id == chat_id).first()
    if not existing:
        db.add(Archive(user_id=current_user.id, chat_id=chat_id))
        db.commit()
    return {"status": "archived"}

@router.post("/archive/remove")
def unarchive_chat(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    chat_id = data.get("chat_id")
    db.query(Archive).filter(Archive.user_id == current_user.id, Archive.chat_id == chat_id).delete()
    db.commit()
    return {"status": "unarchived"}

@router.get("/archive/list")
def get_archive(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    archived = db.query(Archive).filter(Archive.user_id == current_user.id).all()
    chat_ids = [a.chat_id for a in archived]
    chats = db.query(Chat).filter(Chat.id.in_(chat_ids)).all()
    return {"chats": [{"id": c.id, "title": c.name, "type": c.type} for c in chats]}

@router.post("/folders/create")
def create_folder(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    name = data.get("name", "Новая папка")
    chat_ids = json.dumps(data.get("chat_ids", []))
    icon = data.get("icon", "folder")
    folder = Folder(user_id=current_user.id, name=name, chat_ids=chat_ids, icon=icon)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return {"id": folder.id, "name": folder.name, "icon": folder.icon}

@router.get("/folders/list")
def list_folders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    folders = db.query(Folder).filter(Folder.user_id == current_user.id).all()
    return {"folders": [{"id": f.id, "name": f.name, "chat_ids": json.loads(f.chat_ids), "icon": f.icon} for f in folders]}

@router.put("/folders/{folder_id}")
def update_folder(
    folder_id: int,
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    folder = db.query(Folder).filter(Folder.id == folder_id, Folder.user_id == current_user.id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Папка не найдена")
    if "name" in data:
        folder.name = data["name"]
    if "chat_ids" in data:
        folder.chat_ids = json.dumps(data["chat_ids"])
    if "icon" in data:
        folder.icon = data["icon"]
    db.commit()
    return {"status": "updated"}

@router.delete("/folders/{folder_id}")
def delete_folder(
    folder_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    db.query(Folder).filter(Folder.id == folder_id, Folder.user_id == current_user.id).delete()
    db.commit()
    return {"status": "deleted"}
