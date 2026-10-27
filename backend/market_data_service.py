import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from sqlalchemy.orm import Session

from .candle_store import ensure_history, upsert_candles
from .database import SessionLocal, TrackedSymbolModel
from .exchanges.factory import build_exchange, map_timeframe_for_history

log = logging.getLogger("market_data")

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
        async with aiohttp.ClientSession() as http_session:
            ex = build_exchange(http_session)
            result = await ensure_history(
                db, ex, symbol, tf, bar, exhausted=(symbol, tf) in self._history_exhausted)
        if result["exhausted"]:
            self._history_exhausted.add((symbol, tf))      # a OKX não tem mais histórico: não insistir
        if result["added"]:
            log.info("[MarketData] %d candles sincronizados para %s (%s); total armazenado %d",
                     result["added"], symbol, tf, result["count"])
        return

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
        """Persiste candles já buscados (ex.: pelo Scanner) em historic_candles, sem chamar a OKX de novo.

        Silencia exceções para não interromper o chamador (fire-and-forget).
        """
        if not candles_by_tf:
            return
        db: Session = SessionLocal()
        try:
            total = 0
            for tf, candles in candles_by_tf.items():
                if candles and tf in _ALLOWED_TIMEFRAMES:
                    total += upsert_candles(db, symbol, tf, candles)
            if total:
                log.info("[MarketData] Scanner: %d candles persistidos para %s", total, symbol)
        except Exception as exc:
            log.warning("[MarketData] Falha ao persistir candles do Scanner (%s): %s", symbol, exc)
        finally:
            db.close()
