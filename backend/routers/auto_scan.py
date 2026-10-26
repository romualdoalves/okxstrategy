"""
Auto-scan horário: ciclo, loop em background e rotas de status/controle.
"""

from __future__ import annotations
from fastapi import APIRouter
import logging

import asyncio
from typing import Optional
from datetime import datetime, timedelta

from pydantic import BaseModel
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import AutoScanRunLogModel, AutoScanStateModel, BotModel, SessionLocal, TrackedSymbolModel, get_db
from ..bot_manager import manager
from ..risk import guard as risk_guard
from .bots import BotCreate, create_bot, start_bot
from ..common import FIXED_STAKE_USD, _norm_symbol
from .backtest import _run_category_backtest_core

router = APIRouter()
log = logging.getLogger("main")


# ── Auto-Scan horário ──────────────────────────────────────────────────────────
# Roda o Scanner (Todas as categorias) a cada hora cheia, um ativo habilitado por
# vez (rotação), e cria + inicia automaticamente o bot da melhor estratégia se o
# score composto atingir _AUTO_SCAN_MIN_SCORE. Sempre em modo demo, por segurança
# — promover para live é decisão manual, não desta rotina.
_AUTO_SCAN_CATEGORIES = ["TF", "MR", "PA", "SC", "RG", "IF", "NW"]

_AUTO_SCAN_MIN_SCORE = 9.0

def _get_auto_scan_state(db: Session) -> AutoScanStateModel:
    state = db.get(AutoScanStateModel, 1)
    if not state:
        state = AutoScanStateModel(id=1, enabled=False, cursor_symbol=None)
        db.add(state)
        db.commit()
        db.refresh(state)
    return state

def _auto_scan_eligible_symbols(db: Session) -> list[str]:
    """Ativos habilitados (TrackedSymbolModel.is_active) que ainda não têm bot —
    'um ativo por bot' já impede criar outro, então nem tenta os que já têm."""
    tracked = db.query(TrackedSymbolModel.symbol).filter(TrackedSymbolModel.is_active == True).all()  # noqa: E712
    used = {_norm_symbol(b.symbol) for b in db.query(BotModel.symbol).all()}
    return sorted({_norm_symbol(s) for (s,) in tracked} - used)

async def _auto_scan_cycle():
    """Executa um ciclo: escolhe o próximo ativo elegível na rotação, roda o Scanner
    em todas as categorias para ele, e cria+inicia o bot se algum score >= threshold."""
    db = SessionLocal()
    try:
        state = _get_auto_scan_state(db)
        if not state.enabled:
            return

        eligible = _auto_scan_eligible_symbols(db)
        if not eligible:
            db.add(AutoScanRunLogModel(note="Nenhum ativo habilitado disponível (todos já têm bot, ou nenhum rastreado)."))
            db.commit()
            log.info("[AutoScan] Nenhum ativo elegível — ciclo pulado.")
            return

        # Rotação: próximo símbolo depois do cursor atual, com wraparound.
        symbol = eligible[0]
        if state.cursor_symbol and state.cursor_symbol in eligible:
            idx = eligible.index(state.cursor_symbol)
            symbol = eligible[(idx + 1) % len(eligible)]

        log.info("[AutoScan] Ciclo iniciado para %s (%d ativo(s) elegível(is)).", symbol, len(eligible))

        best_score = -1.0
        best_row: dict | None = None
        for category in _AUTO_SCAN_CATEGORIES:
            try:
                result = await _run_category_backtest_core(category=category, symbol=symbol, strict=True)
            except HTTPException:
                continue
            except Exception as exc:
                log.warning("[AutoScan] Falha ao rodar categoria %s para %s: %s", category, symbol, exc)
                continue
            for row in result.get("results", []):
                rec = row.get("recommendation") or {}
                score = rec.get("score")
                if rec.get("verdict") == "N/A" or score is None:
                    continue
                if score > best_score:
                    best_score = score
                    best_row = row

        state.cursor_symbol = symbol
        db.commit()

        bot_created = False
        bot_id = None
        note = f"Melhor score para {symbol}: {best_score:.2f}" if best_row else f"Nenhum resultado válido para {symbol}."

        auto_allowed = True
        if best_row and best_score >= _AUTO_SCAN_MIN_SCORE:
            auto_ids = [r[0] for r in db.query(AutoScanRunLogModel.bot_id)
                        .filter(AutoScanRunLogModel.bot_id.isnot(None)).all()]
            active_auto = (db.query(func.count(BotModel.id))
                           .filter(BotModel.id.in_(auto_ids), BotModel.active.is_(True)).scalar() or 0) if auto_ids else 0
            auto_allowed, why = risk_guard.auto_scan_allowed(db, manager._instances, active_auto)
            if not auto_allowed:
                note = f"Score {best_score:.2f} qualificava, mas o bot não foi criado: {why}"
                log.warning("[AutoScan] %s", note)

        if best_row and best_score >= _AUTO_SCAN_MIN_SCORE and auto_allowed:
            try:
                payload = BotCreate(
                    name=f"{best_row['strategy_id']} - {best_row.get('strategy_name', 'Auto-Scan')} ({symbol})",
                    strategy_id=best_row["strategy_id"],
                    symbol=symbol,
                    timeframe=best_row.get("timeframe") or "15m",
                    demo=True,   # auto-scan nunca cria bot live — promoção é manual
                    stake_usd=FIXED_STAKE_USD,
                    leverage=1,
                    stop_loss_usd=-50.0,
                    strategy_params={},
                )
                created = await create_bot(payload, db)
                bot_id = created["id"]
                await start_bot(bot_id, db)
                bot_created = True
                note = (f"Score {best_score:.2f} >= {_AUTO_SCAN_MIN_SCORE} — bot {bot_id} "
                        f"({best_row['strategy_id']}/{symbol}) criado e iniciado automaticamente.")
                log.info("[AutoScan] %s", note)
            except Exception as exc:
                note = f"Score {best_score:.2f} qualificava, mas criação/início do bot falhou: {exc}"
                log.error("[AutoScan] %s", note)

        db.add(AutoScanRunLogModel(
            symbol=symbol,
            category=best_row["strategy_id"][:2].upper() if best_row else None,
            best_score=best_score if best_row else None,
            best_strategy_id=best_row["strategy_id"] if best_row else None,
            bot_created=bot_created,
            bot_id=bot_id,
            note=note,
        ))
        db.commit()
    except Exception as exc:
        log.error("[AutoScan] Ciclo falhou: %s", exc)
    finally:
        db.close()

async def _auto_scan_loop():
    """Dorme até a próxima hora cheia (UTC) e roda um ciclo, para sempre."""
    while True:
        now = datetime.utcnow()
        next_hour = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        await asyncio.sleep(max(5.0, (next_hour - now).total_seconds()))
        try:
            await _auto_scan_cycle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("[AutoScan] Erro não tratado no loop: %s", exc)

@router.get("/api/auto-scan/status")
def get_auto_scan_status(db: Session = Depends(get_db)):
    state = _get_auto_scan_state(db)
    last_run = db.query(AutoScanRunLogModel).order_by(AutoScanRunLogModel.created_at.desc()).first()
    now = datetime.utcnow()
    next_run = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
    return {
        "enabled": state.enabled,
        "cursor_symbol": state.cursor_symbol,
        "min_score": _AUTO_SCAN_MIN_SCORE,
        "next_run_at": next_run.isoformat() + "Z",
        "last_run": {
            "created_at": last_run.created_at.isoformat() + "Z",
            "symbol": last_run.symbol,
            "category": last_run.category,
            "best_score": last_run.best_score,
            "best_strategy_id": last_run.best_strategy_id,
            "bot_created": last_run.bot_created,
            "bot_id": last_run.bot_id,
            "note": last_run.note,
        } if last_run else None,
    }

class AutoScanToggleRequest(BaseModel):
    enabled: bool

@router.post("/api/auto-scan/toggle")
def toggle_auto_scan(payload: AutoScanToggleRequest, db: Session = Depends(get_db)):
    state = _get_auto_scan_state(db)
    state.enabled = payload.enabled
    db.commit()
    log.info("[AutoScan] %s via API.", "Ativado" if payload.enabled else "Desativado")
    return {"enabled": state.enabled}

@router.get("/api/auto-scan/runs")
def list_auto_scan_runs(
    limit: int = 20,
    symbol: Optional[str] = None,
    strategy_id: Optional[str] = None,
    category: Optional[str] = None,
    date_from: Optional[str] = None,   # "YYYY-MM-DD", início do dia (UTC)
    date_to: Optional[str] = None,     # "YYYY-MM-DD", fim do dia (UTC, inclusive)
    db: Session = Depends(get_db),
):
    """Histórico de ciclos do Auto-Scan, filtrável por ativo, estratégia, categoria e período."""
    q = db.query(AutoScanRunLogModel)
    if symbol:
        q = q.filter(AutoScanRunLogModel.symbol == _norm_symbol(symbol))
    if strategy_id:
        q = q.filter(AutoScanRunLogModel.best_strategy_id == strategy_id.upper())
    if category:
        q = q.filter(AutoScanRunLogModel.category == category.upper())
    if date_from:
        try:
            q = q.filter(AutoScanRunLogModel.created_at >= datetime.fromisoformat(date_from))
        except ValueError:
            raise HTTPException(400, f"date_from inválido: {date_from!r} (use YYYY-MM-DD)")
    if date_to:
        try:
            end = datetime.fromisoformat(date_to) + timedelta(days=1)
            q = q.filter(AutoScanRunLogModel.created_at < end)
        except ValueError:
            raise HTTPException(400, f"date_to inválido: {date_to!r} (use YYYY-MM-DD)")

    runs = q.order_by(AutoScanRunLogModel.created_at.desc()).limit(min(limit, 500)).all()
    return [
        {
            "id": r.id, "created_at": r.created_at.isoformat() + "Z", "symbol": r.symbol,
            "category": r.category, "best_score": r.best_score, "best_strategy_id": r.best_strategy_id,
            "bot_created": r.bot_created, "bot_id": r.bot_id, "note": r.note,
        }
        for r in runs
    ]
