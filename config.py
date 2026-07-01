from pydantic_settings import BaseSettings
from typing import List, Optional
import os

class Settings(BaseSettings):
    # Security
    SECRET_KEY: str = ""  # Must be set in .env - no default for security
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 дней

    # Database
    DATABASE_URL: str = "sqlite:///./monogram.db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_ENABLED: bool = False

    # CORS
    CORS_ORIGINS_STR: str = "https://monogram-one-mu.vercel.app/"

    @property
    def CORS_ORIGINS(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS_STR.split(",")]

    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 500
    RATE_LIMIT_PERIOD: int = 60

    # File Upload
    MAX_FILE_SIZE_MB: int = 50
    ALLOWED_FILE_TYPES: List[str] = [
        "image/jpeg", "image/png", "image/gif", "image/webp",
        "video/mp4", "video/webm",
        "audio/mpeg", "audio/wav",
        "application/pdf"
    ]

    # Email
    SMTP_HOST: str = "smtp.yandex.ru"
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""

    # WebRTC TURN
    TURN_SERVERS: List[dict] = [
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "stun:stun1.l.google.com:19302"},
    ]

    FRONTEND_URL: str = "https://monogram-one-mu.vercel.app/"

    # JWT Cookie
    JWT_COOKIE_NAME: str = "access_token"
    JWT_COOKIE_MAX_AGE: int = 1800
    JWT_COOKIE_SECURE: bool = False
    JWT_COOKIE_SAMESITE: str = "strict"

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.SECRET_KEY:
            raise ValueError("SECRET_KEY must be set in .env file")

settings = Settings()