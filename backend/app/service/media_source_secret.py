"""Media source secret encryption helpers."""

import base64
import hmac
import hashlib
import secrets

from app.core.config import AppSettings

CURRENT_CREDENTIAL_VERSION = 2
LEGACY_CREDENTIAL_VERSION = 1
VERSIONED_SECRET_PREFIX = "v2:"
_NONCE_LENGTH = 16
_TAG_LENGTH = 32


def _key(settings: AppSettings) -> bytes:
    seed = str(settings.database_path).encode("utf-8")
    return hashlib.sha256(seed).digest()


def _xor_bytes(raw: bytes, key: bytes) -> bytes:
    return bytes(value ^ key[index % len(key)] for index, value in enumerate(raw))


def _derive_stream_key(settings: AppSettings, nonce: bytes, length: int) -> bytes:
    key = _key(settings)
    chunks: list[bytes] = []
    counter = 0
    while sum(len(chunk) for chunk in chunks) < length:
        counter_bytes = counter.to_bytes(4, "big")
        chunks.append(hmac.new(key, nonce + counter_bytes, hashlib.sha256).digest())
        counter += 1
    return b"".join(chunks)[:length]


def _encrypt_v2_secret(settings: AppSettings, secret: str) -> str:
    raw = secret.encode("utf-8")
    nonce = secrets.token_bytes(_NONCE_LENGTH)
    stream_key = _derive_stream_key(settings, nonce, len(raw))
    encrypted = _xor_bytes(raw, stream_key)
    tag = hmac.new(
        _key(settings),
        b"mediaai-media-source-secret-v2" + nonce + encrypted,
        hashlib.sha256,
    ).digest()
    payload = base64.urlsafe_b64encode(nonce + encrypted + tag).decode("ascii")
    return f"{VERSIONED_SECRET_PREFIX}{payload}"


def _decrypt_v2_secret(settings: AppSettings, encrypted_secret: str) -> str:
    payload = encrypted_secret[len(VERSIONED_SECRET_PREFIX):]
    decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
    if len(decoded) < _NONCE_LENGTH + _TAG_LENGTH:
        raise ValueError("媒体源密文格式无效")
    nonce = decoded[:_NONCE_LENGTH]
    tag = decoded[-_TAG_LENGTH:]
    encrypted = decoded[_NONCE_LENGTH:-_TAG_LENGTH]
    expected_tag = hmac.new(
        _key(settings),
        b"mediaai-media-source-secret-v2" + nonce + encrypted,
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("媒体源密文校验失败")
    stream_key = _derive_stream_key(settings, nonce, len(encrypted))
    return _xor_bytes(encrypted, stream_key).decode("utf-8")


def _decrypt_legacy_secret(settings: AppSettings, encrypted_secret: str) -> str:
    encrypted = base64.urlsafe_b64decode(encrypted_secret.encode("ascii"))
    raw = _xor_bytes(encrypted, _key(settings))
    return raw.decode("utf-8")


def encrypt_secret(settings: AppSettings, secret: str | None) -> str | None:
    if secret is None:
        return None
    return _encrypt_v2_secret(settings, secret)


def has_secret(encrypted_secret: str | None) -> bool:
    return bool(encrypted_secret)


def credential_version_for_secret(encrypted_secret: str | None) -> int:
    if not encrypted_secret:
        return LEGACY_CREDENTIAL_VERSION
    if encrypted_secret.startswith(VERSIONED_SECRET_PREFIX):
        return CURRENT_CREDENTIAL_VERSION
    return LEGACY_CREDENTIAL_VERSION


def should_migrate_secret(encrypted_secret: str | None) -> bool:
    return bool(encrypted_secret) and not encrypted_secret.startswith(VERSIONED_SECRET_PREFIX)


def migrate_secret(settings: AppSettings, encrypted_secret: str | None) -> str | None:
    if not encrypted_secret:
        return encrypted_secret
    if not should_migrate_secret(encrypted_secret):
        return encrypted_secret
    return encrypt_secret(settings, _decrypt_legacy_secret(settings, encrypted_secret))


def decrypt_secret(settings: AppSettings, encrypted_secret: str | None) -> str | None:
    if not encrypted_secret:
        return None
    if encrypted_secret.startswith(VERSIONED_SECRET_PREFIX):
        return _decrypt_v2_secret(settings, encrypted_secret)
    return _decrypt_legacy_secret(settings, encrypted_secret)
