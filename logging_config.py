import logging
import os
import sys
import json
from datetime import datetime

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra"):
            log_entry.update(record.extra)
        return json.dumps(log_entry, ensure_ascii=False)

os.makedirs('logs', exist_ok=True)

handler_file = logging.FileHandler('logs/app.jsonl', encoding='utf-8')
handler_file.setFormatter(JSONFormatter())
handler_stdout = logging.StreamHandler(sys.stdout)

logging.basicConfig(
    level=logging.INFO,
    handlers=[handler_file, handler_stdout]
)

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

logger = logging.getLogger(__name__)