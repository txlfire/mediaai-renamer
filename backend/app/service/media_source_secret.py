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
