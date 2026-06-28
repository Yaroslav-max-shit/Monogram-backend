from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session
import random
import string
import os
import shutil
from database import get_db
from models import User, StickerPack, Sticker
from .auth import get_current_user
from datetime import datetime

router = APIRouter(prefix="/stickers", tags=["stickers"])

def generate_code():
    return ''.join(random.choices(string.digits, k=8))

class CreatePackRequest(BaseModel):
    name: str

@router.post("/pack/create")
async def create_pack(
    data: CreatePackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    code = generate_code()
    while db.query(StickerPack).filter(StickerPack.code == code).first():
        code = generate_code()
    
    pack = StickerPack(
        name=data.name,
        code=code,
        author_id=current_user.id
    )
    db.add(pack)
    db.commit()
    db.refresh(pack)
    
    return {"id": pack.id, "name": pack.name, "code": pack.code}

@router.post("/pack/{pack_id}/add")
async def add_sticker(
    pack_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    pack = db.query(StickerPack).filter(
        StickerPack.id == pack_id,
        StickerPack.author_id == current_user.id
    ).first()
    
    if not pack:
        raise HTTPException(404, "Стикер-пак не найден")
    
    # Определяем тип файла
    content_type = file.content_type or ""
    if content_type.startswith("image/"):
        file_type = "image"
    elif content_type.startswith("video/"):
        file_type = "video"
    else:
        raise HTTPException(400, "Поддерживаются только изображения и видео")
    
    # Сохраняем файл
    import uuid
    upload_dir = "uploads/stickers"
    os.makedirs(upload_dir, exist_ok=True)
    ext = os.path.splitext(file.filename or "sticker")[1] if file.filename else ".png"
    unique_name = str(uuid.uuid4())
    filename = f"{pack_id}_{unique_name}{ext}"
    filepath = os.path.join(upload_dir, filename)
    filepath = os.path.abspath(filepath)
    upload_dir_abs = os.path.abspath(upload_dir)
    if not filepath.startswith(upload_dir_abs):
        raise HTTPException(400, "Invalid file path")
    
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    count = db.query(Sticker).filter(Sticker.pack_id == pack_id).count()
    
    sticker = Sticker(
        pack_id=pack_id,
        type=file_type,
        url=f"/uploads/stickers/{filename}",
        order=count
    )
    db.add(sticker)
    db.commit()
    
    return {"id": sticker.id, "url": sticker.url}

@router.get("/pack/{code}")
async def get_pack(code: str, db: Session = Depends(get_db)):
    pack = db.query(StickerPack).filter(StickerPack.code == code).first()
    if not pack:
        raise HTTPException(404, "Стикер-пак не найден")
    
    stickers = db.query(Sticker).filter(Sticker.pack_id == pack.id).order_by(Sticker.order).all()
    
    return {
        "id": pack.id,
        "name": pack.name,
        "code": pack.code,
        "author": pack.author_id,
        "stickers": [{"id": s.id, "type": s.type, "url": s.url} for s in stickers]
    }

@router.get("/my")
async def get_my_packs(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    packs = db.query(StickerPack).filter(StickerPack.author_id == current_user.id).all()
    return [{"id": p.id, "name": p.name, "code": p.code} for p in packs]


@router.get("/packs")
async def list_packs(db: Session = Depends(get_db)):
    packs = db.query(StickerPack).filter(StickerPack.is_public == True).order_by(StickerPack.created_at.desc()).all()
    result = []
    for p in packs:
        sticker_count = db.query(Sticker).filter(Sticker.pack_id == p.id).count()
        first_sticker = db.query(Sticker).filter(Sticker.pack_id == p.id).order_by(Sticker.order).first()
        result.append({
            "id": p.id, "name": p.name, "code": p.code,
            "author_id": p.author_id,
            "sticker_count": sticker_count,
            "thumbnail": first_sticker.url if first_sticker else None,
            "created_at": p.created_at.isoformat() if p.created_at else None
        })
    return result


@router.get("/trending")
async def trending_packs(db: Session = Depends(get_db)):
    packs = db.query(StickerPack).filter(StickerPack.is_public == True).order_by(StickerPack.created_at.desc()).limit(10).all()
    result = []
    for p in packs:
        first_sticker = db.query(Sticker).filter(Sticker.pack_id == p.id).order_by(Sticker.order).first()
        result.append({
            "id": p.id, "name": p.name, "code": p.code,
            "thumbnail": first_sticker.url if first_sticker else None
        })
    return result


@router.delete("/{sticker_id}")
async def delete_sticker(
    sticker_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    sticker = db.query(Sticker).filter(Sticker.id == sticker_id).first()
    if not sticker:
        raise HTTPException(404, "Sticker not found")
    pack = db.query(StickerPack).filter(StickerPack.id == sticker.pack_id).first()
    if pack and pack.author_id != current_user.id:
        raise HTTPException(403, "Not your sticker pack")
    # Remove file
    import os
    relative = sticker.url.lstrip("/")
    filepath = os.path.normpath(relative)
    if filepath.startswith("..") or not filepath.startswith("uploads/"):
        raise HTTPException(400, "Invalid sticker path")
    if os.path.exists(filepath):
        os.remove(filepath)
    db.delete(sticker)
    db.commit()
    return {"status": "deleted"}


@router.get("/search")
async def search_packs(q: str = "", db: Session = Depends(get_db)):
    packs = db.query(StickerPack).filter(
        StickerPack.is_public == True,
        StickerPack.name.ilike(f"%{q}%")
    ).limit(20).all()
    result = []
    for p in packs:
        first_sticker = db.query(Sticker).filter(Sticker.pack_id == p.id).order_by(Sticker.order).first()
        result.append({
            "id": p.id, "name": p.name, "code": p.code,
            "thumbnail": first_sticker.url if first_sticker else None
        })
    return result