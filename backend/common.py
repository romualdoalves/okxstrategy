"""
common.py — Helpers e constantes compartilhados pelos routers (símbolos, fuso, serviços).
"""

from __future__ import annotations
import logging

import aiohttp
import os

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from .database import BotModel
from .market_data_service import MarketDataService
from .exchanges.factory import build_exchange

log = logging.getLogger("main")


# Offset do fuso horário local em horas (ex: -3 para BRT). Usado para converter
# datas UTC da OKX para o dia local correto.
_TZ_OFFSET: int = int(os.getenv("TZ_OFFSET", "0"))

FIXED_STAKE_USD: float = 100.0

def _local_date_to_utc_range(date_str: str) -> tuple[str, str]:
    """Converte uma data local (YYYY-MM-DD) para o intervalo UTC equivalente."""
    import datetime as _dt
    local_day = _dt.datetime.fromisoformat(date_str)
    utc_start = local_day + _dt.timedelta(hours=-_TZ_OFFSET)
    utc_end   = utc_start + _dt.timedelta(days=1)
    return utc_start.strftime("%Y-%m-%dT%H:%M:%SZ"), utc_end.strftime("%Y-%m-%dT%H:%M:%SZ")

market_data_svc = MarketDataService()

def _env_truthy(name: str, default: str = "") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

def _norm_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper().replace("/", "-")
    if s.endswith("-SWAP"):
        s = s[:-5]
    if s.endswith("-FUTURES"):
        s = s[:-8]
    return s

def _assert_spot_only(symbol: str):
    s = (symbol or "").strip().upper()
    if s.endswith("-SWAP") or s.endswith("-FUTURES"):
        raise HTTPException(400, "Este app opera somente OKX spot. Use o par sem -SWAP/-FUTURES.")

def _find_symbol_conflict(db: Session, symbol: str, *, exclude_bot_id: int | None = None, active_only: bool = False):
    query = db.query(BotModel).filter(func.upper(BotModel.symbol) == _norm_symbol(symbol))
    if exclude_bot_id is not None:
        query = query.filter(BotModel.id != exclude_bot_id)
    if active_only:
        query = query.filter(BotModel.active == True)
    return query.first()

def _assert_symbol_available(db: Session, symbol: str, *, exclude_bot_id: int | None = None, active_only: bool = False):
    conflict = _find_symbol_conflict(
        db,
        symbol,
        exclude_bot_id=exclude_bot_id,
        active_only=active_only,
    )
    if conflict:
        scope = "em execução" if active_only else "configurado"
        raise HTTPException(
            400,
            f"Já existe um bot {scope} para o ativo {_norm_symbol(symbol)} ('{conflict.name}'). "
            "OKX mantém uma única posição por ativo — use um ativo diferente."
        )

async def _get_symbol_tradability(symbol: str, *, demo: bool = True) -> dict:
    """Retorna status de negociabilidade do par para a conta OKX atual."""
    sym = _norm_symbol(symbol)
    _assert_spot_only(sym)
    try:
        async with aiohttp.ClientSession() as session:
            ex = build_exchange(session, demo=demo)
            checker = getattr(ex, "get_spot_trade_status", None)
            if callable(checker):
                out = await checker(sym)
                if isinstance(out, dict):
                    out.setdefault("symbol", sym)
                    return out
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Falha ao validar negociabilidade de %s: %s", sym, exc)

    return {
        "symbol": sym,
        "tradable": True,
        "reason": "Validação de elegibilidade indisponível; criação permitida.",
        "status": "unknown",
        "source": "fallback",
    }

async def _assert_symbol_tradable(symbol: str, *, demo: bool = True):
    status = await _get_symbol_tradability(symbol, demo=demo)
    if status.get("tradable") is False:
        reason = status.get("reason") or "Par indisponível para negociação nesta conta."
        raise HTTPException(400, f"Par {_norm_symbol(symbol)} bloqueado: {reason}")
