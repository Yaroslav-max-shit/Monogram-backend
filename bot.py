import random
import time
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from database import SessionLocal, engine
from models import Message, Chat
from logging_config import logger


BOT_RESPONSES = [
    'Привет! Как дела.',
    'Интересно, да продолжай!',
    'Я тоже так думаю, ты правда умный.',
    'Хороший вопрос, подожди немного!'
]

bot_last_active = {}

def run_bot():
    while True:
        try:
            db = SessionLocal()

            recent_time = datetime.utcnow() - timedelta(minutes=2)
            recent_messages = db.query(Message).filter(
                Message.timestamp > recent_time
            ).all()

            for msg in recent_messages:
                if msg.sender_id != 9999 and msg.chat_id in bot_last_active:
                    response = Message(
                        content=random.choice(BOT_RESPONSES),
                        sender_id=9999,
                        chat_id=msg.chat_id,
                        timestamp=datetime.utcnow()
                    )
                    db.add(response)
                    db.commit()
                    bot_last_active[msg.chat_id] = time.time()

        except Exception as e:
            db.rollback()
            logger.error(f"Bot error: {e}")
        finally:
            db.close()

        time.sleep(10)
