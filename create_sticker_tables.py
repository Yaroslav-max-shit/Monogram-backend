from database import engine
from models import Base
from logging_config import logger


Base.metadata.create_all(bind=engine)
logger.info("Таблицы созданы")
