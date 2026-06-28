import os
from cryptography.fernet import Fernet, InvalidToken
import base64
import hashlib

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")

if not ENCRYPTION_KEY:
    import logging
    logging.getLogger(__name__).warning("ENCRYPTION_KEY not set - encryption disabled")

key_bytes = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
key = base64.urlsafe_b64encode(key_bytes)

fernet = Fernet(key)

def encrypt_data(data: str) -> str:
    if not data:
        return data
    return fernet.encrypt(data.encode()).decode()

def decrypt_data(encrypted_data: str) -> str:
    if not encrypted_data:
        return encrypted_data
    return fernet.decrypt(encrypted_data.encode()).decode()
