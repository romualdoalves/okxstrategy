import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .database import HistoricCandleModel, SessionLocal, TrackedSymbolModel
from .exchanges.factory import build_exchange, map_timeframe_for_history

log = logging.getLogger("market_data")

# Histórico alvo por série (símbolo/timeframe), usado pelo backtest. O 1º sync busca isso tudo via
# history-candles; depois só os candles recentes (o /market/candles entrega ~1440, mas a cada 5 min
# repetir 1500 por série era carga desnecessária).
HISTORY_TARGET_CANDLES = 3000
_RECENT_CANDLES = 300

_ALLOWED_TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1D", "1W"}


def _is_okx_spot_symbol(symbol: str) -> bool:
    s = (symbol or "").strip().upper().replace("/", "-")
    return bool(s) and "-" in s and not s.endswith("-SWAP") and not s.endswith("-FUTURES")


class MarketDataService:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self.loop_interval = 300
        self._history_exhausted: set[tuple[str, str]] = set()   # séries com menos histórico que o alvo

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._sync_loop())
        log.info("MarketDataService iniciado.")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("MarketDataService parado.")

    async def _sync_loop(self):
        await asyncio.sleep(5)
        while self._running:
            try:
                await self._sync_all_tracked()
            except Exception as exc:
                log.error("Erro no loop do MarketDataService: %s", exc)
            await asyncio.sleep(self.loop_interval)

    async def _sync_all_tracked(self):
        db: Session = SessionLocal()
        try:
            tracked = db.query(TrackedSymbolModel).filter(TrackedSymbolModel.is_active == True).all()
            for item in tracked:
                if not self._running:
                    break
                if not _is_okx_spot_symbol(item.symbol):
                    log.warning("[MarketData] Ignorando símbolo não-spot OKX: %s", item.symbol)
                    continue
                for tf in (item.timeframes or []):
                    try:
                        await self._sync_symbol_timeframe(db, item.symbol, tf)
                    except Exception as exc:
                        log.error("[MarketData] Erro ao sincronizar %s (%s): %s", item.symbol, tf, exc)
                item.last_sync = datetime.now(timezone.utc).replace(tzinfo=None)
                db.commit()
                await asyncio.sleep(0.5)
        finally:
            db.close()

    async def _sync_symbol_timeframe(self, db: Session, symbol: str, timeframe: str):
        tf = (timeframe or "").strip()
        if tf not in _ALLOWED_TIMEFRAMES:
            log.warning("[MarketData] Timeframe não suportado para %s: %s", symbol, tf)
            return

        bar = map_timeframe_for_history(tf)
        stored = (
            db.query(func.count(HistoricCandleModel.epoch))
            .filter(HistoricCandleModel.symbol == symbol, HistoricCandleModel.timeframe == tf)
            .scalar()
            or 0
        )
        deep = stored < HISTORY_TARGET_CANDLES * 0.95 and (symbol, tf) not in self._history_exhausted
        async with aiohttp.ClientSession() as http_session:
            ex = build_exchange(http_session)
            if deep:
                candles = await ex.fetch_history_candles(symbol, bar, limit=HISTORY_TARGET_CANDLES)
                if len(candles) < HISTORY_TARGET_CANDLES * 0.95:
                    self._history_exhausted.add((symbol, tf))   # a OKX não tem mais histórico: não insistir
            else:
                candles = await ex.fetch_candles(symbol, bar, limit=_RECENT_CANDLES)

        if not candles:
            return

        records = []
        for c in candles:
            dt = datetime.fromtimestamp(int(c.epoch) / 1000, tz=timezone.utc).replace(tzinfo=None)
            records.append({
                "symbol": symbol,
                "timeframe": tf,
                "epoch": dt,
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            })

        stmt = insert(HistoricCandleModel).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "timeframe", "epoch"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
            },
        )
        db.execute(stmt)
        db.commit()
        log.info("[MarketData] %d candles sincronizados para %s (%s)", len(records), symbol, tf)

    async def sync_symbol(self, symbol: str):
        sym = (symbol or "").strip().upper().replace("/", "-")
        if not _is_okx_spot_symbol(sym):
            return
        db: Session = SessionLocal()
        try:
            tracked = db.query(TrackedSymbolModel).filter(TrackedSymbolModel.symbol == sym).first()
            if not tracked:
                return
            for tf in (tracked.timeframes or []):
                await self._sync_symbol_timeframe(db, sym, tf)
            tracked.last_sync = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        finally:
            db.close()

    async def force_sync(self, symbol: str, timeframe: Optional[str] = None):
        sym = (symbol or "").strip().upper().replace("/", "-")
        if not _is_okx_spot_symbol(sym):
            return

        db: Session = SessionLocal()
        try:
            tracked = db.query(TrackedSymbolModel).filter(TrackedSymbolModel.symbol == sym).first()
            if not tracked:
                return
            tfs = [timeframe] if (timeframe and timeframe in (tracked.timeframes or [])) else list(tracked.timeframes or [])
            for tf in tfs:
                # Mantem histórico: apenas upsert incremental sem truncar candles antigos.
                await self._sync_symbol_timeframe(db, sym, tf)
            tracked.last_sync = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        finally:
            db.close()

    async def persist_candles(self, symbol: str, candles_by_tf: dict) -> None:
        """Persiste incrementalmente candles já buscados (ex: Scanner) em historic_candles.

        Reutiliza o mesmo upsert do _sync_symbol_timeframe sem chamar a OKX novamente.
        Silencia exceções para não interromper o chamador (fire-and-forget).
        """
        if not candles_by_tf:
            return
        db: Session = SessionLocal()
        try:
            total = 0
            for tf, candles in candles_by_tf.items():
                if not candles or tf not in _ALLOWED_TIMEFRAMES:
                    continue
                records = [
                    {
                        "symbol": symbol,
                        "timeframe": tf,
                        "epoch": datetime.fromtimestamp(int(c.epoch) / 1000, tz=timezone.utc).replace(tzinfo=None),
                        "open": float(c.open),
                        "high": float(c.high),
                        "low": float(c.low),
                        "close": float(c.close),
                        "volume": float(c.volume),
                    }
                    for c in candles
                ]
                if not records:
                    continue
                stmt = insert(HistoricCandleModel).values(records)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["symbol", "timeframe", "epoch"],
                    set_={
                        "open": stmt.excluded.open,
                        "high": stmt.excluded.high,
                        "low": stmt.excluded.low,
                        "close": stmt.excluded.close,
                        "volume": stmt.excluded.volume,
                    },
                )
                db.execute(stmt)
                total += len(records)
            if total:
                db.commit()
                log.info("[MarketData] Scanner: %d candles persistidos para %s", total, symbol)
        except Exception as exc:
            log.warning("[MarketData] Falha ao persistir candles do Scanner (%s): %s", symbol, exc)
        finally:
            db.close()
