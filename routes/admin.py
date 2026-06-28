from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from database import get_db
from models import User, Chat, Message, Membership, Admin, Profile
from .auth import get_current_user

router = APIRouter(prefix="/admin", tags=["admin"])

def check_admin(current_user: User, db: Session):
    admin = db.query(Admin).filter(Admin.user_id == current_user.id).first()
    if not admin:
        raise HTTPException(status_code=403, detail="Р”РѕСЃС‚СѓРї Р·Р°РїСЂРµС‰С‘РЅ")
    return True

@router.get("/stats")
def get_stats(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    check_admin(current_user, db)
    return {
        "total_users": db.query(User).count(),
        "total_chats": db.query(Chat).count(),
        "total_messages": db.query(Message).count(),
        "active_today": db.query(User).filter(User.last_login >= datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)).count()
    }

@router.get("/users")
def get_users(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    check_admin(current_user, db)
    return [{"id": u.id, "username": u.username, "email": u.email, "first_name": u.first_name, "last_name": u.last_name, "avatar_url": u.avatar_url, "is_active": u.is_active, "created_at": u.created_at.isoformat() if u.created_at else None} for u in db.query(User).all()]

@router.get("/chats")
def get_chats(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    check_admin(current_user, db)
    return [{"id": c.id, "type": c.type if hasattr(c, 'type') else "private", "name": c.name, "members_count": db.query(Membership).filter(Membership.chat_id == c.id).count(), "messages_count": db.query(Message).filter(Message.chat_id == c.id).count()} for c in db.query(Chat).all()]

@router.get("/admins")
def get_admins(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    check_admin(current_user, db)
    ids = [a.user_id for a in db.query(Admin).all()]
    return [{"id": u.id, "username": u.username, "first_name": u.first_name, "last_name": u.last_name, "avatar_url": u.avatar_url} for u in db.query(User).filter(User.id.in_(ids)).all()] if ids else []

@router.post("/admins/{user_id}")
def add_admin(user_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    check_admin(current_user, db)
    if not db.query(Admin).filter(Admin.user_id == user_id).first():
        db.add(Admin(user_id=user_id, added_by=current_user.id))
        db.commit()
    return {"status": "ok"}

@router.delete("/admins/{user_id}")
def remove_admin(user_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    check_admin(current_user, db)
    db.query(Admin).filter(Admin.user_id == user_id).delete()
    db.commit()
    return {"status": "ok"}

@router.post("/admins/by-username")
def add_admin_by_username(data: dict, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    check_admin(current_user, db)
    user = db.query(User).filter(User.username == data.get("username")).first()
    if not user:
        raise HTTPException(status_code=404, detail="РџРѕР»СЊР·РѕРІР°С‚РµР»СЊ РЅРµ РЅР°Р№РґРµРЅ")
    if not db.query(Admin).filter(Admin.user_id == user.id).first():
        db.add(Admin(user_id=user.id, added_by=current_user.id))
        db.commit()
    return {"status": "ok", "id": user.id}

@router.delete("/users/{user_id}")
def delete_user(user_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    check_admin(current_user, db)
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="РќРµР»СЊР·СЏ СѓРґР°Р»РёС‚СЊ СЃР°РјРѕРіРѕ СЃРµР±СЏ")
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        db.query(Message).filter(Message.sender_id == user_id).delete()
        db.query(Membership).filter(Membership.user_id == user_id).delete()
        db.query(Profile).filter(Profile.user_id == user_id).delete()
        db.delete(user)
        db.commit()
    return {"status": "ok"}
