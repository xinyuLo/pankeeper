from __future__ import annotations

import hashlib
import secrets
import time

import jwt
from cryptography.fernet import Fernet

from .config import cred_key, jwt_secret

_fernet = Fernet(cred_key())

def hash_password(plain: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), 120_000).hex()
    return f"pbkdf2${salt}${digest}"

def verify_password(plain: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False
    calc = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), 120_000).hex()
    return secrets.compare_digest(calc, digest)

def make_token(username: str, session_days: int) -> str:
    payload = {"sub": username, "exp": int(time.time()) + session_days * 86400}
    return jwt.encode(payload, jwt_secret(), algorithm="HS256")

def parse_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, jwt_secret(), algorithms=["HS256"])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None

def encrypt_credential(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()

def decrypt_credential(cipher: str) -> str:
    if not cipher:
        return ""
    return _fernet.decrypt(cipher.encode()).decode()
