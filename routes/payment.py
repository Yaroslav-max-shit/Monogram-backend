from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import uuid
import httpx
import os
import secrets
import logging
import hmac
import hashlib
from database import get_db
from models import User, PaymentToken, PendingTransfer
from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payment", tags=["payment"])

QUARKPAY_DOMAIN = os.getenv("QUARKPAY_DOMAIN", "https://f1w6ggb2-5174.euw.devtunnels.ms")

@router.post("/create")
async def create_payment(
    plan: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    prices = {"month": 5000, "year": 50000}
    amount = prices.get(plan, 4900)
    
    shop_id = os.getenv("YOOMONEY_SHOP_ID", "")
    wallet = os.getenv("YOOMONEY_WALLET", "")
    secret_key = os.getenv("YOOMONEY_SECRET_KEY", "")
    if not shop_id:
        raise HTTPException(500, "YOOMONEY_SHOP_ID not configured")
    return_url = os.getenv("FRONTEND_URL", "https://monograme.netlify.app/") + "/payment/success"
    idempotence_key = str(uuid.uuid4())
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.yookassa.ru/v3/payments",
            json={
                "amount": {"value": amount / 100, "currency": "RUB"},
                "payment_method_data": {"type": "bank_card"},
                "confirmation": {
                    "type": "redirect",
                    "return_url": return_url
                },
                "description": f"Premium {plan} для {current_user.username}",
                "metadata": {
                    "user_id": current_user.id,
                    "plan": plan,
                    "notification_token": notification_token
                }
            },
            auth=(shop_id, secret_key),
            headers={"Idempotence-Key": idempotence_key}
        )
        
        if response.status_code != 200:
            raise HTTPException(400, "Ошибка создания платежа")
        
        data = response.json()
        return {"payment_url": data["confirmation"]["confirmation_url"]}

@router.post("/webhook")
async def payment_webhook(request: Request, db: Session = Depends(get_db)):
    # Verify webhook signature
    signature = request.headers.get("X-Shop-Signature", "")
    body_bytes = await request.body()
    expected_sig = hmac.new(
        os.getenv("YOOMONEY_SECRET_KEY", "").encode(),
        body_bytes,
        hashlib.sha256
    ).hexdigest()
    if signature and signature != expected_sig:
        logger.warning("Invalid webhook signature")
        raise HTTPException(403, "Invalid signature")
    
    body = await request.json()
    
    if body.get("event") == "payment.succeeded":
        payment = body.get("object", {})
        metadata = payment.get("metadata", {})
        
        notification_token = metadata.get("notification_token", "")
        stored = db.query(PaymentToken).filter(PaymentToken.token == notification_token).first()
        if not stored:
            return {"status": "ignored"}
        
        user_id = stored.user_id
        plan = stored.plan
        db.delete(stored)
        
        duration = 30 if plan == "month" else 365
        expires_at = datetime.utcnow() + timedelta(days=duration)
        
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.premium_until = expires_at
            db.commit()
        
        return {"status": "ok"}
    
    return {"status": "ignored"}

@router.post("/quarkpay-webhook")
async def quarkpay_webhook(request: Request, db: Session = Depends(get_db)):
    """Webhook от QuarkPay — подтверждение оплаты Premium"""
    body = await request.json()
    
    user_id = body.get("user_id")
    plan = body.get("plan")
    amount = body.get("amount")
    transaction_id = body.get("transaction_id")
    
    if not user_id or not plan:
        return {"status": "ignored"}
    
    duration = 30 if plan == "month" else 365
    expires_at = datetime.utcnow() + timedelta(days=duration)
    
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.premium_until = expires_at
        db.commit()
    
    return {"status": "ok", "user_id": user_id, "plan": plan}

@router.post("/quarkpay-init-transfer")
async def quarkpay_init_transfer(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Инициация перевода через QuarkPay — создаёт pending transfer и возвращает verify code"""
    body = await request.json()
    
    to_username = body.get("to_username")
    amount = body.get("amount")
    description = body.get("description", "")
    pin_code = body.get("pin_code", "")
    
    if not to_username or not amount:
        raise HTTPException(400, "Заполните сумму и получателя")
    
    if float(amount) <= 0:
        raise HTTPException(400, "Сумма должна быть больше 0")
    
    verify_code = secrets.token_urlsafe(16)
    
    pending = PendingTransfer(
        verify_code=verify_code,
        from_user_id=current_user.id,
        from_username=current_user.username,
        to_username=to_username,
        amount=float(amount),
        description=description,
        pin_code=pin_code,
        status="pending",
        expires_at=datetime.utcnow() + timedelta(minutes=10),
    )
    db.add(pending)
    db.commit()
    
    try:
        with httpx.Client(timeout=5) as client:
            client.post(f"{QUARKPAY_DOMAIN}/transfer/pending", json={
                "verify_code": verify_code,
                "from_user_id": current_user.id,
                "from_username": current_user.username,
                "to_username": to_username,
                "amount": float(amount),
                "description": description,
            })
    except Exception:
        pass
    
    return {"verify_code": verify_code, "status": "pending"}

@router.post("/quarkpay-transfer-webhook")
async def quarkpay_transfer_webhook(request: Request, db: Session = Depends(get_db)):
    """Webhook от QuarkPay — подтверждение перевода"""
    body = await request.json()
    
    transaction_id = body.get("transaction_id")
    from_user_id = body.get("from_user_id")
    to_username = body.get("to_username")
    amount = body.get("amount")
    description = body.get("description", "")
    
    if not transaction_id or not from_user_id or not to_username:
        return {"status": "ignored"}
    
    return {"status": "ok", "transaction_id": transaction_id}

@router.get("/quarkpay-status")
async def quarkpay_status(current_user: User = Depends(get_current_user)):
    """Проверка подключения QuarkPay"""
    try:
        with httpx.Client(timeout=3) as client:
            response = client.get(f"{QUARKPAY_DOMAIN}/connect/status-by-mono-id/{current_user.id}")
            if response.status_code == 200:
                return response.json()
    except Exception:
        pass
    
    return {"connected": False}

@router.get("/quarkpay-recipient-status/{username}")
async def quarkpay_recipient_status(username: str, current_user: User = Depends(get_current_user)):
    """Проверка подключения QuarkPay у получателя"""
    try:
        with httpx.Client(timeout=3) as client:
            response = client.get(f"{QUARKPAY_DOMAIN}/connect/status-by-username/{username}")
            if response.status_code == 200:
                return response.json()
    except Exception:
        pass
    
    return {"connected": False}

@router.post("/quarkpay-generate-connect-code")
async def quarkpay_generate_connect_code(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Генерация кода подключения для Monogram → QuarkPay"""
    import secrets as sec
    
    try:
        with httpx.Client(timeout=5) as client:
            response = client.post(f"{QUARKPAY_DOMAIN}/connect/generate", json={
                "monogram_user_id": current_user.id,
                "username": current_user.username,
            })
            if response.status_code == 200:
                data = response.json()
                code = data.get("connect_code", "")
                if code:
                    return {"connect_code": code}
    except httpx.ConnectError:
        logger.warning("QuarkPay unreachable, generating local code")
    except Exception as e:
        logger.error(f"QuarkPay generate error: {e}")
    
    code = sec.token_urlsafe(16)
    pending = PendingTransfer(
        verify_code=code,
        from_user_id=current_user.id,
        from_username=current_user.username,
        to_username="",
        amount=0,
        description="connect",
        pin_code="0000",
        status="pending",
        expires_at=datetime.utcnow() + timedelta(hours=24),
    )
    db.add(pending)
    db.commit()
    return {"connect_code": code}

@router.post("/quarkpay-confirm-connect")
async def quarkpay_confirm_connect(request: Request, current_user: User = Depends(get_current_user)):
    """Подтверждение связи аккаунта Monogram с QuarkPay"""
    body = await request.json()
    connect_code = body.get("connect_code")

    if not connect_code:
        raise HTTPException(400, "Код не указан")

    try:
        with httpx.Client(timeout=10) as client:
            response = client.post(f"{QUARKPAY_DOMAIN}/connect/confirm-public", json={
                "connect_code": connect_code,
                "monogram_user_id": current_user.id,
            })
            if response.status_code == 200:
                return {"status": "connected"}
            else:
                logger.warning(f"QuarkPay confirm returned {response.status_code}: {response.text}")
                return {"status": "failed", "detail": f"QuarkPay error: {response.status_code}"}
    except httpx.ConnectError:
        logger.error(f"QuarkPay unreachable at {QUARKPAY_DOMAIN}")
        raise HTTPException(503, "QuarkPay сервис недоступен")
    except Exception as e:
        logger.error(f"QuarkPay confirm error: {e}")
        raise HTTPException(500, "Ошибка подключения к QuarkPay")

@router.get("/quarkpay-connect-info/{code}")
async def quarkpay_connect_info(code: str):
    """Получение информации о подключении по коду"""
    return {"user": {"username": "QuarkPay User"}, "code": code}

@router.post("/quarkpay-auto-register")
async def quarkpay_auto_register(request: Request):
    """Авто-регистрация в QuarkPay через Monogram"""
    body = await request.json()
    connect_code = body.get("connect_code")
    monogram_user_id = body.get("monogram_user_id")
    monogram_username = body.get("monogram_username")

    try:
        with httpx.Client(timeout=10) as client:
            response = client.post(f"{QUARKPAY_DOMAIN}/connect/auto-register", json={
                "connect_code": connect_code,
                "monogram_user_id": monogram_user_id,
                "monogram_username": monogram_username,
            })
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"QuarkPay auto-register returned {response.status_code}")
                return {"status": "failed"}
    except httpx.ConnectError:
        logger.error(f"QuarkPay unreachable for auto-register")
        return {"status": "failed"}
    except Exception as e:
        logger.error(f"QuarkPay auto-register error: {e}")
        return {"status": "failed"}
