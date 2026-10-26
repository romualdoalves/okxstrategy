"""
Rotas dos limites de risco globais da conta.
"""

from __future__ import annotations
from fastapi import APIRouter

from typing import Optional

from pydantic import BaseModel
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..risk import load_limits, guard as risk_guard, save_limits
from ..bot_manager import manager

router = APIRouter()


# ── Risco global da conta ─────────────────────────────────────────────────────
class RiskLimitsUpdate(BaseModel):
    enabled: Optional[bool] = None
    max_open_positions: Optional[int] = None
    max_exposure_usd: Optional[float] = None
    max_account_daily_loss_usd: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    capital_base_usd: Optional[float] = None
    max_auto_bots: Optional[int] = None

@router.get("/api/risk")
def get_risk(db: Session = Depends(get_db)):
    """Limites de risco globais, uso atual e se há bloqueio de novas entradas."""
    return risk_guard.snapshot(db, manager._instances)

@router.put("/api/risk")
def update_risk(body: RiskLimitsUpdate, db: Session = Depends(get_db)):
    limits = load_limits(db)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(limits, k, v)
    try:
        save_limits(db, limits)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return risk_guard.snapshot(db, manager._instances)

@router.post("/api/risk/release-drawdown")
def release_risk_drawdown(db: Session = Depends(get_db)):
    """Libera o halt de drawdown (manual) e reinicia a medição do pico a partir de agora."""
    risk_guard.release_drawdown(db)
    return risk_guard.snapshot(db, manager._instances)
