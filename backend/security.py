"""
security.py — Autenticação da API (HTTP + WebSocket) e política de CORS.

Modos:
  - API_AUTH_TOKEN definido: exige `Authorization: Bearer <token>` (HTTP) ou
    `?token=<token>` (WebSocket, pois o browser não permite headers customizados).
  - API_AUTH_TOKEN ausente: fail-closed — só aceita clientes em loopback
    (uso local de desenvolvimento). Qualquer outro cliente recebe 401.

Isentos: /api/health (healthcheck), preflight OPTIONS e a SPA estática (não-/api, não-/ws).
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from urllib.parse import parse_qs

log = logging.getLogger("security")

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
PUBLIC_PATHS = {"/api/health"}
DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:8000", "http://127.0.0.1:8000",
]


def get_auth_token() -> str:
    return os.getenv("API_AUTH_TOKEN", "").strip()


def get_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if not raw:
        return DEFAULT_CORS_ORIGINS
    return [o.strip() for o in raw.split(",") if o.strip()]


def _token_matches(provided: str, expected: str) -> bool:
    return bool(provided) and hmac.compare_digest(provided.encode(), expected.encode())


class ApiAuthMiddleware:
    """Middleware ASGI puro (BaseHTTPMiddleware não cobre WebSocket)."""

    def __init__(self, app):
        self.app = app
        if not get_auth_token():
            log.warning(
                "API_AUTH_TOKEN não definido: API restrita a clientes loopback. "
                "Defina API_AUTH_TOKEN para expor o serviço (ex.: Docker/VPS)."
            )

    def _authorized(self, scope) -> bool:
        expected = get_auth_token()
        if not expected:
            client = scope.get("client")
            return bool(client) and client[0] in LOOPBACK_HOSTS

        if scope["type"] == "websocket":
            qs = parse_qs(scope.get("query_string", b"").decode())
            return _token_matches((qs.get("token") or [""])[0], expected)

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        auth = headers.get(b"authorization", b"").decode()
        scheme, _, value = auth.partition(" ")
        return scheme.lower() == "bearer" and _token_matches(value.strip(), expected)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        protected = path.startswith("/api/") or path == "/ws"
        if (not protected
                or path in PUBLIC_PATHS
                or (scope["type"] == "http" and scope.get("method") == "OPTIONS")
                or self._authorized(scope)):
            return await self.app(scope, receive, send)

        if scope["type"] == "websocket":
            await receive()                      # websocket.connect
            return await send({"type": "websocket.close", "code": 4401})

        body = json.dumps({"detail": "Não autorizado"}).encode()
        await send({
            "type": "http.response.start", "status": 401,
            "headers": [(b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"www-authenticate", b"Bearer")],
        })
        await send({"type": "http.response.body", "body": body})
