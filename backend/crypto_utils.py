"""
crypto_utils.py — Utilitários de criptografia para credenciais sensíveis.

Usa Fernet (AES-128-CBC + HMAC-SHA256) da biblioteca `cryptography`.
A chave Fernet é derivada deterministicamente de ENCRYPTION_KEY (env var).
Se ENCRYPTION_KEY não estiver definida, usa POSTGRES_PASSWORD + salt fixo como fallback.

Isso garante que:
  - O mesmo container sempre deriva a mesma chave (sem armazenar a chave em disco).
  - A chave muda apenas se ENCRYPTION_KEY ou POSTGRES_PASSWORD mudar.
  - O banco pode ser restaurado em qualquer container com as mesmas env vars.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _derive_key() -> bytes:
    """
    Deriva uma chave Fernet de 32 bytes a partir das env vars.

    Prioridade:
      1. ENCRYPTION_KEY  (recomendado — string arbitrária definida pelo usuário)
      2. POSTGRES_PASSWORD + salt fixo (fallback automático)
    """
    secret = os.getenv("ENCRYPTION_KEY") or (
        os.getenv("POSTGRES_PASSWORD", "crypto") + ":okx_strategy:v1"
    )
    digest = hashlib.sha256(secret.encode("utf-8")).digest()   # 32 bytes
    return base64.urlsafe_b64encode(digest)                    # Fernet exige base64url 32b


def _fernet() -> Fernet:
    return Fernet(_derive_key())


def encrypt(plaintext: str) -> str:
    """Encripta uma string e retorna o token Fernet como string UTF-8."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """Decripta um token Fernet e retorna o plaintext. Lança InvalidToken se inválido."""
    if not token:
        return ""
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")


def safe_decrypt(token: str, default: str = "") -> str:
    """Decripta silenciosamente; retorna `default` se o token for inválido."""
    try:
        return decrypt(token)
    except (InvalidToken, Exception):
        return default
