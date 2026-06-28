from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db, engine
from models import User
from .auth import get_current_user
import json
from sqlalchemy import text

router = APIRouter(prefix="/e2ee", tags=["e2ee"])

def ensure_keys_table():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS e2ee_keys (
                user_id INTEGER PRIMARY KEY,
                public_key TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """))
        conn.commit()

@router.post("/set-public-key")
async def set_public_key(
    public_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not public_key or len(public_key) < 16:
        raise HTTPException(400, "Invalid public key format")
    ensure_keys_table()
    db.execute(
        text("INSERT OR REPLACE INTO e2ee_keys (user_id, public_key) VALUES (:uid, :key)"),
        {"uid": current_user.id, "key": public_key}
    )
    db.commit()
    return {"status": "ok", "message": "Public key saved"}

@router.get("/get-public-key/{user_id}")
async def get_public_key(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    ensure_keys_table()
    row = db.execute(
        text("SELECT public_key FROM e2ee_keys WHERE user_id = :uid"),
        {"uid": user_id}
    ).fetchone()
    return {"public_key": row[0] if row else None, "user_id": user_id}