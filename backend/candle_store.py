"""
candle_store.py — Repositório de candles no banco (tabela historic_candles), compartilhado pelo app
(MarketDataService, Scanner) e pelos estudos/validações.

Regra: nunca baixar duas vezes o que já está no banco. `ensure_history()` completa só o que falta:
  1. fecha o buraco até o presente (candles novos desde o mais recente guardado);
  2. se o histórico guardado for menor que o alvo do timeframe, busca para TRÁS a partir do candle
     mais antigo guardado (não recomeça do zero).
Séries cujo histórico na OKX acabou antes do alvo são marcadas como esgotadas, para não insistir.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from .database import HistoricCandleModel
from .exchanges.base import CandleBar

log = logging.getLogger("candle_store")

# Candles-alvo por timeframe. 3000 cobre o Scanner (e ~8 anos de 1D); 4h e 1h guardam anos para a
# validação por ano. Custo: ~100 bytes por candle (20.000 candles = ~2 MB por ativo).
DEFAULT_TARGET = 3000
HISTORY_TARGET_BY_TIMEFRAME = {
    "1h": 20000, "2h": 10000, "4h": 8000, "6h": 6000, "12h": 4000,
}
RECENT_CANDLES = 300           # atualização incremental barata (endpoint de candles recentes)
_UPSERT_CHUNK = 2000           # linhas por INSERT (limite de parâmetros do Postgres)

_TF_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000, "12h": 43_200_000,
    "1d": 86_400_000, "1w": 604_800_000,
}


def history_target(timeframe: str) -> int:
    return HISTORY_TARGET_BY_TIMEFRAME.get((timeframe or "").strip().lower(), DEFAULT_TARGET)


def timeframe_ms(timeframe: str) -> Optional[int]:
    return _TF_MS.get((timeframe or "").strip().lower())


def _to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).replace(tzinfo=None)


def _to_ms(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def coverage(db: Session, symbol: str, timeframe: str) -> dict:
    count, oldest, newest = (
        db.query(func.count(HistoricCandleModel.id), func.min(HistoricCandleModel.epoch), func.max(HistoricCandleModel.epoch))
        .filter(HistoricCandleModel.symbol == symbol, HistoricCandleModel.timeframe == timeframe)
        .one()
    )
    return {"count": int(count or 0), "oldest_ms": _to_ms(oldest) if oldest else None,
            "newest_ms": _to_ms(newest) if newest else None}


def load_candles(db: Session, symbol: str, timeframe: str, limit: Optional[int] = None) -> list[CandleBar]:
    """Candles em ordem cronológica. Com `limit`, os `limit` mais recentes."""
    q = (
        db.query(HistoricCandleModel)
        .filter(HistoricCandleModel.symbol == symbol, HistoricCandleModel.timeframe == timeframe)
    )
    if limit:
        rows = list(reversed(q.order_by(HistoricCandleModel.epoch.desc()).limit(limit).all()))
    else:
        rows = q.order_by(HistoricCandleModel.epoch.asc()).all()
    return [
        CandleBar(epoch=_to_ms(r.epoch), open=float(r.open), high=float(r.high), low=float(r.low),
                  close=float(r.close), volume=float(r.volume))
        for r in rows
    ]


def upsert_candles(db: Session, symbol: str, timeframe: str, candles: list) -> int:
    """Grava (ou atualiza) candles; idempotente. Retorna quantos candles foram enviados ao banco."""
    if not candles:
        return 0
    dialect = db.get_bind().dialect.name
    insert = postgresql.insert if dialect == "postgresql" else sqlite.insert
    records = [
        {"symbol": symbol, "timeframe": timeframe, "epoch": _to_dt(c.epoch), "open": float(c.open),
         "high": float(c.high), "low": float(c.low), "close": float(c.close), "volume": float(c.volume)}
        for c in candles
    ]
    for i in range(0, len(records), _UPSERT_CHUNK):
        stmt = insert(HistoricCandleModel).values(records[i:i + _UPSERT_CHUNK])
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "timeframe", "epoch"],
            set_={k: getattr(stmt.excluded, k) for k in ("open", "high", "low", "close", "volume")},
        )
        db.execute(stmt)
    db.commit()
    return len(records)


async def ensure_history(db: Session, ex, symbol: str, timeframe: str, bar: str,
                         target: Optional[int] = None, exhausted: bool = False) -> dict:
    """
    Garante `target` candles armazenados para (símbolo, timeframe) baixando SÓ o que falta.
    `bar` é o código do timeframe na exchange; `ex` precisa de fetch_candles e fetch_history_candles.
    Retorna {"added", "count", "oldest_ms", "newest_ms", "exhausted"}.
    """
    target = target or history_target(timeframe)
    added = 0
    exhausted_now = exhausted
    cov = coverage(db, symbol, timeframe)

    if cov["count"] == 0:
        candles = await ex.fetch_history_candles(symbol, bar, limit=target)
        added += upsert_candles(db, symbol, timeframe, candles)
        if len(candles) < target * 0.95:
            exhausted_now = True
    else:
        # 1) até o presente: se o app ficou fora do ar por mais que a janela recente, usa o histórico
        step = timeframe_ms(timeframe)
        gap = int((time.time() * 1000 - cov["newest_ms"]) / step) + 2 if step else RECENT_CANDLES
        if gap > RECENT_CANDLES - 10:
            tail = await ex.fetch_history_candles(symbol, bar, limit=gap)
        else:
            tail = await ex.fetch_candles(symbol, bar, limit=RECENT_CANDLES)
        added += upsert_candles(db, symbol, timeframe, tail)

        # 2) para trás, a partir do mais antigo guardado
        cov = coverage(db, symbol, timeframe)
        missing = target - cov["count"]
        if missing > target * 0.05 and not exhausted_now:
            older = await ex.fetch_history_candles(symbol, bar, limit=missing, before=cov["oldest_ms"])
            added += upsert_candles(db, symbol, timeframe, older)
            if len(older) < missing * 0.95:
                exhausted_now = True

    cov = coverage(db, symbol, timeframe)
    return {"added": added, **cov, "exhausted": exhausted_now}
