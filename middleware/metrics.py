from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
import time
import json
import os

METRICS_FILE = "logs/metrics.jsonl"
MAX_METRICS_SIZE = 100 * 1024 * 1024  # 100MB max

class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start
        
        metric = {
            "timestamp": time.time(),
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round(duration * 1000, 2),
        }
        
        try:
            os.makedirs("logs", exist_ok=True)
            if os.path.exists(METRICS_FILE) and os.path.getsize(METRICS_FILE) > MAX_METRICS_SIZE:
                open(METRICS_FILE, "w").close()
            with open(METRICS_FILE, "a") as f:
                f.write(json.dumps(metric) + "\n")
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Metrics write error: {e}")
        
        return response
