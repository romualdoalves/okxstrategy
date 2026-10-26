"""
crypto_utils.py — Utilitários de criptografia para credenciais sensíveis.

Usa Fernet (AES-128-CBC + HMAC-SHA256) da biblioteca `cryptography`.

Chaves:
  - Primária (ENCRYPTION_KEY): string de alta entropia definida no ambiente, derivada com
    PBKDF2-HMAC-SHA256. É a única usada para encriptar.
  - Legadas (somente decriptação, para ler dados antigos): sha256(ENCRYPTION_KEY) e
    sha256(POSTGRES_PASSWORD + ":okx_strategy:v1").

Sem ENCRYPTION_KEY o módulo cai no esquema legado (derivado de POSTGRES_PASSWORD) e avisa:
quem tem a senha do banco consegue decifrar as credenciais. Com ENCRYPTION_KEY definida,
`rotate_secrets()` (chamada no startup) reencripta valores antigos com a chave primária.

Gere uma chave com: python -c "import secrets; print(secrets.token_urlsafe(48))"
Guarde-a fora do banco: perdê-la torna as credenciais irrecuperáveis (reconecte a OKX).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

log = logging.getLogger("crypto_utils")

_PBKDF2_SALT = b"okx_strategy:fernet:v2"
_PBKDF2_ITERATIONS = 600_000
SECRET_SETTING_SUFFIXES = ("_api_key", "_api_secret", "_passphrase")


def _sha256_key(secret: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())


def _pbkdf2_key(secret: str) -> bytes:
    raw = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), _PBKDF2_SALT, _PBKDF2_ITERATIONS)
    return base64.urlsafe_b64encode(raw)


def _legacy_pg_secret() -> str:
    return os.getenv("POSTGRES_PASSWORD", "crypto") + ":okx_strategy:v1"


@lru_cache(maxsize=1)
def _fernets() -> tuple[Fernet, MultiFernet]:
    """Retorna (fernet primário, MultiFernet com primário + legadas para decriptar)."""
    enc_key = os.getenv("ENCRYPTION_KEY", "").strip()
    if enc_key:
        primary = Fernet(_pbkdf2_key(enc_key))
        keys = [primary, Fernet(_sha256_key(enc_key)), Fernet(_sha256_key(_legacy_pg_secret()))]
    else:
        log.warning(
            "ENCRYPTION_KEY não definida: credenciais protegidas só pela senha do Postgres. "
            "Defina ENCRYPTION_KEY no .env."
        )
        primary = Fernet(_sha256_key(_legacy_pg_secret()))
        keys = [primary]
    return primary, MultiFernet(keys)


def encrypt(plaintext: str) -> str:
    """Encripta uma string e retorna o token Fernet como string UTF-8."""
    if not plaintext:
        return ""
    return _fernets()[0].encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """Decripta um token Fernet (qualquer chave conhecida). Lança InvalidToken se inválido."""
    if not token:
        return ""
    return _fernets()[1].decrypt(token.encode("utf-8")).decode("utf-8")


def safe_decrypt(token: str, default: str = "") -> str:
    """Decripta silenciosamente; retorna `default` se o token for inválido."""
    try:
        return decrypt(token)
    except (InvalidToken, Exception):
        return default


def rotate_secrets(db) -> int:
    """
    Reencripta com a chave primária os segredos da tabela `settings` que ainda usam chave
    legada. Só age se ENCRYPTION_KEY estiver definida. Retorna quantos valores migrou.
    """
    if not os.getenv("ENCRYPTION_KEY", "").strip():
        return 0
    from .database import SettingsModel

    primary = _fernets()[0]
    migrated = 0
    for row in db.query(SettingsModel).all():
        if not row.key.endswith(SECRET_SETTING_SUFFIXES) or not row.value:
            continue
        try:
            primary.decrypt(row.value.encode("utf-8"))
            continue                                   # já está na chave primária
        except InvalidToken:
            pass
        try:
            row.value = encrypt(decrypt(row.value))
            migrated += 1
        except InvalidToken:
            log.error("Segredo '%s' não decifra com nenhuma chave conhecida — reconecte a conta.", row.key)
    if migrated:
        db.commit()
        log.info("%d segredo(s) reencriptado(s) com ENCRYPTION_KEY.", migrated)
    return migrated
