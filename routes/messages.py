from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.orm import Session, joinedload
from typing import Annotated
from database import get_db
from models import Message, Chat, User, Membership, ScheduledMessage, Notification, QuickReply
from schemas import MessageCreate, MessageResponse
from .auth import get_current_user
import json
import re
import os
from datetime import datetime, timedelta

router = APIRouter(prefix="/messages", tags=["messages"])

@router.post("/read")
def mark_read(
    request: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    message_ids = request.get("message_ids", [])
    if not message_ids:
        return {"status": "ok", "read_at": datetime.utcnow().isoformat()}
    now = datetime.utcnow()
    for mid in message_ids:
        msg = db.query(Message).filter(Message.id == mid).first()
        if msg:
            msg.read_at = now
    db.commit()
    return {"status": "ok", "read_at": now.isoformat()}

@router.delete("/{message_id}")
def delete_message(
    message_id: int,
    data: dict = {},
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    if msg.sender_id != current_user.id:
        membership = db.query(Membership).filter(Membership.user_id == current_user.id, Membership.chat_id == msg.chat_id, Membership.role.in_(["admin", "owner"])).first()
        if not membership:
            raise HTTPException(status_code=403, detail="Нет прав")
    msg.is_deleted = True
    db.commit()
    return {"status": "deleted"}

@router.put("/{message_id}")
def edit_message(
    message_id: int,
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    msg = db.query(Message).filter(Message.id == message_id, Message.sender_id == current_user.id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Сообщение не найдено или нет прав")
    new_content = data.get("content", "").strip()
    if not new_content:
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")
    msg.content = new_content
    msg.edited = True
    msg.edited_at = datetime.utcnow()
    db.commit()
    return {"id": msg.id, "content": msg.content, "edited": True}

@router.post("/forward")
def forward_message(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Support both: { message_id, chat_ids } and { message_ids: [...], target_chat_id }
    message_ids = data.get("message_ids") or ([data.get("message_id")] if data.get("message_id") else [])
    target_chat_ids = data.get("chat_ids") or ([data.get("target_chat_id")] if data.get("target_chat_id") else [])
    if not message_ids or not target_chat_ids:
        raise HTTPException(status_code=400, detail="Нужны message_ids и target_chat_id")
    if not isinstance(message_ids, list):
        message_ids = [message_ids]
    if not isinstance(target_chat_ids, list):
        target_chat_ids = [target_chat_ids]
    forwarded = []
    for msg_id in message_ids:
        original = db.query(Message).filter(Message.id == msg_id).first()
        if not original:
            continue
        # Check membership in source chat
        src_membership = db.query(Membership).filter(
            Membership.user_id == current_user.id,
            Membership.chat_id == original.chat_id
        ).first()
        if not src_membership:
            continue
        for cid in target_chat_ids:
            membership = db.query(Membership).filter(Membership.user_id == current_user.id, Membership.chat_id == cid).first()
            if not membership:
                continue
            new_msg = Message(
                content=json.dumps({"type": "forwarded", "original_content": original.content, "original_sender_id": original.sender_id}),
                sender_id=current_user.id,
                chat_id=cid,
                is_forwarded=True
            )
            db.add(new_msg)
            db.flush()
            forwarded.append(new_msg.id)
    db.commit()
    return {"status": "ok", "message_ids": forwarded}


# ============================================
# Реакции
# ============================================

@router.get("/{message_id}/forwarded-by")
def get_forwarded_by(message_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    forwards = db.query(Message).options(
        joinedload(Message.sender)
    ).filter(
        Message.forwarded_from_message_id == message_id
    ).all()
    return [{
        "user_id": m.sender_id,
        "username": m.sender.username,
        "first_name": m.sender.first_name,
        "timestamp": m.timestamp.isoformat() if m.timestamp else None
    } for m in forwards]

@router.post("/react")
def add_reaction(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    message_id = data.get("message_id")
    emoji = data.get("emoji", "👍")
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Сообщение не найдено")
    reactions = json.loads(msg.reactions_json or "{}")
    if str(current_user.id) in reactions:
        if reactions[str(current_user.id)] == emoji:
            del reactions[str(current_user.id)]
            msg.reactions_json = json.dumps(reactions)
            db.commit()
            return {"status": "removed"}
    reactions[str(current_user.id)] = emoji
    msg.reactions_json = json.dumps(reactions)
    db.commit()
    return {"status": "reacted", "emoji": emoji}

@router.get("/{chat_id}/pinned")
def get_pinned_messages(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    chat = db.query(Chat).filter(Chat.id == chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Чат не найден")
    membership = db.query(Membership).filter(Membership.user_id == current_user.id, Membership.chat_id == chat_id).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Нет доступа к чату")
    if not chat.pinned_message_id:
        return []
    msg = db.query(Message).filter(Message.id == chat.pinned_message_id).first()
    if not msg:
        return []
    return [{
        "id": msg.id,
        "content": msg.content,
        "sender_id": msg.sender_id,
        "timestamp": msg.timestamp.isoformat() if msg.timestamp else None
    }]

@router.get("/media/{chat_id}")
def get_chat_media(
    chat_id: int,
    offset: int = 0,
    limit: int = 30,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this chat")

    media_msgs = db.query(Message).options(
        joinedload(Message.sender)
    ).filter(
        Message.chat_id == chat_id,
        Message.is_deleted == False
    ).order_by(Message.timestamp.desc()).offset(offset).limit(limit).all()

    result = []
    for m in media_msgs:
        try:
            parsed = json.loads(m.content)
            if isinstance(parsed, dict) and parsed.get("type") in ("file", "photo", "video", "audio", "document", "animation", "voice", "video_note", "sticker"):
                media_type = parsed.get("type", "file")
                if media_type in ("photo", "image"):
                    media_type = "image"
                elif media_type in ("video", "animation", "video_note"):
                    media_type = "video"
                elif media_type in ("audio", "voice"):
                    media_type = "audio"
                else:
                    media_type = "file"
                result.append({
                    "id": m.id,
                    "message_id": m.id,
                    "sender": {
                        "id": m.sender.id,
                        "username": m.sender.username,
                        "first_name": m.sender.first_name,
                        "avatar_url": m.sender.avatar_url
                    },
                    "sender_id": m.sender_id,
                    "senderName": m.sender.username,
                    "timestamp": m.timestamp.isoformat(),
                    "content": m.content,
                    "type": media_type,
                    "url": parsed.get("file") or parsed.get("file_url") or parsed.get("url", ""),
                    "fileName": parsed.get("file_name") or parsed.get("caption", "Untitled"),
                    "fileSize": parsed.get("file_size", 0),
                    "file_type": parsed.get("file_type", ""),
                    "duration": parsed.get("duration"),
                    "width": parsed.get("width"),
                    "height": parsed.get("height"),
                })
        except (json.JSONDecodeError, TypeError):
            pass

    return {"items": result}

@router.post("/upload")
async def upload_files(
    chat_id: int = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    files: list[UploadFile] = File(...),
):
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Нет доступа к чату")
    
    import os
    upload_dir = os.path.join("uploads", "files")
    os.makedirs(upload_dir, exist_ok=True)
    
    saved_files = []
    for file in files:
        filename = f"{current_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
        filepath = os.path.join(upload_dir, filename)
        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)
        saved_files.append({"url": f"/uploads/files/{filename}", "name": file.filename})
    
    content = json.dumps({"type": "file", "files": saved_files})
    db_message = Message(content=content, sender_id=current_user.id, chat_id=chat_id)
    db.add(db_message)
    db.commit()
    db.refresh(db_message)
    
    return {"status": "ok", "message_id": db_message.id, "files": saved_files}

@router.post("/upload-audio")
async def upload_audio(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    audio: UploadFile = File(...),
    chat_id: int = Form(...),
    duration: int = Form(0),
):
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Нет доступа к чату")
    
    upload_dir = os.path.join("uploads", "audio")
    os.makedirs(upload_dir, exist_ok=True)
    
    filename = f"{current_user.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.webm"
    filepath = os.path.join(upload_dir, filename)
    
    content = await audio.read()
    with open(filepath, "wb") as f:
        f.write(content)
    
    audio_content = json.dumps({
        "type": "voice",
        "duration": duration,
        "url": f"/uploads/audio/{filename}"
    })
    
    db_message = Message(
        content=audio_content,
        sender_id=current_user.id,
        chat_id=chat_id
    )
    db.add(db_message)
    db.commit()
    db.refresh(db_message)
    
    return {"status": "ok", "message_id": db_message.id, "url": f"/uploads/audio/{filename}"}

@router.post("/", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def send_message(
    message: MessageCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    db_chat = db.query(Chat).filter(Chat.id == message.chat_id).first()
    if not db_chat:
        db_chat = Chat(id=message.chat_id, type="private", name=f"Чат {message.chat_id}")
        db.add(db_chat)
        db.commit()
        db.refresh(db_chat)
        
        membership = Membership(user_id=current_user.id, chat_id=message.chat_id, role="member")
        db.add(membership)
        db.commit()
    
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == message.chat_id
    ).first()
    if not membership:
        membership = Membership(user_id=current_user.id, chat_id=message.chat_id, role="member")
        db.add(membership)
        db.commit()
    
    if db_chat.type == 'channel' and membership.role not in ('admin', 'owner'):
        raise HTTPException(status_code=403, detail="В канале могут писать только создатель и администраторы")
    
    db_message = Message(
        content=message.content,
        sender_id=current_user.id,
        chat_id=message.chat_id
    )
    db.add(db_message)
    db.commit()
    db.refresh(db_message)
    
    # Broadcast to all users in the chat via SSE
    members = db.query(Membership).filter(Membership.chat_id == message.chat_id).all()
    for m in members:
        if m.user_id != current_user.id:
            try:
                import asyncio
                from main import send_sse_event
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(send_sse_event(m.user_id, {
                        "type": "new_message",
                        "message_id": db_message.id,
                        "chat_id": message.chat_id,
                        "sender_id": current_user.id,
                        "content": message.content,
                        "timestamp": db_message.timestamp.isoformat(),
                    }))
                else:
                    loop.run_until_complete(send_sse_event(m.user_id, {
                        "type": "new_message",
                        "message_id": db_message.id,
                        "chat_id": message.chat_id,
                        "sender_id": current_user.id,
                        "content": message.content,
                        "timestamp": db_message.timestamp.isoformat(),
                    }))
            except Exception:
                pass
    
    return db_message

@router.get("/chat/{chat_id}", response_model=list[MessageResponse])
def get_messages(
    chat_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)]
):
    # Check if user is member of chat
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this chat")
    
    messages = db.query(Message).options(
        joinedload(Message.sender)
    ).filter(Message.chat_id == chat_id).order_by(Message.timestamp).all()
    return messages


# ============================================
# @Mentions
# ============================================

@router.post("/mention")
def mention_user(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    content = data.get("content", "")
    chat_id = data.get("chat_id")
    mentions = re.findall(r'@(\w+)', content)
    notified = []
    for username in mentions:
        user = db.query(User).filter(User.username == username).first()
        if user and user.id != current_user.id:
            notification = Notification(
                user_id=user.id,
                type="mention",
                message=f"{current_user.username} упомянул вас в чате",
                from_user_id=current_user.id,
                chat_id=chat_id
            )
            db.add(notification)
            notified.append(user.id)
    db.commit()
    return {"status": "ok", "mentioned_users": notified}


# ============================================
# Scheduled Messages
# ============================================

@router.post("/schedule")
def schedule_message(
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    schedule = ScheduledMessage(
        content=data["content"],
        sender_id=current_user.id,
        chat_id=data["chat_id"],
        scheduled_for=datetime.fromisoformat(data["scheduled_for"])
    )
    db.add(schedule)
    db.commit()
    return {"status": "ok", "id": schedule.id}


@router.get("/scheduled")
def get_scheduled_messages(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    now = datetime.utcnow()
    messages = db.query(ScheduledMessage).filter(
        ScheduledMessage.sender_id == current_user.id,
        ScheduledMessage.status == "pending",
        ScheduledMessage.scheduled_for > now
    ).order_by(ScheduledMessage.scheduled_for).all()
    return messages


# ============================================
# Slash Commands
# ============================================

@router.post("/chats/{chat_id}/command")
def execute_command(
    chat_id: int,
    data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    command_text = data.get("text", "")
    membership = db.query(Membership).filter(
        Membership.user_id == current_user.id,
        Membership.chat_id == chat_id
    ).first()
    if not membership:
        raise HTTPException(403, "Not a member")

    parts = command_text.split()
    cmd = parts[0].lower()

    if cmd == "/mute":
        if membership.role not in ("admin", "owner"):
            return {"status": "error", "message": "Only admins can mute"}
        target = parts[1].lstrip("@") if len(parts) > 1 else None
        target_user = db.query(User).filter(User.username == target).first() if target else None
        if target_user:
            target_membership = db.query(Membership).filter(
                Membership.user_id == target_user.id,
                Membership.chat_id == chat_id
            ).first()
            if target_membership:
                target_membership.muted_until = datetime.utcnow() + timedelta(days=365)
                db.commit()
                return {"status": "ok", "action": "muted", "user": target_user.username}
        return {"status": "error", "message": "User not found"}

    elif cmd == "/unmute":
        if membership.role not in ("admin", "owner"):
            return {"status": "error", "message": "Only admins can unmute"}
        target = parts[1].lstrip("@") if len(parts) > 1 else None
        target_user = db.query(User).filter(User.username == target).first() if target else None
        if target_user:
            target_membership = db.query(Membership).filter(
                Membership.user_id == target_user.id,
                Membership.chat_id == chat_id
            ).first()
            if target_membership:
                target_membership.muted_until = None
                db.commit()
                return {"status": "ok", "action": "unmuted", "user": target_user.username}
        return {"status": "error", "message": "User not found"}

    elif cmd == "/kick":
        if membership.role not in ("admin", "owner"):
            return {"status": "error", "message": "Only admins can kick"}
        target = parts[1].lstrip("@") if len(parts) > 1 else None
        target_user = db.query(User).filter(User.username == target).first() if target else None
        if target_user:
            target_membership = db.query(Membership).filter(
                Membership.user_id == target_user.id,
                Membership.chat_id == chat_id
            ).first()
            if target_membership:
                db.delete(target_membership)
                db.commit()
                return {"status": "ok", "action": "kicked", "user": target_user.username}
        return {"status": "error", "message": "User not found"}

    elif cmd == "/ban":
        if membership.role not in ("admin", "owner"):
            return {"status": "error", "message": "Only admins can ban"}
        target = parts[1].lstrip("@") if len(parts) > 1 else None
        target_user = db.query(User).filter(User.username == target).first() if target else None
        if target_user:
            target_membership = db.query(Membership).filter(
                Membership.user_id == target_user.id,
                Membership.chat_id == chat_id
            ).first()
            if target_membership:
                members_name = target_membership
                db.delete(members_name)
                db.commit()
                return {"status": "ok", "action": "banned", "user": target_user.username}
        return {"status": "error", "message": "User not found"}

    elif cmd == "/pin":
        if membership.role not in ("admin", "owner"):
            return {"status": "error", "message": "Only admins can pin"}
        last_msg = db.query(Message).filter(Message.chat_id == chat_id).order_by(Message.timestamp.desc()).first()
        if last_msg:
            chat = db.query(Chat).filter(Chat.id == chat_id).first()
            if chat:
                chat.pinned_message_id = last_msg.id
                db.commit()
                return {"status": "ok", "action": "pinned", "message_id": last_msg.id}
        return {"status": "error", "message": "No messages to pin"}

    elif cmd == "/admin":
        target = parts[1].lstrip("@") if len(parts) > 1 else None
        if membership.role != "owner":
            return {"status": "error", "message": "Only owner can assign admin"}
        target_user = db.query(User).filter(User.username == target).first() if target else None
        if target_user:
            target_membership = db.query(Membership).filter(
                Membership.user_id == target_user.id,
                Membership.chat_id == chat_id
            ).first()
            if target_membership:
                target_membership.role = "admin"
                db.commit()
                return {"status": "ok", "action": "admin_set", "user": target_user.username}
        return {"status": "error", "message": "User not found"}

    return {"status": "unknown_command"}


# ============================================
# Notifications
# ============================================

@router.get("/notifications")
def get_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    notifications = db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False
    ).order_by(Notification.created_at.desc()).all()
    return notifications


@router.post("/notifications/read/{notification_id}")
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user.id
    ).first()
    if not notification:
        raise HTTPException(404, "Notification not found")
    notification.is_read = True
    db.commit()
    return {"status": "ok"}

@router.post("/{message_id}/pin")
def pin_message(message_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(404, "Сообщение не найдено")
    membership = db.query(Membership).filter(Membership.user_id == current_user.id, Membership.chat_id == msg.chat_id, Membership.role.in_(["admin", "owner"])).first()
    if not membership:
        raise HTTPException(403, "Нет прав")
    msg.is_pinned = not msg.is_pinned
    db.commit()
    return {"is_pinned": msg.is_pinned}

@router.get("/search")
def search_messages(q: str, chat_id: int = None, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    query = db.query(Message).filter(Message.content.ilike(f"%{q}%"), Message.is_deleted == False)
    if chat_id:
        query = query.filter(Message.chat_id == chat_id)
    else:
        user_chat_ids = [m.chat_id for m in db.query(Membership).filter(Membership.user_id == current_user.id).all()]
        query = query.filter(Message.chat_id.in_(user_chat_ids))
    messages = query.order_by(Message.timestamp.desc()).limit(50).all()
    return [{"id": m.id, "content": m.content, "chat_id": m.chat_id, "sender_id": m.sender_id, "timestamp": m.timestamp.isoformat()} for m in messages]

@router.post("/schedule")
def schedule_message(data: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from datetime import datetime
    content = data.get("content", "")
    chat_id = data.get("chat_id")
    send_at_str = data.get("send_at")
    if not content or not chat_id or not send_at_str:
        raise HTTPException(400, "Заполните все поля")
    send_at = datetime.fromisoformat(send_at_str)
    msg = Message(content=content, sender_id=current_user.id, chat_id=chat_id, scheduled_for=send_at)
    db.add(msg)
    db.commit()
    return {"status": "scheduled", "send_at": send_at.isoformat()}

@router.post("/{message_id}/auto-delete")
def set_auto_delete(message_id: int, data: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    msg = db.query(Message).filter(Message.id == message_id, Message.sender_id == current_user.id).first()
    if not msg:
        raise HTTPException(404, "Сообщение не найдено")
    seconds = data.get("seconds", 60)
    from datetime import timedelta
    msg.auto_delete_at = datetime.utcnow() + timedelta(seconds=seconds)
    db.commit()
    return {"status": "ok", "auto_delete_at": msg.auto_delete_at.isoformat()}

@router.get("/quick-replies")
def get_quick_replies(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    replies = db.query(QuickReply).filter(QuickReply.user_id == current_user.id).all()
    return [{"id": r.id, "shortcut": r.shortcut, "text": r.text, "category": r.category} for r in replies]

@router.post("/quick-replies")
def create_quick_reply(data: dict, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    shortcut = data.get("shortcut", "")
    text = data.get("text", "")
    category = data.get("category", "general")
    if not shortcut or not text:
        raise HTTPException(400, "Заполните все поля")
    reply = QuickReply(user_id=current_user.id, shortcut=shortcut, text=text, category=category)
    db.add(reply)
    db.commit()
    db.refresh(reply)
    return {"id": reply.id, "shortcut": reply.shortcut, "text": reply.text, "category": reply.category}

@router.delete("/quick-replies/{reply_id}")
def delete_quick_reply(reply_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reply = db.query(QuickReply).filter(QuickReply.id == reply_id, QuickReply.user_id == current_user.id).first()
    if not reply:
        raise HTTPException(404, "Шаблон не найден")
    db.delete(reply)
    db.commit()
    return {"status": "deleted"}
