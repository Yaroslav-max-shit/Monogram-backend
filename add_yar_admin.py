import sys
sys.path.insert(0, '.')
from database import SessionLocal
from models import Admin, User
from logging_config import logger

db = SessionLocal()

user = db.query(User).filter(User.username == 'Yarik').first()
if not user:
    user = db.query(User).filter(User.username == 'Yar').first()

if user:
    existing = db.query(Admin).filter(Admin.user_id == user.id).first()
    if not existing:
        db.add(Admin(user_id=user.id, added_by=user.id))
        db.commit()
        logger.info(f"{user.username} (id={user.id}) добавлен как админ!")
    else:
        logger.info(f"{user.username} уже админ")
else:
    logger.error("Пользователь не найден. Список:")
    for u in db.query(User).all():
        logger.info(f"  {u.id}: {u.username}")

db.close()
