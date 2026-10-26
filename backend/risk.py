"""
risk.py — Limites de risco globais da conta (somando todos os bots).

Os bots já têm um stop diário individual (stop_loss_usd). Isto cobre o que ele não vê:
muitas posições abertas ao mesmo tempo, exposição total, perda diária da conta inteira e
drawdown acumulado. Ao estourar um limite só BLOQUEIA NOVAS ENTRADAS — posições abertas
mantêm o SL/TP na exchange e as saídas nunca são bloqueadas.

Estado: nada vive só em memória. P&L do dia e drawdown saem da tabela `trades`; limites e o
halt de drawdown ficam na tabela `settings`. Sobrevive a restart.

Medições (aproximações assumidas):
  - P&L = pnl - fee das saídas (fee ausente conta 0). Não inclui P&L não realizado.
  - Exposição = stake_usd * leverage de cada posição aberta/entrada em envio.
  - Drawdown = queda do P&L realizado acumulado desde o pico, em % de capital_base_usd.
    Estoura => halt PERSISTENTE até liberação manual (release_drawdown).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .database import SessionLocal, SettingsModel, TradeModel

log = logging.getLogger("risk")

LIMITS_KEY = "risk_limits"
STATE_KEY = "risk_state"

# Fuso local em horas (ex.: -3 para BRT), o mesmo TZ_OFFSET usado pela API. Define a virada do "dia".
_TZ_OFFSET_HOURS = int(os.getenv("TZ_OFFSET", "0"))


def current_day_start_utc() -> datetime:
    """Início do dia local atual, como datetime UTC naive (formato gravado no banco)."""
    offset = timedelta(hours=_TZ_OFFSET_HOURS)
    local_now = datetime.now(timezone.utc).replace(tzinfo=None) + offset
    return local_now.replace(hour=0, minute=0, second=0, microsecond=0) - offset


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class RiskLimits:
    enabled: bool = True
    max_open_positions: int = 5
    max_exposure_usd: float = 500.0
    max_account_daily_loss_usd: float = 100.0   # valor positivo = perda máxima do dia
    max_drawdown_pct: float = 10.0
    capital_base_usd: float = 2000.0            # base do % de drawdown (10% = $200, o dobro da perda diária)
    max_auto_bots: int = 3                      # bots ativos criados pelo auto-scan

    def validate(self) -> "RiskLimits":
        if self.max_open_positions < 1 or self.max_auto_bots < 0:
            raise ValueError("max_open_positions deve ser >= 1 e max_auto_bots >= 0")
        for name in ("max_exposure_usd", "max_account_daily_loss_usd", "capital_base_usd"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} deve ser > 0")
        if not 0 < self.max_drawdown_pct <= 100:
            raise ValueError("max_drawdown_pct deve estar entre 0 e 100")
        return self


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""
    kind: str = ""            # "positions" | "exposure" | "daily_loss" | "drawdown" | "error"
    first_time: bool = False  # primeira vez que este tipo de bloqueio aparece no dia (p/ notificar)


# ── Persistência (tabela settings) ───────────────────────────────────────────

def _get_json(db: Session, key: str) -> dict:
    row = db.get(SettingsModel, key)
    if not row or not row.value:
        return {}
    try:
        data = json.loads(row.value)
        return data if isinstance(data, dict) else {}
    except ValueError:
        log.error("Valor inválido em settings[%s]; usando padrão.", key)
        return {}


def _set_json(db: Session, key: str, data: dict) -> None:
    row = db.get(SettingsModel, key)
    value = json.dumps(data)
    if row:
        row.value = value
    else:
        db.add(SettingsModel(key=key, value=value))
    db.commit()


def load_limits(db: Session) -> RiskLimits:
    raw = _get_json(db, LIMITS_KEY)
    known = {f.name for f in fields(RiskLimits)}
    return RiskLimits(**{k: v for k, v in raw.items() if k in known})


def save_limits(db: Session, limits: RiskLimits) -> RiskLimits:
    limits.validate()
    _set_json(db, LIMITS_KEY, asdict(limits))
    return limits


# ── Medições ─────────────────────────────────────────────────────────────────

def _net_pnl_expr():
    return TradeModel.pnl - func.coalesce(TradeModel.fee, 0.0)


def net_pnl_today(db: Session) -> float:
    total = (
        db.query(func.coalesce(func.sum(_net_pnl_expr()), 0.0))
        .filter(
            TradeModel.type == "exit",
            TradeModel.pnl.isnot(None),
            TradeModel.timestamp >= current_day_start_utc(),
        )
        .scalar()
    )
    return float(total or 0.0)


def drawdown_usd(db: Session, since: Optional[datetime]) -> tuple[float, float]:
    """(drawdown atual em USD, pico do P&L acumulado) considerando saídas após `since`."""
    q = db.query(TradeModel.timestamp, _net_pnl_expr()).filter(
        TradeModel.type == "exit", TradeModel.pnl.isnot(None)
    )
    if since is not None:
        q = q.filter(TradeModel.timestamp > since)
    cumulative = peak = 0.0
    for _, pnl in q.order_by(TradeModel.timestamp, TradeModel.id):
        cumulative += float(pnl or 0.0)
        peak = max(peak, cumulative)
    return peak - cumulative, peak


def _active_positions(instances: dict[int, Any], exclude_id: Optional[int]) -> list[Any]:
    """Bots com posição aberta ou entrada em envio (exceto `exclude_id`)."""
    return [
        inst for bid, inst in instances.items()
        if bid != exclude_id and (getattr(inst, "_direction", 0) != 0 or getattr(inst, "_entry_inflight", False))
    ]


def _exposure_usd(insts: Iterable[Any]) -> float:
    return sum(float(i.config.stake_usd or 0) * max(1, int(i.config.leverage or 1)) for i in insts)


# ── Guard ────────────────────────────────────────────────────────────────────

class RiskGuard:
    def __init__(self) -> None:
        self._notified: set[tuple[str, str]] = set()   # (kind, dia) já avisados

    def _first_time(self, kind: str) -> bool:
        key = (kind, current_day_start_utc().date().isoformat())
        if key in self._notified:
            return False
        self._notified.add(key)
        return True

    # -- halt de drawdown ---------------------------------------------------

    def _evaluate_drawdown(self, db: Session, limits: RiskLimits) -> tuple[float, dict]:
        """Retorna (dd_pct atual, state). Grava o halt persistente se estourou."""
        state = _get_json(db, STATE_KEY)
        since = datetime.fromisoformat(state["dd_baseline"]) if state.get("dd_baseline") else None
        dd, _ = drawdown_usd(db, since)
        dd_pct = dd / limits.capital_base_usd * 100.0
        if dd_pct >= limits.max_drawdown_pct and not state.get("dd_halt"):
            state["dd_halt"] = {"at": _utcnow().isoformat(), "dd_pct": round(dd_pct, 2), "dd_usd": round(dd, 2)}
            _set_json(db, STATE_KEY, state)
            log.critical("RISCO GLOBAL: drawdown %.2f%% >= %.2f%% — novas entradas bloqueadas até liberação manual.",
                         dd_pct, limits.max_drawdown_pct)
        return dd_pct, state

    def release_drawdown(self, db: Session) -> None:
        """Libera o halt e passa a medir o drawdown a partir de agora (pico zerado)."""
        _set_json(db, STATE_KEY, {"dd_baseline": _utcnow().isoformat(), "dd_halt": None})
        self._notified = {k for k in self._notified if k[0] != "drawdown"}
        log.warning("RISCO GLOBAL: halt de drawdown liberado manualmente.")

    # -- consulta -----------------------------------------------------------

    def snapshot(self, db: Session, instances: dict[int, Any]) -> dict:
        limits = load_limits(db)
        dd_pct, state = self._evaluate_drawdown(db, limits) if limits.enabled else (0.0, _get_json(db, STATE_KEY))
        active = _active_positions(instances, None)
        pnl_today = net_pnl_today(db)
        usage = {
            "open_positions": len(active),
            "exposure_usd": round(_exposure_usd(active), 2),
            "daily_pnl_usd": round(pnl_today, 2),
            "drawdown_pct": round(dd_pct, 2),
        }
        breaches = []
        if len(active) >= limits.max_open_positions:
            breaches.append("positions")
        if _exposure_usd(active) >= limits.max_exposure_usd:
            breaches.append("exposure")
        if -pnl_today >= limits.max_account_daily_loss_usd:
            breaches.append("daily_loss")
        if state.get("dd_halt"):
            breaches.append("drawdown")
        return {
            "limits": asdict(limits),
            "usage": usage,
            "drawdown_halt": state.get("dd_halt"),
            "breaches": breaches if limits.enabled else [],
            "blocking": bool(breaches) and limits.enabled,
        }

    def check_entry(self, instances: dict[int, Any], bot_id: int, stake_usd: float, leverage: int = 1) -> RiskDecision:
        """
        Decide se o bot `bot_id` pode abrir nova posição. SÍNCRONO de propósito: o bot marca
        `_entry_inflight` antes de chamar e não há `await` entre o flag e esta checagem, então
        dois bots não passam juntos pelo mesmo limite. Falha => bloqueia (fail-closed).
        """
        try:
            with SessionLocal() as db:
                return self._check_entry(db, instances, bot_id, stake_usd, leverage)
        except Exception as exc:
            log.error("RISCO GLOBAL: falha ao avaliar limites (%s) — entrada bloqueada.", exc)
            return RiskDecision(False, "Risco global indisponível (erro ao consultar limites).", "error",
                                self._first_time("error"))

    def _check_entry(self, db: Session, instances: dict[int, Any], bot_id: int,
                     stake_usd: float, leverage: int) -> RiskDecision:
        limits = load_limits(db)
        if not limits.enabled:
            return RiskDecision(True)

        dd_pct, state = self._evaluate_drawdown(db, limits)
        if state.get("dd_halt"):
            return RiskDecision(
                False,
                f"Risco global: drawdown {dd_pct:.1f}% (limite {limits.max_drawdown_pct:g}%) — liberação manual necessária.",
                "drawdown", self._first_time("drawdown"))

        pnl_today = net_pnl_today(db)
        if -pnl_today >= limits.max_account_daily_loss_usd:
            return RiskDecision(
                False,
                f"Risco global: perda diária da conta ${-pnl_today:.2f} (limite ${limits.max_account_daily_loss_usd:g}).",
                "daily_loss", self._first_time("daily_loss"))

        others = _active_positions(instances, bot_id)
        if len(others) + 1 > limits.max_open_positions:
            return RiskDecision(
                False,
                f"Risco global: {len(others)} posições abertas (limite {limits.max_open_positions}).",
                "positions", self._first_time("positions"))

        exposure = _exposure_usd(others) + float(stake_usd) * max(1, int(leverage))
        if exposure > limits.max_exposure_usd:
            return RiskDecision(
                False,
                f"Risco global: exposição ${exposure:.0f} excede o limite ${limits.max_exposure_usd:g}.",
                "exposure", self._first_time("exposure"))

        return RiskDecision(True)

    def auto_scan_allowed(self, db: Session, instances: dict[int, Any], active_auto_bots: int) -> tuple[bool, str]:
        """O auto-scan não deve criar bots se a conta já está no limite ou no teto de bots automáticos."""
        limits = load_limits(db)
        if not limits.enabled:
            return True, ""
        if active_auto_bots >= limits.max_auto_bots:
            return False, f"Teto de bots automáticos atingido ({active_auto_bots}/{limits.max_auto_bots})."
        snap = self.snapshot(db, instances)
        if snap["blocking"]:
            return False, f"Limite de risco global ativo: {', '.join(snap['breaches'])}."
        return True, ""


guard = RiskGuard()
