from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
import time
import os
from collections import defaultdict
from config import settings

AUTH_LIMIT = 100
AUTH_WINDOW = 150

REDIS_AVAILABLE = False
aredis = None
try:
    import redis.asyncio as aredis
    REDIS_AVAILABLE = True
except ImportError:
    pass

EXEMPT_PATHS = [
    "/api/sse/",
    "/ws/",
    "/uploads/",
    "/assets/",
    "/api/health",
    "/robots.txt",
    "/favicon.ico",
]

class RateLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.requests = defaultdict(list)
        self.redis_enabled = os.getenv("REDIS_ENABLED", "false").lower() == "true"
        self.redis_client = None
    
    async def _check_redis_rate_limit(self, client_ip: str) -> bool:
        if not self.redis_enabled or not REDIS_AVAILABLE:
            return True
        try:
            if self.redis_client is None:
                self.redis_client = aredis.Redis.from_url(settings.REDIS_URL)
            current = await self.redis_client.get(f"ratelimit:{client_ip}")
            if current and int(current) >= 300:
                return False
            pipe = self.redis_client.pipeline()
            pipe.incr(f"ratelimit:{client_ip}", 1)
            pipe.expire(f"ratelimit:{client_ip}", 60)
            await pipe.execute()
        except ImportError:
            self.redis_enabled = False
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Redis rate limit error: {e}")
        return True
    
    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else request.headers.get('x-forwarded-for', 'unknown')
        path = request.url.path
        
        for exempt in EXEMPT_PATHS:
            if path.startswith(exempt):
                return await call_next(request)
        
        if not await self._check_redis_rate_limit(client_ip):
            raise HTTPException(status_code=429, detail="Слишком много запросов. Попробуйте позже.")
        
        now = time.time()
        
        if path.startswith("/auth/login") or path.startswith("/auth/register"):
            window_start = now - AUTH_WINDOW
            self.requests[client_ip] = [t for t in self.requests[client_ip] if t > window_start]
            if len(self.requests[client_ip]) >= AUTH_LIMIT:
                raise HTTPException(status_code=429, detail="Слишком много попыток входа. Попробуйте позже.")
        else:
            window_start = now - settings.RATE_LIMIT_PERIOD
            self.requests[client_ip] = [t for t in self.requests[client_ip] if t > window_start]
            if len(self.requests[client_ip]) >= settings.RATE_LIMIT_REQUESTS:
                raise HTTPException(status_code=429, detail="Слишком много запросов. Попробуйте позже.")
        
        self.requests[client_ip].append(now)
        
        if len(self.requests) > 10000:
            cutoff = now - max(AUTH_WINDOW, settings.RATE_LIMIT_PERIOD)
            self.requests = defaultdict(list, {
                k: [t for t in v if t > cutoff]
                for k, v in self.requests.items()
            })
        
        response = await call_next(request)
        return response
