"""Encryption for secrets held in the relational store.

The architecture explicitly moves the cloud API key out of ``.env`` and into
PostgreSQL (so an admin can rotate it at runtime through the Admin Dashboard).
Storing a live API key as plaintext in a table is not acceptable, so keys are
encrypted with Fernet (symmetric AES-128-CBC + HMAC) before they are persisted.

The master key is read from ``LLM_CONFIG_SECRET_KEY``. If that value is not a
valid Fernet key it is stretched to one with SHA-256, so any sufficiently long
passphrase works. When the variable is unset a well-known development key is
used and a loud warning is logged - production deployments **must** set their
own key.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Optional

from .config import settings

logger = logging.getLogger(__name__)

_DEV_PASSPHRASE = "hybrid-ai-tutor-development-key-change-me"

_fernet_instance = None


class SecretStoreError(RuntimeError):
    """Raised when a secret cannot be encrypted or decrypted."""


def _derive_fernet_key(raw: str) -> bytes:
    """Turn an arbitrary string into a valid 32-byte urlsafe Fernet key."""
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


def _fernet():
    """Return the process-wide Fernet instance (lazy, cached)."""
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance

    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - install-time issue
        raise SecretStoreError(
            "The 'cryptography' package is required to store LLM API keys "
            "securely. Run `pip install -r requirements.txt`."
        ) from exc

    configured = (settings.llm_config_secret_key or "").strip()
    if not configured:
        logger.warning(
            "LLM_CONFIG_SECRET_KEY is not set - falling back to a well-known "
            "development key. Set a strong value in .env before deploying."
        )
        configured = _DEV_PASSPHRASE

    # A Fernet key is 44 chars of urlsafe base64. If the configured value is
    # already one, use it verbatim; otherwise derive a deterministic key.
    if len(configured) == 44:
        try:
            _fernet_instance = Fernet(configured.encode("ascii"))
            return _fernet_instance
        except (ValueError, TypeError):
            pass

    _fernet_instance = Fernet(_derive_fernet_key(configured))
    return _fernet_instance


def encrypt_secret(plaintext: str) -> str:
    """Encrypt ``plaintext`` and return a URL-safe token string."""
    if not plaintext:
        raise SecretStoreError("Refusing to encrypt an empty secret.")
    token = _fernet().encrypt(plaintext.strip().encode("utf-8"))
    return token.decode("ascii")


def try_decrypt_secret(token: Optional[str]) -> Optional[str]:
    """Best-effort decryption.

    Returns ``None`` when there is no token or it cannot be decrypted (for
    example after the master key was rotated), instead of crashing a request.
    """
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except SecretStoreError:
        raise
    except Exception as exc:  # noqa: BLE001 - cryptography raises many types
        logger.error("Stored LLM API key could not be decrypted: %s", exc)
        return None


def mask_secret(plaintext: Optional[str]) -> Optional[str]:
    """Return a display-safe form of a secret (``sk-...a1b2``)."""
    if not plaintext:
        return None
    tail = plaintext[-4:] if len(plaintext) > 4 else plaintext
    return f"...{tail}"
