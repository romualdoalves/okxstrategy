from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import HistoricCandleModel, MarketDataSyncJobModel, SessionLocal, TrackedSymbolModel
from ..exchanges.factory import get_ranked_assets_universe
from ..market_data_service import MarketDataService

router = APIRouter(prefix="/market-data", tags=["Market Data"])

_ALLOWED_TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1D", "1W"}
_DEFAULT_OKX_SPOT_SYMBOLS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT",
    "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "DOT-USDT", "LINK-USDT",
    "LTC-USDT", "UNI-USDT", "TRX-USDT", "BCH-USDT", "APT-USDT",
]


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
    timeframes: list[str] = ["1m", "5m", "15m", "1h", "1d"]


class BulkSyncRequest(BaseModel):
    timeframe: str | None = None


def _normalize_timeframe(tf: str) -> str:
    raw = (tf or "").strip()
    if not raw:
        return ""
    aliases = {
        "1d": "1D",
        "1w": "1W",
    }
    return aliases.get(raw.lower(), raw)


def _create_sync_job(db: Session, symbol: str, timeframe: str | None, trigger: str, batch_id: str | None = None):
    job = MarketDataSyncJobModel(
        batch_id=batch_id,
        symbol=symbol,
        timeframe=timeframe,
        trigger=trigger,
        status="queued",
        created_at=datetime.utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _job_payload(job: MarketDataSyncJobModel):
    return {
        "id": job.id,
        "batch_id": job.batch_id,
        "symbol": job.symbol,
        "timeframe": job.timeframe,
        "trigger": job.trigger,
        "status": job.status,
        "candles_synced": job.candles_synced,
        "error": job.error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


async def _run_sync_symbol_job(job_id: int, symbol: str, svc: MarketDataService):
    db = SessionLocal()
    try:
        job = db.query(MarketDataSyncJobModel).filter(MarketDataSyncJobModel.id == job_id).first()
        if not job:
            return
        job.status = "running"
        job.started_at = datetime.utcnow()
        job.error = None
        db.commit()

        await svc.sync_symbol(symbol)

        count = db.query(func.count(HistoricCandleModel.id)).filter(
            HistoricCandleModel.symbol == symbol,
        ).scalar() or 0
        job.status = "success"
        job.candles_synced = int(count)
        job.finished_at = datetime.utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.query(MarketDataSyncJobModel).filter(MarketDataSyncJobModel.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


async def _run_force_sync_job(job_id: int, symbol: str, timeframe: str | None, svc: MarketDataService):
    db = SessionLocal()
    try:
        job = db.query(MarketDataSyncJobModel).filter(MarketDataSyncJobModel.id == job_id).first()
        if not job:
            return
        job.status = "running"
        job.started_at = datetime.utcnow()
        job.error = None
        db.commit()

        await svc.force_sync(symbol, timeframe)

        q = db.query(func.count(HistoricCandleModel.id)).filter(HistoricCandleModel.symbol == symbol)
        if timeframe:
            q = q.filter(HistoricCandleModel.timeframe == timeframe)
        count = q.scalar() or 0

        job.status = "success"
        job.candles_synced = int(count)
        job.finished_at = datetime.utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.query(MarketDataSyncJobModel).filter(MarketDataSyncJobModel.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


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


@router.get("/available-symbols")
def get_available_symbols(db: Session = Depends(get_db)):
    raw = list(get_ranked_assets_universe() or [])
    # Inclui também ativos já rastreados para não perder opções em cenários parciais.
    tracked = db.query(TrackedSymbolModel.symbol).all()
    raw.extend([row[0] for row in tracked if row and row[0]])
    # Fallback seguro para UI quando universo vier vazio/curto no ambiente.
    if len(raw) < 3:
        raw.extend(_DEFAULT_OKX_SPOT_SYMBOLS)
    out = []
    seen = set()
    for symbol in raw:
        sym = _norm_symbol(symbol)
        if not sym or sym in seen:
            continue
        try:
            _assert_okx_spot_symbol(sym)
        except HTTPException:
            continue
        out.append(sym)
        seen.add(sym)
    return out


@router.get("/sync-jobs")
def get_sync_jobs(limit: int = 60, batch_id: str | None = None, db: Session = Depends(get_db)):
    lim = max(1, min(int(limit or 60), 500))
    q = db.query(MarketDataSyncJobModel)
    if batch_id:
        q = q.filter(MarketDataSyncJobModel.batch_id == batch_id)
    rows = q.order_by(MarketDataSyncJobModel.created_at.desc()).limit(lim).all()
    return [_job_payload(row) for row in rows]


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
        tf_norm = _normalize_timeframe(tf)
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

    job = _create_sync_job(db, symbol=symbol, timeframe=None, trigger="track")
    background_tasks.add_task(_run_sync_symbol_job, job.id, symbol, svc)
    return {
        "status": "ok",
        "symbol": symbol,
        "timeframes": tracked.timeframes,
        "sync": "queued",
        "job_id": job.id,
    }


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
        tf_norm = _normalize_timeframe(tf)
        if tf_norm in _ALLOWED_TIMEFRAMES:
            tfs.append(tf_norm)
    if not tfs:
        tfs = ["1m", "5m", "15m", "1h", "1D"]

    inserted = 0
    updated = 0
    batch_id = str(uuid4())
    jobs = []
    for symbol in symbols:
        tracked = db.query(TrackedSymbolModel).filter(TrackedSymbolModel.symbol == symbol).first()
        if tracked:
            tracked.timeframes = sorted(set((tracked.timeframes or []) + tfs))
            tracked.is_active = True
            updated += 1
        else:
            db.add(TrackedSymbolModel(symbol=symbol, timeframes=sorted(set(tfs)), is_active=True))
            inserted += 1
        job = _create_sync_job(db, symbol=symbol, timeframe=None, trigger="bootstrap", batch_id=batch_id)
        jobs.append(job.id)
        background_tasks.add_task(_run_sync_symbol_job, job.id, symbol, svc)
    db.commit()

    return {
        "status": "ok",
        "batch_id": batch_id,
        "inserted": inserted,
        "updated": updated,
        "symbols": symbols,
        "timeframes": sorted(set(tfs)),
        "sync": "queued",
        "jobs_queued": len(jobs),
    }


@router.post("/force-sync-all")
async def force_sync_all(
    req: BulkSyncRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    svc: MarketDataService = Depends(get_service),
):
    tf = _normalize_timeframe(req.timeframe) if req.timeframe else None
    if tf and tf not in _ALLOWED_TIMEFRAMES:
        raise HTTPException(400, "Timeframe inválido para sync em lote.")

    tracked = db.query(TrackedSymbolModel).filter(TrackedSymbolModel.is_active == True).all()
    batch_id = str(uuid4())
    jobs = []
    symbols = []
    for row in tracked:
        try:
            _assert_okx_spot_symbol(row.symbol)
        except HTTPException:
            continue
        symbols.append(row.symbol)
        job = _create_sync_job(db, symbol=row.symbol, timeframe=tf, trigger="bulk", batch_id=batch_id)
        jobs.append(job.id)
        background_tasks.add_task(_run_force_sync_job, job.id, row.symbol, tf, svc)

    return {
        "status": "sync_queued",
        "batch_id": batch_id,
        "jobs_queued": len(jobs),
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
    db.commit()
    return {"status": "deleted", "symbol": sym, "timeframe": tf, "history_preserved": True}


@router.delete("/track/{symbol:path}")
def remove_tracked_symbol(symbol: str, db: Session = Depends(get_db)):
    sym = _norm_symbol(symbol)
    _assert_okx_spot_symbol(sym)

    tracked = db.query(TrackedSymbolModel).filter(TrackedSymbolModel.symbol == sym).first()
    if not tracked:
        raise HTTPException(404, "Símbolo não rastreado")

    db.delete(tracked)
    db.commit()
    return {"status": "deleted", "symbol": sym, "history_preserved": True}


@router.post("/force-sync/{symbol:path}/{timeframe}")
async def force_sync_timeframe(
    symbol: str,
    timeframe: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    svc: MarketDataService = Depends(get_service),
):
    sym = _norm_symbol(symbol)
    _assert_okx_spot_symbol(sym)
    tf = _normalize_timeframe(timeframe)
    if tf not in _ALLOWED_TIMEFRAMES:
        raise HTTPException(400, "Timeframe inválido.")
    job = _create_sync_job(db, symbol=sym, timeframe=tf, trigger="manual")
    background_tasks.add_task(_run_force_sync_job, job.id, sym, tf, svc)
    return {"status": "sync_queued", "symbol": sym, "timeframe": tf, "job_id": job.id}


@router.post("/force-sync/{symbol:path}")
async def force_sync(
    symbol: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    svc: MarketDataService = Depends(get_service),
):
    sym = _norm_symbol(symbol)
    _assert_okx_spot_symbol(sym)
    job = _create_sync_job(db, symbol=sym, timeframe=None, trigger="manual")
    background_tasks.add_task(_run_force_sync_job, job.id, sym, None, svc)
    return {"status": "sync_queued", "symbol": sym, "job_id": job.id}
