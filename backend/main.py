"""
main.py — FastAPI: REST API + WebSocket hub — OKXStrategy.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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

from .routers import (
    account, auto_scan, backtest, bot_analysis, bots, integrity, market, market_data, risk,
    strategies, system, trades,
)
from .security import ApiAuthMiddleware, get_cors_origins
from .database import BotModel, SessionLocal, create_tables
from .notifications import TelegramNotifier, build_stop_msg
from .feeds.economic_calendar import calendar_feed
from .bot_manager import manager
from .routers.auto_scan import _auto_scan_loop
from .routers.trades import _sync_trade_fees_impl
from .common import market_data_svc

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")

log = logging.getLogger("main")

# ── App ───────────────────────────────────────────────────────────────────────
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

STATIC_FRONTEND_DIR = Path(__file__).resolve().parent / "static_frontend"

STATIC_INDEX = STATIC_FRONTEND_DIR / "index.html"

STATIC_ASSETS_DIR = STATIC_FRONTEND_DIR / "assets"

if STATIC_ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_ASSETS_DIR), name="assets")


# ── Startup ───────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    create_tables()
    log.info("Banco de dados inicializado.")

    from .crypto_utils import rotate_secrets
    with SessionLocal() as _db:
        rotate_secrets(_db)

    # Inicia o feed do calendário económico do provider atual, se configurado
    await calendar_feed.start()
    await market_data_svc.start()

    # Migração automática: remove -SWAP e força stake fixo global de 100
    db = SessionLocal()
    try:
        from sqlalchemy import text
        # Migração automática de IDs legados → taxonomia semântica
        # F001/F002 (Factory antiga) → semântico
        db.execute(text("UPDATE bots SET strategy_id = 'TF014' WHERE strategy_id = 'F001'"))
        db.execute(text("UPDATE bots SET strategy_id = 'PA007' WHERE strategy_id = 'F002'"))
        
        # Migração Global de Timeframes para os recomendados (nova taxonomia)
        tf_recommendations = {
            "TF008": "1D",
            "PA002": "4h", "IF001": "4h",
            "TF002": "1h", "TF003": "1h", "TF011": "1h", "MR001": "1h", "PA003": "1h", "RG002": "1h", "PA005": "1h",
            "TF001": "15m", "TF013": "15m", "TF004": "15m", "PA001": "15m", "TF010": "15m", "SC003": "15m", "NW001": "15m", "TF007": "15m", "PA007": "15m", "IF003": "15m",
            "SC001": "5m",
            "PA004": "15m",
            "MR002": "15m",
            "MR005": "15m",
            "IF002": "5m",
        }
        for strat_id, tf in tf_recommendations.items():
            db.execute(text("UPDATE bots SET timeframe = :tf WHERE strategy_id = :sid"), {"tf": tf, "sid": strat_id})

        db.execute(text("UPDATE bots SET symbol = REPLACE(symbol, '-SWAP', '') WHERE symbol LIKE '%-SWAP'"))
        db.execute(text("UPDATE bots SET symbol = REPLACE(symbol, '-FUTURES', '') WHERE symbol LIKE '%-FUTURES'"))
        db.execute(text("UPDATE bots SET leverage = 1 WHERE leverage IS NULL OR leverage <> 1"))
        db.execute(text("UPDATE bots SET stake_usd = 100.0 WHERE stake_usd IS NULL OR stake_usd <> 100.0"))
        db.commit()
        log.info("Migração de IDs, símbolos, stakes e TIMEFRAMES concluída.")

        active = db.query(BotModel).filter(BotModel.active == True).all()
        for bot in active:
            try:
                manager.start_bot(bot)
                log.info("Bot %d re-iniciado automaticamente.", bot.id)
            except ValueError as ve:
                log.warning("Bot %d não iniciado: %s", bot.id, ve)
            except Exception as e:
                log.error("Bot %d falhou no startup: %s", bot.id, e)
    finally:
        db.close()

    # Reconciliação periódica: detecta posições fechadas externamente na OKX
    asyncio.create_task(manager._reconcile_loop())

    # Auto-Scan horário: roda o Scanner (Todas as categorias) a cada hora cheia,
    # um ativo habilitado por vez, criando+iniciando bot se score >= threshold
    asyncio.create_task(_auto_scan_loop())

    # Sincronização de taxas de corretagem: preenche fee nos trades recentes sem dados
    async def _startup_fee_sync():
        await asyncio.sleep(10)   # aguarda exchange estar disponível
        db_s = SessionLocal()
        try:
            result = await _sync_trade_fees_impl(db_s, days_back=30)
            log.info("Sync de corretagem no startup: %s", result)
        except Exception as exc:
            log.warning("Sync de corretagem no startup falhou: %s", exc)
        finally:
            db_s.close()
    asyncio.create_task(_startup_fee_sync())

@app.on_event("shutdown")
async def shutdown():
    """
    Graceful shutdown: notifica Telegram, aguarda fills pendentes,
    e para todos os bots de forma ordenada.
    """
    import asyncio as _asyncio
    log.info("[Shutdown] Iniciando graceful shutdown...")

    # 1. Notifica Telegram
    notifier = TelegramNotifier()
    if notifier.enabled:
        tasks = []
        for inst in manager._instances.values():
            if inst.status in ("running", "maintenance"):
                tasks.append(notifier.send(build_stop_msg(
                    bot_name  = inst.config.name,
                    demo      = inst.config.demo,
                    symbol    = inst.config.symbol,
                    daily_pnl = inst._daily_pnl,
                    wins      = inst._wins,
                    losses    = inst._losses,
                    reason    = "Servidor desligado",
                )))
        if tasks:
            await _asyncio.gather(*tasks, return_exceptions=True)

    # 2. Aguarda confirmação de fills pendentes (3s para WS chegar)
    await _asyncio.sleep(3)

    # 3. Para todos os bots de forma ordenada
    for inst in list(manager._instances.values()):
        inst.stop()

    # 4. Aguarda tasks terminarem (máx 10s)
    pending_tasks = [
        inst._task for inst in manager._instances.values()
        if inst._task and not inst._task.done()
    ]
    if pending_tasks:
        await _asyncio.wait_for(
            _asyncio.gather(*pending_tasks, return_exceptions=True),
            timeout=10.0,
        )

    await market_data_svc.stop()

    log.info("[Shutdown] Graceful shutdown completo.")


# ── WebSocket: tempo real ──────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    manager.register_ws(queue)
    try:
        while True:
            msg = await queue.get()
            await ws.send_text(msg)
    except WebSocketDisconnect:
        pass
    finally:
        manager.unregister_ws(queue)

@app.get("/{full_path:path}", include_in_schema=False)
def serve_spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(404, "Not found")
    if STATIC_INDEX.exists():
        return FileResponse(STATIC_INDEX)
    raise HTTPException(404, "Frontend build not found")
