from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy.orm import Session
from database import get_db
from models import User, AIModel, AIApiKey
from datetime import datetime, timedelta
import os
import httpx
import logging
import time

router = APIRouter(prefix="/api/v1", tags=["ai_api"])
logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

def get_api_key(request: Request, db: Session):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ma_"):
        raise HTTPException(401, "Invalid API key format. Use: Authorization: Bearer ma_{key}")
    key_str = auth.replace("Bearer ma_", "")
    key = db.query(AIApiKey).filter(AIApiKey.key == key_str, AIApiKey.is_active == True).first()
    if not key:
        raise HTTPException(401, "Invalid API key")
    
    now = datetime.utcnow()
    if (now - key.last_reset).days >= 7:
        key.weekly_used = 0
        key.last_reset = now
    if (now - key.last_reset).days >= 1:
        key.daily_used = 0
        key.last_reset = now
    
    if key.daily_used >= key.daily_limit:
        raise HTTPException(429, "Daily token limit exceeded")
    if key.weekly_used >= key.weekly_limit:
        raise HTTPException(429, "Weekly token limit exceeded")
    
    return key

@router.get("/models")
def list_models(db: Session = Depends(get_db)):
    models = db.query(AIModel).filter(AIModel.is_active == True).all()
    return {
        "object": "list",
        "data": [
            {
                "id": m.name,
                "object": "model",
                "created": int(m.created_at.timestamp()) if m.created_at else 0,
                "owned_by": "monogram",
                "name": m.display_name,
                "description": m.description,
                "max_tokens": m.max_tokens,
                "pricing": {
                    "input": m.input_price_per_million,
                    "output": m.output_price_per_million
                }
            }
            for m in models
        ]
    }

@router.post("/chat/completions")
async def chat_completions(request: Request, db: Session = Depends(get_db)):
    api_key = get_api_key(request, db)
    
    body = await request.json()
    model_name = body.get("model", "qwen3.5-4b")
    messages = body.get("messages", [])
    stream = body.get("stream", False)
    max_tokens = body.get("max_tokens", 2048)
    
    if not messages:
        raise HTTPException(400, "messages is required")
    
    model = db.query(AIModel).filter(AIModel.name == model_name, AIModel.is_active == True).first()
    if not model:
        raise HTTPException(404, f"Model '{model_name}' not found")
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            if stream:
                async def generate():
                    async with client.stream("POST", f"{OLLAMA_URL}/api/chat", json={
                        "model": model_name,
                        "messages": messages,
                        "stream": True,
                        "options": {"num_predict": max_tokens}
                    }) as response:
                        async for line in response.aiter_lines():
                            if line:
                                yield f"data: {line}\n\n"
                    yield "data: [DONE]\n\n"
                
                return StreamingResponse(
                    generate(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "X-MonoCode-Plan": api_key.plan,
                        "X-MonoCode-Daily-Limit": str(api_key.daily_limit),
                        "X-MonoCode-Weekly-Limit": str(api_key.weekly_limit),
                    }
                )
            else:
                response = await client.post(f"{OLLAMA_URL}/api/chat", json={
                    "model": model_name,
                    "messages": messages,
                    "stream": False,
                    "options": {"num_predict": max_tokens}
                })
                
                if response.status_code != 200:
                    raise HTTPException(502, "AI service unavailable")
                
                result = response.json()
                content = result.get("message", {}).get("content", "")
                prompt_tokens = result.get("prompt_eval_count", 0)
                completion_tokens = result.get("eval_count", 0)
                total_tokens = prompt_tokens + completion_tokens
                
                api_key.daily_used += total_tokens
                api_key.weekly_used += total_tokens
                db.commit()
                
                return JSONResponse(
                    content={
                        "id": f"mono-{int(time.time())}",
                        "object": "chat.completion",
                        "model": model_name,
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": content
                                },
                                "finish_reason": "stop"
                            }
                        ],
                        "usage": {
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": total_tokens
                        }
                    },
                    headers={
                        "X-MonoCode-Plan": api_key.plan,
                        "X-MonoCode-Tokens-Used": str(total_tokens),
                        "X-MonoCode-Daily-Used": str(api_key.daily_used),
                        "X-MonoCode-Daily-Limit": str(api_key.daily_limit),
                        "X-MonoCode-Weekly-Used": str(api_key.weekly_used),
                        "X-MonoCode-Weekly-Limit": str(api_key.weekly_limit),
                    }
                )
    except httpx.ConnectError:
        raise HTTPException(503, "AI service unavailable. Ollama not running.")
    except Exception as e:
        logger.error(f"AI error: {e}")
        raise HTTPException(500, f"AI error: {str(e)}")