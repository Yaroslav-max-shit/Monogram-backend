import os
import shutil
from pathlib import Path
from fastapi import HTTPException, UploadFile
from typing import List
try:
    import magic
except ImportError:
    magic = None
try:
    from PIL import Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False
from config import settings

UPLOAD_DIRS = {
    "avatars": "uploads/avatars",
    "files": "uploads/files",
    "stickers": "uploads/stickers",
    "wallpapers": "uploads/wallpapers",
}

for dir_path in UPLOAD_DIRS.values():
    Path(dir_path).mkdir(parents=True, exist_ok=True)

def validate_file(file: UploadFile, max_size_mb: int = None) -> str:
    max_size = max_size_mb or settings.MAX_FILE_SIZE_MB
    
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    
    if size > max_size * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Р¤Р°Р№Р» СЃР»РёС€РєРѕРј Р±РѕР»СЊС€РѕР№. РњР°РєСЃРёРјСѓРј {max_size}MB")
    
    try:
        mime = magic.from_buffer(file.file.read(1024), mime=True)
        file.file.seek(0)
    except Exception:
        mime = file.content_type or "application/octet-stream"
    
    if mime not in settings.ALLOWED_FILE_TYPES:
        raise HTTPException(status_code=400, detail=f"РўРёРї С„Р°Р№Р»Р° '{mime}' РЅРµ РїРѕРґРґРµСЂР¶РёРІР°РµС‚СЃСЏ")
    
    return mime

def save_upload_file(file: UploadFile, subdir: str, filename: str = None) -> str:
    import uuid
    
    if not filename:
        ext = Path(file.filename).suffix
        filename = f"{uuid.uuid4().hex}{ext}"
    
    safe_filename = Path(filename).name
    filepath = Path(UPLOAD_DIRS[subdir]) / safe_filename
    
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    return f"/{UPLOAD_DIRS[subdir]}/{safe_filename}"

def optimize_image(filepath: str, max_width: int = 1920, quality: int = 85):
    if not HAS_PILLOW:
        return
    try:
        img = Image.open(filepath)
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
        
        if img.mode == 'RGBA':
            img = img.convert('RGB')
        
        img.save(filepath, optimize=True, quality=quality)
    except Exception:
        pass
