"""
main.py — FastAPI: REST API + WebSocket hub — OKXStrategy.

Só monta a aplicação: carrega o .env, registra routers, middlewares e eventos de ciclo de vida.
Rotas ficam em backend/routers/, startup/shutdown em lifecycle.py, helpers em common.py.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# O .env precisa ser carregado ANTES de importar os módulos do backend: database, risk,
# bot_manager etc. leem variáveis de ambiente no momento do import.
try:
    from dotenv import load_dotenv
    # Carrega OKXStrategy/.env primeiro (tem EXCHANGE_PROVIDER=okx, DATABASE_URL okx_strategy)
    # override=False: não sobrescreve vars já definidas no ambiente (ex: set do .bat)
    _okx_env = Path(__file__).resolve().parents[2] / "OKXStrategy" / ".env"
    if _okx_env.exists():
        load_dotenv(_okx_env, override=False)
    # Fallback local: OKXStrategy/.env ao executar diretamente a partir do repo
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False, encoding="utf-8")
except ImportError:
    pass

from . import lifecycle  # noqa: E402
from .routers import (  # noqa: E402
    account, auto_scan, backtest, bot_analysis, bots, integrity, market, market_data, risk,
    spa, strategies, system, trades, ws,
)
from .security import ApiAuthMiddleware, get_cors_origins  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")

log = logging.getLogger("main")

app = FastAPI(title="OKXStrategy API", version="1.0.0")

app.include_router(market_data.router, prefix="/api")
for _router in (system.router, strategies.router, market.router, bots.router, bot_analysis.router,
                account.router, trades.router, integrity.router, backtest.router, auto_scan.router,
                risk.router):
    app.include_router(_router)

# Ordem importa: o último middleware adicionado é o mais externo. CORS fica por fora
# para responder preflight (OPTIONS) e anexar headers também nas respostas 401.
app.add_middleware(ApiAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_event_handler("startup", lifecycle.startup)
app.add_event_handler("shutdown", lifecycle.shutdown)

if spa.STATIC_ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=spa.STATIC_ASSETS_DIR), name="assets")

app.include_router(ws.router)
app.include_router(spa.router)   # sempre por último: contém o catch-all da SPA
