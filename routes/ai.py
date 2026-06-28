from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from database import get_db
from models import User
from .auth import get_current_user
import httpx
import json
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])

OLLAMA_URL = "http://localhost:11434"

@router.post("/chat")
async def ai_chat(request: Request, current_user: User = Depends(get_current_user)):
    """Отправить сообщение AI-ассистенту"""
    data = await request.json()
    message = data.get("message", "")
    model = data.get("model", "qwen3.5:4b")

    if not message:
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": message}],
                    "stream": False,
                }
            )

            if response.status_code == 200:
                result = response.json()
                reply = result.get("message", {}).get("content", "")
                return {"reply": reply}
            else:
                raise HTTPException(status_code=502, detail="AI сервис недоступен")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Ollama не запущен. Запустите: ollama serve")
    except Exception as e:
        logger.error(f"AI error: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка AI сервиса")

@router.post("/translate")
async def ai_translate(request: Request, current_user: User = Depends(get_current_user)):
    """Перевести сообщение на другой язык"""
    data = await request.json()
    text = data.get("text", "")
    target_lang = data.get("target_lang", "en")

    if not text:
        raise HTTPException(status_code=400, detail="Текст не может быть пустым")

    prompt = f"Translate the following text to {target_lang}. Return ONLY the translation, no explanations:\n\n{text}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": "qwen3.5:4b",
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                }
            )

            if response.status_code == 200:
                result = response.json()
                translation = result.get("message", {}).get("content", "")
                return {"translation": translation}
            else:
                raise HTTPException(status_code=502, detail="AI сервис недоступен")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Ollama не запущен")
    except Exception as e:
        logger.error(f"Translation error: {e}")
        raise HTTPException(status_code=500, detail="Ошибка перевода")

@router.get("/status")
async def ai_status():
    """Проверить статус Ollama"""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            if response.status_code == 200:
                models = response.json().get("models", [])
                return {"status": "online", "models": [m.get("name") for m in models]}
    except Exception:
        pass
    return {"status": "offline", "models": []}
