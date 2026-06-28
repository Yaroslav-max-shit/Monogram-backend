from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
import secrets

class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)

        path = request.url.path
        if path.startswith("/auth/") and path not in ("/auth/logout",):
            return await call_next(request)

        csrf_header = request.headers.get("X-CSRF-Token", "")
        csrf_cookie = request.cookies.get("csrf_token", "")

        if not csrf_header or not csrf_cookie or csrf_header != csrf_cookie:
            raise HTTPException(status_code=403, detail="CSRF validation failed")

        return await call_next(request)
