from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import HistoricCandleModel, SessionLocal, TrackedSymbolModel
from ..exchanges.factory import get_ranked_assets_universe
from ..market_data_service import MarketDataService

router = APIRouter(prefix="/market-data", tags=["Market Data"])

_ALLOWED_TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1D", "1W"}


def _norm_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper().replace("/", "-")


def _assert_okx_spot_symbol(symbol: str):
    s = _norm_symbol(symbol)
    if "-" not in s or s.endswith("-SWAP") or s.endswith("-FUTURES"):
        raise HTTPException(400, "Somente ativos spot OKX são permitidos (ex: BTC-USDT).")


def get_service() -> MarketDataService:
    from ..main import market_data_svc

    return market_data_svc


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class TrackRequest(BaseModel):
    symbol: str
    timeframes: list[str]


class BootstrapRequest(BaseModel):
    symbols: list[str] = []
    timeframes: list[str] = ["15m", "1h", "4h"]


class BulkSyncRequest(BaseModel):
    timeframe: str | None = None


@router.get("/tracked")
def get_tracked_symbols(db: Session = Depends(get_db)):
    tracked = db.query(TrackedSymbolModel).all()
    out = []
    for row in tracked:
        for tf in (row.timeframes or []):
            count, first_dt, last_dt = db.query(
                func.count(HistoricCandleModel.id),
                func.min(HistoricCandleModel.epoch),
                func.max(HistoricCandleModel.epoch),
            ).filter(
                HistoricCandleModel.symbol == row.symbol,
                HistoricCandleModel.timeframe == tf,
            ).first()
            out.append(
                {
                    "id": f"{row.id}:{tf}",
                    "symbol": row.symbol,
                    "timeframe": tf,
                    "is_active": bool(row.is_active),
                    "last_sync": row.last_sync.isoformat() if row.last_sync else None,
                    "candle_count": int(count or 0),
                    "first_candle": first_dt.isoformat() if first_dt else None,
                    "last_candle": last_dt.isoformat() if last_dt else None,
                }
            )
    return out


@router.post("/track")
async def add_tracked_symbol(
    req: TrackRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    svc: MarketDataService = Depends(get_service),
):
    symbol = _norm_symbol(req.symbol)
    _assert_okx_spot_symbol(symbol)

    tfs = []
    for tf in (req.timeframes or []):
        tf_norm = (tf or "").strip()
        if tf_norm in _ALLOWED_TIMEFRAMES:
            tfs.append(tf_norm)
    if not tfs:
        raise HTTPException(400, "Nenhum timeframe válido informado.")

    tracked = db.query(TrackedSymbolModel).filter(TrackedSymbolModel.symbol == symbol).first()
    if tracked:
        tracked.timeframes = sorted(set((tracked.timeframes or []) + tfs))
        tracked.is_active = True
    else:
        tracked = TrackedSymbolModel(symbol=symbol, timeframes=sorted(set(tfs)), is_active=True)
        db.add(tracked)
    db.commit()

    background_tasks.add_task(svc.sync_symbol, symbol)
    return {"status": "ok", "symbol": symbol, "timeframes": tracked.timeframes, "sync": "started"}


@router.post("/bootstrap-defaults")
async def bootstrap_defaults(
    req: BootstrapRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    svc: MarketDataService = Depends(get_service),
):
    base_symbols = req.symbols or get_ranked_assets_universe()
    symbols = []
    for sym in base_symbols:
        norm = _norm_symbol(sym)
        try:
            _assert_okx_spot_symbol(norm)
            symbols.append(norm)
        except HTTPException:
            continue

    if not symbols:
        raise HTTPException(400, "Nenhum ativo spot OKX válido para bootstrap.")

    tfs = []
    for tf in (req.timeframes or []):
        tf_norm = (tf or "").strip()
        if tf_norm in _ALLOWED_TIMEFRAMES:
            tfs.append(tf_norm)
    if not tfs:
        tfs = ["15m", "1h", "4h"]

    inserted = 0
    updated = 0
    for symbol in symbols:
        tracked = db.query(TrackedSymbolModel).filter(TrackedSymbolModel.symbol == symbol).first()
        if tracked:
            tracked.timeframes = sorted(set((tracked.timeframes or []) + tfs))
            tracked.is_active = True
            updated += 1
        else:
            db.add(TrackedSymbolModel(symbol=symbol, timeframes=sorted(set(tfs)), is_active=True))
            inserted += 1
        background_tasks.add_task(svc.sync_symbol, symbol)
    db.commit()

    return {
        "status": "ok",
        "inserted": inserted,
        "updated": updated,
        "symbols": symbols,
        "timeframes": sorted(set(tfs)),
        "sync": "started",
    }


@router.post("/force-sync-all")
async def force_sync_all(
    req: BulkSyncRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    svc: MarketDataService = Depends(get_service),
):
    tf = (req.timeframe or "").strip() if req.timeframe else None
    if tf and tf not in _ALLOWED_TIMEFRAMES:
        raise HTTPException(400, "Timeframe inválido para sync em lote.")

    tracked = db.query(TrackedSymbolModel).filter(TrackedSymbolModel.is_active == True).all()
    symbols = []
    for row in tracked:
        try:
            _assert_okx_spot_symbol(row.symbol)
        except HTTPException:
            continue
        symbols.append(row.symbol)
        background_tasks.add_task(svc.force_sync, row.symbol, tf)

    return {
        "status": "sync_started",
        "symbols_queued": len(symbols),
        "timeframe": tf,
        "symbols": symbols,
    }


@router.delete("/track/{symbol:path}/{timeframe}")
def remove_tracked_symbol_timeframe(symbol: str, timeframe: str, db: Session = Depends(get_db)):
    sym = _norm_symbol(symbol)
    _assert_okx_spot_symbol(sym)
    tf = (timeframe or "").strip()

    tracked = db.query(TrackedSymbolModel).filter(TrackedSymbolModel.symbol == sym).first()
    if not tracked:
        raise HTTPException(404, "Símbolo não rastreado")

    tfs = list(tracked.timeframes or [])
    if tf not in tfs:
        raise HTTPException(404, f"Timeframe {tf} não está rastreado para {sym}")

    tfs.remove(tf)
    if not tfs:
        db.delete(tracked)
    else:
        tracked.timeframes = tfs
    db.query(HistoricCandleModel).filter(
        HistoricCandleModel.symbol == sym,
        HistoricCandleModel.timeframe == tf,
    ).delete()
    db.commit()
    return {"status": "deleted", "symbol": sym, "timeframe": tf}


@router.delete("/track/{symbol:path}")
def remove_tracked_symbol(symbol: str, db: Session = Depends(get_db)):
    sym = _norm_symbol(symbol)
    _assert_okx_spot_symbol(sym)

    tracked = db.query(TrackedSymbolModel).filter(TrackedSymbolModel.symbol == sym).first()
    if not tracked:
        raise HTTPException(404, "Símbolo não rastreado")

    db.delete(tracked)
    db.query(HistoricCandleModel).filter(HistoricCandleModel.symbol == sym).delete()
    db.commit()
    return {"status": "deleted", "symbol": sym}


@router.post("/force-sync/{symbol:path}/{timeframe}")
async def force_sync_timeframe(
    symbol: str,
    timeframe: str,
    background_tasks: BackgroundTasks,
    svc: MarketDataService = Depends(get_service),
):
    sym = _norm_symbol(symbol)
    _assert_okx_spot_symbol(sym)
    tf = (timeframe or "").strip()
    background_tasks.add_task(svc.force_sync, sym, tf)
    return {"status": "sync_started", "symbol": sym, "timeframe": tf}


@router.post("/force-sync/{symbol:path}")
async def force_sync(
    symbol: str,
    background_tasks: BackgroundTasks,
    svc: MarketDataService = Depends(get_service),
):
    sym = _norm_symbol(symbol)
    _assert_okx_spot_symbol(sym)
    background_tasks.add_task(svc.force_sync, sym)
    return {"status": "sync_started", "symbol": sym}
