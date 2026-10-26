"""
lifecycle.py — Eventos de startup/shutdown da aplicação.

Startup: cria tabelas, reencripta segredos, migrações automáticas de bots, reinicia bots
ativos e dispara as tarefas em background. Shutdown: avisa no Telegram e para os bots.
"""
from __future__ import annotations

import asyncio
import logging

from .bot_manager import manager
from .common import market_data_svc
from .database import BotModel, SessionLocal, create_tables
from .feeds.economic_calendar import calendar_feed
from .notifications import TelegramNotifier, build_stop_msg
from .routers.auto_scan import _auto_scan_loop
from .routers.trades import _sync_trade_fees_impl

log = logging.getLogger("main")


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
