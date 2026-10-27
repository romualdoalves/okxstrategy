"""
lifecycle.py — Eventos de startup/shutdown da aplicação.

Startup: cria tabelas, reencripta segredos, migrações automáticas de bots, reinicia bots
ativos e dispara as tarefas em background. Shutdown: avisa no Telegram e para os bots.
"""
from __future__ import annotations

import asyncio
import logging

from .backtest_worker import shutdown_pool as shutdown_backtest_pool
from .bot_manager import manager
from .common import market_data_svc
from .database import BotModel, SessionLocal, create_tables
from .feeds.economic_calendar import calendar_feed
from .notifications import TelegramNotifier, build_stop_msg
from .routers.trades import _sync_trade_fees_impl

log = logging.getLogger("main")


def normalize_bots(db) -> None:
    """
    Ajustes idempotentes aplicados a todos os bots a cada startup:
      - IDs legados da Fábrica antiga (F001/F002) -> taxonomia semântica;
      - símbolos spot (remove -SWAP/-FUTURES);
      - stake fixo de US$100 e alavancagem 1x (política do projeto, ver SKILL.md).
    O timeframe NÃO é tocado: ele é escolhido na criação/edição do bot e precisa sobreviver a restarts
    (antes, um mapa de "timeframes recomendados" sobrescrevia a escolha do usuário a cada startup).
    """
    from sqlalchemy import text
    db.execute(text("UPDATE bots SET strategy_id = 'TF014' WHERE strategy_id = 'F001'"))
    db.execute(text("UPDATE bots SET strategy_id = 'PA007' WHERE strategy_id = 'F002'"))
    db.execute(text("UPDATE bots SET symbol = REPLACE(symbol, '-SWAP', '') WHERE symbol LIKE '%-SWAP'"))
    db.execute(text("UPDATE bots SET symbol = REPLACE(symbol, '-FUTURES', '') WHERE symbol LIKE '%-FUTURES'"))
    db.execute(text("UPDATE bots SET leverage = 1 WHERE leverage IS NULL OR leverage <> 1"))
    db.execute(text("UPDATE bots SET stake_usd = 100.0 WHERE stake_usd IS NULL OR stake_usd <> 100.0"))
    db.commit()


async def startup():
    create_tables()
    log.info("Banco de dados inicializado.")

    from .crypto_utils import rotate_secrets
    with SessionLocal() as _db:
        rotate_secrets(_db)

    # Inicia o feed do calendário económico do provider atual, se configurado
    await calendar_feed.start()
    await market_data_svc.start()

    # Normalização dos bots no startup (IDs legados, símbolos spot, stake/alavancagem fixos)
    db = SessionLocal()
    try:
        normalize_bots(db)
        log.info("Normalização de IDs, símbolos, stakes e alavancagem concluída.")

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
    shutdown_backtest_pool()

    log.info("[Shutdown] Graceful shutdown completo.")
