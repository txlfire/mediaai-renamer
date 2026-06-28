"""Media source secret encryption helpers."""

import base64
import hashlib

from app.core.config import AppSettings


def _key(settings: AppSettings) -> bytes:
    seed = str(settings.database_path).encode("utf-8")
    return hashlib.sha256(seed).digest()


def encrypt_secret(settings: AppSettings, secret: str | None) -> str | None:
    if secret is None:
        return None
    raw = secret.encode("utf-8")
    key = _key(settings)
    encrypted = bytes(value ^ key[index % len(key)] for index, value in enumerate(raw))
    return base64.urlsafe_b64encode(encrypted).decode("ascii")


def has_secret(encrypted_secret: str | None) -> bool:
    return bool(encrypted_secret)


def decrypt_secret(settings: AppSettings, encrypted_secret: str | None) -> str | None:
    if not encrypted_secret:
        return None
    encrypted = base64.urlsafe_b64decode(encrypted_secret.encode("ascii"))
    key = _key(settings)
    raw = bytes(value ^ key[index % len(key)] for index, value in enumerate(encrypted))
    return raw.decode("utf-8")
