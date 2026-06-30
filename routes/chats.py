from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import text
from typing import Annotated, List
from database import get_db
from models import Chat, Membership, User, Message, Folder
from schemas import ChatCreate, ChatResponse
from .auth import get_current_user
import logging
import secrets
import json
from datetime import datetime
from fastapi.responses import JSONResponse, HTMLResponse, Response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chats", tags=["chats"])

@router.post("/typing")
def typing_indicator(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    chat_id = data.get("chat_id")
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id обязателен")
    membership = db.query(Membership).filter(Membership.user_id == current_user.id, Membership.chat_id == chat_id).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Нет доступа")
    return {"status": "typing", "user_id": current_user.id, "chat_id": chat_id}

@router.get("/online/{user_id}")
def check_online(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    is_online = user.last_login and (datetime.utcnow() - user.last_login).total_seconds() < 120
    return {"is_online": is_online, "last_seen": user.last_login.isoformat() if user.last_login else None}

@router.post("/pin")
def pin_message(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    chat_id = data.get("chat_id")
    message_id = data.get("message_id")
    if not chat_id or not message_id:
        raise HTTPException(status_code=400, detail="chat_id и message_id обязательны")
    membership = db.query(Membership).filter(Membership.user_id == current_user.id, Membership.chat_id == chat_id).first()
    if not membership or membership.role not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Нет прав")
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if chat:
        chat.pinned_message_id = message_id
        db.commit()
    return {"status": "pinned", "message_id": message_id}

@router.delete("/pin/{chat_id}")
def unpin_message(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    membership = db.query(Membership).filter(Membership.user_id == current_user.id, Membership.chat_id == chat_id).first()
    if not membership or membership.role not in ["admin", "owner"]:
        raise HTTPException(status_code=403, detail="Нет прав")
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if chat:
        chat.pinned_message_id = None
        db.commit()
    return {"status": "unpinned"}

@router.get("/", response_model=List[ChatResponse])
def get_user_chats(
    db: Annotated[Session, Depends(get_db)], 
    current_user: Annotated[User, Depends(get_current_user)]
):
    memberships = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.is_archived == False
    ).all()
    
    chat_ids = [m.chat_id for m in memberships]
    system_chat_ids = [999998, 999999]
    
    for sys_id in system_chat_ids:
        if sys_id not in chat_ids:
            chat_ids.append(sys_id)
    
    chats = db.query(Chat).filter(Chat.id.in_(chat_ids)).all()
    
    result = []
    for chat in chats:
        chat_data = {
            "id": chat.id,
            "type": chat.type,
            "name": chat.name,
            "description": chat.description,
            "created_at": chat.created_at,
        }
        
        if chat.type == "private" and chat.name and chat.name.startswith("Чат "):
            other_membership = db.query(Membership).filter(
                Membership.chat_id == chat.id,
                Membership.user_id != current_user.id
            ).first()
            if other_membership:
                other_user = db.query(User).filter(User.id == other_membership.user_id).first()
                if other_user:
                    name_parts = []
                    if other_user.first_name:
                        name_parts.append(other_user.first_name)
                    if other_user.last_name:
                        name_parts.append(other_user.last_name)
                    chat_data["name"] = " ".join(name_parts) if name_parts else other_user.username
                    if other_user.avatar_url:
                        chat_data["avatar_url"] = other_user.avatar_url
        
        result.append(chat_data)
    
    return result

@router.get("/archived")
def get_archived_chats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    memberships = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.is_archived == True
    ).all()
    
    chat_ids = [m.chat_id for m in memberships]
    chats = db.query(Chat).filter(Chat.id.in_(chat_ids)).all()
    return chats

@router.post("/{chat_id}/archive")
def archive_chat(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    
    if not membership:
        raise HTTPException(404, "Чат не найден")
    
    membership.is_archived = True
    db.commit()
    return {"status": "archived"}

@router.post("/{chat_id}/unarchive")
def unarchive_chat(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    
    if not membership:
        raise HTTPException(404, "Чат не найден")
    
    membership.is_archived = False
    db.commit()
    return {"status": "unarchived"}

@router.post("/{chat_id}/mute")
def mute_chat(
    chat_id: int,
    duration_minutes: int = 60,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    
    if not membership:
        raise HTTPException(404, "Чат не найден")
    
    from datetime import datetime, timedelta
    membership.muted_until = datetime.utcnow() + timedelta(minutes=duration_minutes)
    db.commit()
    return {"muted_until": membership.muted_until}

@router.post("/{chat_id}/unmute")
def unmute_chat(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    
    if not membership:
        raise HTTPException(404, "Чат не найден")
    
    membership.muted_until = None
    db.commit()
    return {"status": "unmuted"}

@router.post("/{chat_id}/sound")
def set_chat_sound(
    chat_id: int,
    sound: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    
    if not membership:
        raise HTTPException(404, "Чат не найден")
    
    membership.custom_notification_sound = sound
    db.commit()
    return {"sound": sound}

@router.get("/{chat_id}", response_model=ChatResponse)
def get_chat(
    chat_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Нет доступа к чату")
    return chat

@router.post("/", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
def create_chat(
    chat: ChatCreate, 
    db: Annotated[Session, Depends(get_db)], 
    current_user: Annotated[User, Depends(get_current_user)]
):
    db_chat = Chat(
        type=chat.type, 
        name=chat.name or f"Чат {current_user.username}", 
        description=chat.description or ""
    )
    db.add(db_chat)
    db.commit()
    db.refresh(db_chat)
    
    membership = Membership(
        user_id=current_user.id, 
        chat_id=db_chat.id, 
        role="owner"
    )
    db.add(membership)
    db.commit()
    
    return db_chat

@router.post("/{chat_id}/join")
def join_chat(
    chat_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")
    
    existing = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    
    if existing:
        return {"message": "Вы уже в этом чате", "chat_id": chat_id}
    
    membership = Membership(
        user_id=current_user.id,
        chat_id=chat_id,
        role="member"
    )
    db.add(membership)
    db.commit()
    
    return {"message": "Вы присоединились к чату", "chat_id": chat_id}

@router.delete("/{chat_id}/leave")
def leave_chat(
    chat_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    
    if not membership:
        raise HTTPException(status_code=404, detail="Вы не в этом чате")
    
    db.delete(membership)
    db.commit()
    
    return {"message": "Вы покинули чат", "chat_id": chat_id}

@router.get("/{chat_id}/members")
def get_chat_members(
    chat_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")
    
    membership_check = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    if not membership_check:
        raise HTTPException(status_code=403, detail="Нет доступа к чату")
    
    memberships = db.query(Membership).filter(Membership.chat_id == chat_id).all()
    user_ids = [m.user_id for m in memberships]
    users = db.query(User).filter(User.id.in_(user_ids)).all()
    
    result = []
    for membership in memberships:
        user = next((u for u in users if u.id == membership.user_id), None)
        if user:
            result.append({
                "user_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "avatar_url": user.avatar_url,
                "role": membership.role,
                "joined_at": membership.joined_at.isoformat() if membership.joined_at else None
            })
    
    return result

@router.post("/{chat_id}/wallpaper")
def set_chat_wallpaper(
    chat_id: int,
    wallpaper: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(404, "Чат не найден")
    
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    if not membership:
        raise HTTPException(403, "Нет доступа к чату")
    
    chat.wallpaper = wallpaper
    db.commit()
    return {"wallpaper": wallpaper}

@router.get("/{chat_id}/export/{format}")
def export_chat(
    chat_id: int,
    format: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    
    if not membership:
        raise HTTPException(403, "Нет доступа к чату")
    
    import html as html_lib
    
    messages = db.query(Message).filter(
        Message.chat_id == chat_id,
        Message.is_deleted == False
    ).order_by(Message.timestamp).all()
    
    if format == "json":
        data = [
            {
                "id": m.id,
                "sender_id": m.sender_id,
                "content": m.content,
                "timestamp": m.timestamp.isoformat()
            }
            for m in messages
        ]
        return JSONResponse(content=data)
    
    elif format == "html":
        html = """<html>
        <head><meta charset="UTF-8"><title>Экспорт чата</title></head>
        <body><h1>Экспорт чата</h1>"""
        for m in messages:
            safe_content = html_lib.escape(m.content)
            html += f"<div><b>{html_lib.escape(str(m.sender_id))}</b>: {safe_content} <small>{m.timestamp}</small></div>"
        html += "</body></html>"
        return HTMLResponse(content=html)
    
    elif format == "txt":
        text = "\n".join([f"{m.timestamp} - {m.sender_id}: {m.content}" for m in messages])
        return Response(content=text, media_type="text/plain")
    
    raise HTTPException(400, "Неверный формат")


# ============================================
# Invite Links
# ============================================

@router.post("/{chat_id}/invite-link")
def generate_invite_link(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(404, "Chat not found")
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id,
        Membership.role.in_(["owner", "admin"])
    ).first()
    if not membership:
        raise HTTPException(403, "Not authorized")
    invite = secrets.token_urlsafe(16)
    chat.invite_link = invite
    db.commit()
    return {"invite_link": invite}


@router.post("/join/{invite_link}")
def join_by_invite(
    invite_link: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    chat = db.query(Chat).filter(Chat.invite_link == invite_link).first()
    if not chat:
        raise HTTPException(404, "Invalid invite link")
    existing = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat.id
    ).first()
    if existing:
        raise HTTPException(400, "Already a member")
    db.add(Membership(user_id=current_user.id, chat_id=chat.id, role="member"))
    db.commit()
    return {"chat_id": chat.id, "name": chat.name}


@router.post("/{chat_id}/auto-delete")
def set_auto_delete(chat_id: int, data: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(404)
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id,
        Membership.role.in_(["owner", "admin"])
    ).first()
    if not membership:
        raise HTTPException(403, "Только администраторы могут настроить авто-удаление")
    chat.auto_delete_after = data.get("seconds", 0)
    db.commit()
    return {"status": "ok", "auto_delete_after": chat.auto_delete_after}

@router.get("/{chat_id}/stats/referral")
def get_referral_stats(chat_id: int, period: str = "day", db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from datetime import timedelta
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(404)
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id,
        Membership.role.in_(["owner", "admin"])
    ).first()
    if not membership:
        raise HTTPException(403)
    now = datetime.utcnow()
    period_map = {
        "hour": timedelta(hours=1),
        "day": timedelta(days=1),
        "week": timedelta(weeks=1),
        "month": timedelta(days=30),
        "6months": timedelta(days=180),
        "year": timedelta(days=365),
        "2years": timedelta(days=730),
        "5years": timedelta(days=1825),
    }
    delta = period_map.get(period, timedelta(days=1))
    since = now - delta
    joined = db.query(Membership).filter(
        Membership.chat_id == chat_id,
        Membership.joined_at >= since
    ).count()
    return {
        "period": period,
        "subs_total": db.query(Membership).filter(Membership.chat_id == chat_id).count(),
        "subs_gained": joined,
        "subs_lost": 0,
        "subs_by_search": 0,
        "subs_by_link": 0,
        "subs_by_referral": 0,
        "earnings": "0",
    }

@router.get("/{chat_id}/media")
def get_chat_media(
    chat_id: int,
    type: str = "all",
    offset: int = 0,
    limit: int = 20,
    sort_by: str = "date",
    sort_order: str = "desc",
    search: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this chat")

    query = db.query(Message).options(
        joinedload(Message.sender)
    ).filter(
        Message.chat_id == chat_id,
        Message.is_deleted == False
    )

    media_types = {"all", "images", "videos", "files", "image", "video", "audio", "file"}
    if type != "all" and type in media_types:
        type_filter = type.rstrip("s")
        query = query.filter(Message.content.like(f'%"{type_filter}"%'))

    if sort_by == "date":
        order_col = Message.timestamp
    elif sort_by == "size":
        order_col = Message.id
    else:
        order_col = Message.timestamp

    if sort_order == "asc":
        query = query.order_by(order_col.asc())
    else:
        query = query.order_by(order_col.desc())

    messages = query.offset(offset).limit(limit).all()

    result = []
    for m in messages:
        try:
            parsed = json.loads(m.content)
            if isinstance(parsed, dict) and parsed.get("type") in ("file", "photo", "video", "audio", "document", "animation", "voice", "video_note", "sticker", "image"):
                media_type = parsed.get("type", "file")
                if media_type in ("photo", "image"):
                    media_type = "image"
                elif media_type in ("video", "animation", "video_note"):
                    media_type = "video"
                elif media_type in ("audio", "voice"):
                    media_type = "audio"
                else:
                    media_type = "file"

                file_url = parsed.get("file") or parsed.get("file_url") or parsed.get("url", "")
                file_name = parsed.get("file_name") or f"file_{m.id}"
                file_size = parsed.get("file_size", 0)
                file_ext = file_url.split(".")[-1].lower() if "." in file_url else ""
                file_type = parsed.get("file_type", f"application/{file_ext}" if file_ext else "application/octet-stream")

                result.append({
                    "id": m.id,
                    "type": media_type,
                    "url": file_url,
                    "fileName": file_name,
                    "fileSize": file_size,
                    "file_type": file_type,
                    "timestamp": m.timestamp.isoformat(),
                    "senderName": m.sender.username if m.sender else "Unknown",
                    "senderId": m.sender_id,
                    "messageId": m.id,
                    "duration": parsed.get("duration"),
                    "width": parsed.get("width"),
                    "height": parsed.get("height"),
                })
        except (json.JSONDecodeError, TypeError):
            if search:
                if search.lower() in m.content.lower():
                    result.append({
                        "id": m.id,
                        "type": "file",
                        "url": "",
                        "fileName": "Text message",
                        "fileSize": 0,
                        "file_type": "text/plain",
                        "timestamp": m.timestamp.isoformat(),
                        "senderName": m.sender.username if m.sender else "Unknown",
                        "senderId": m.sender_id,
                        "messageId": m.id,
                    })

    return {"items": result}

@router.get("/folders")
def get_folders(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    folders = db.query(Folder).filter(Folder.user_id == current_user.id).all()
    import json
    return [{"id": f.id, "name": f.name, "chat_ids": json.loads(f.chat_ids), "icon": f.icon} for f in folders]

@router.post("/folders")
def create_folder(data: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    import json
    name = data.get("name", "Новая папка")
    chat_ids = json.dumps(data.get("chat_ids", []))
    icon = data.get("icon", "folder")
    folder = Folder(user_id=current_user.id, name=name, chat_ids=chat_ids, icon=icon)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return {"id": folder.id, "name": folder.name, "icon": folder.icon}
