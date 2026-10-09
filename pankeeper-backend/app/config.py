from __future__ import annotations

import os
import secrets
from pathlib import Path

DATA_DIR = Path(os.environ.get("PK_DATA", Path(__file__).resolve().parent.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "pankeeper.db"
DB_URL = f"sqlite:///{DB_PATH}"

JWT_KEY_FILE = DATA_DIR / "jwt.key"
CRED_KEY_FILE = DATA_DIR / "cred.key"

def _ensure_key(file: Path, generator) -> bytes:
    if file.exists():
        return file.read_bytes().strip()
    key = generator()
    file.write_bytes(key)
    return key

def jwt_secret() -> bytes:
    return _ensure_key(JWT_KEY_FILE, lambda: secrets.token_hex(32).encode())

def cred_key() -> bytes:
    from cryptography.fernet import Fernet

    return _ensure_key(CRED_KEY_FILE, Fernet.generate_key)
