"""
risk.py — Limites de risco globais da conta (somando todos os bots).

Os bots já têm um stop diário individual (stop_loss_usd). Isto cobre o que ele não vê:
muitas posições abertas ao mesmo tempo, exposição total, perda diária da conta inteira e
drawdown acumulado. Ao estourar um limite só BLOQUEIA NOVAS ENTRADAS — posições abertas
mantêm o SL/TP na exchange e as saídas nunca são bloqueadas.

Estado: nada vive só em memória. P&L do dia e drawdown saem da tabela `trades`; limites e o
halt de drawdown ficam na tabela `settings`. Sobrevive a restart.

Também limita a CORRELAÇÃO (várias posições em ativos que andam juntos contam como uma aposta só) e,
opcionalmente, dimensiona a ordem pelo RISCO até o Stop Loss (sizing_mode="risk") em vez do stake fixo.

Medições (aproximações assumidas):
  - P&L = pnl - fee das saídas (fee ausente conta 0). Não inclui P&L não realizado.
  - Exposição = notional real (tamanho x preço de entrada) de cada posição aberta; entrada em envio conta o
    stake planejado.
  - Correlação = correlação dos retornos logarítmicos de fechamentos de 1h (últimos 300 candles do cache de
    mercado). Sem dados suficientes, assume-se correlação alta (ASSUMED_CORRELATION): na dúvida, conta como grupo.
  - Drawdown = queda do P&L realizado acumulado desde o pico, em % de capital_base_usd.
    Estoura => halt PERSISTENTE até liberação manual (release_drawdown).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import numpy as np
from sqlalchemy import func
from sqlalchemy.orm import Session

from .database import HistoricCandleModel, SessionLocal, SettingsModel, TradeModel

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
    max_correlated_positions: int = 3           # posições simultâneas em ativos correlacionados (inclui a nova)
    correlation_threshold: float = 0.80         # a partir daqui dois ativos contam como "o mesmo risco"
    # Dimensionamento da ordem: "fixed" = stake do bot (US$100, política padrão do projeto);
    # "risk" = arrisca risk_per_trade_pct de capital_base_usd até o Stop Loss, limitado a [min, max] stake.
    sizing_mode: str = "fixed"
    risk_per_trade_pct: float = 0.5
    min_stake_usd: float = 25.0
    max_stake_usd: float = 250.0

    def validate(self) -> "RiskLimits":
        if self.max_open_positions < 1:
            raise ValueError("max_open_positions deve ser >= 1")
        for name in ("max_exposure_usd", "max_account_daily_loss_usd", "capital_base_usd"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} deve ser > 0")
        if not 0 < self.max_drawdown_pct <= 100:
            raise ValueError("max_drawdown_pct deve estar entre 0 e 100")
        if self.max_correlated_positions < 1:
            raise ValueError("max_correlated_positions deve ser >= 1")
        if not 0.3 <= self.correlation_threshold < 1.0:
            raise ValueError("correlation_threshold deve estar entre 0,3 e 1")
        if self.sizing_mode not in ("fixed", "risk"):
            raise ValueError("sizing_mode deve ser 'fixed' ou 'risk'")
        if not 0 < self.risk_per_trade_pct <= 5:
            raise ValueError("risk_per_trade_pct deve estar entre 0 e 5")
        if not 0 < self.min_stake_usd <= self.max_stake_usd:
            raise ValueError("min_stake_usd deve ser > 0 e <= max_stake_usd")
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


def _position_exposure(inst: Any) -> float:
    """Posição aberta: notional real. Entrada em envio: stake planejado (ou o do bot)."""
    size = abs(float(getattr(inst, "_sz", 0) or 0))
    entry = float(getattr(inst, "_entry_price", 0) or 0)
    if getattr(inst, "_direction", 0) != 0 and size > 0 and entry > 0:
        return size * entry
    planned = getattr(inst, "_planned_stake_usd", None)
    return float(planned or inst.config.stake_usd or 0) * max(1, int(inst.config.leverage or 1))


def _exposure_usd(insts: Iterable[Any]) -> float:
    return sum(_position_exposure(i) for i in insts)


# ── Correlação entre ativos ─────────────────────────────────────────────────

CORRELATION_TIMEFRAME = "1h"
CORRELATION_SAMPLES = 300
CORRELATION_MIN_POINTS = 100
CORRELATION_TTL_SECONDS = 900
ASSUMED_CORRELATION = 0.85       # sem dados: cripto spot costuma andar junto


def _closes(db: Session, symbol: str) -> dict:
    rows = (
        db.query(HistoricCandleModel.epoch, HistoricCandleModel.close)
        .filter(HistoricCandleModel.symbol == symbol, HistoricCandleModel.timeframe == CORRELATION_TIMEFRAME)
        .order_by(HistoricCandleModel.epoch.desc())
        .limit(CORRELATION_SAMPLES)
        .all()
    )
    return {epoch: float(close) for epoch, close in rows if close and close > 0}


def pair_correlation(db: Session, a: str, b: str) -> Optional[float]:
    """Correlação dos retornos logarítmicos 1h entre dois ativos; None se faltar histórico em comum."""
    ca, cb = _closes(db, a), _closes(db, b)
    common = sorted(set(ca) & set(cb))
    if len(common) < CORRELATION_MIN_POINTS:
        return None
    pa = np.log(np.array([ca[t] for t in common]))
    pb = np.log(np.array([cb[t] for t in common]))
    ra, rb = np.diff(pa), np.diff(pb)
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def compute_stake(limits: "RiskLimits", base_stake: float, price: float, sl_price: float,
                  balance: Optional[float] = None) -> dict:
    """
    Tamanho da ordem. Modo "fixed": o stake do bot. Modo "risk": stake = risco_em_USD / distância_até_o_SL,
    limitado a [min_stake_usd, max_stake_usd] e a 95% do saldo disponível. Retorna
    {"stake", "mode", "risk_usd", "sl_pct", "limited_by", "blocked"}; `blocked` (texto) = não operar.
    """
    info = {"stake": float(base_stake), "mode": limits.sizing_mode, "risk_usd": None, "sl_pct": None,
            "limited_by": None, "blocked": None}
    if limits.sizing_mode != "risk":
        return info
    if price <= 0 or sl_price <= 0 or sl_price == price:
        info["mode"] = "fixed"                      # sem SL válido não há como dimensionar pelo risco
        return info

    sl_pct = abs(price - sl_price) / price
    risk_usd = limits.capital_base_usd * limits.risk_per_trade_pct / 100.0
    stake = risk_usd / sl_pct
    info.update(risk_usd=round(risk_usd, 2), sl_pct=round(sl_pct, 5))

    if stake > limits.max_stake_usd:
        stake, info["limited_by"] = limits.max_stake_usd, "max_stake"
    if balance is not None and stake > balance * 0.95:
        stake, info["limited_by"] = balance * 0.95, "balance"
    if stake < limits.min_stake_usd:
        if info["limited_by"] == "balance":
            info["blocked"] = (f"Saldo insuficiente para o tamanho mínimo (${limits.min_stake_usd:g}); "
                               f"disponível ${balance:.2f}.")
        else:
            stake, info["limited_by"] = limits.min_stake_usd, "min_stake"
    info["stake"] = round(stake, 2)
    return info


# ── Guard ────────────────────────────────────────────────────────────────────

class RiskGuard:
    def __init__(self) -> None:
        self._notified: set[tuple[str, str]] = set()   # (kind, dia) já avisados
        self._corr_cache: dict[tuple[str, str], tuple[float, Optional[float]]] = {}

    def _correlation(self, db: Session, a: str, b: str) -> tuple[float, bool]:
        """(correlação, assumida). Cache de 15 min por par para não consultar o banco a cada checagem."""
        key = tuple(sorted((a, b)))
        hit = self._corr_cache.get(key)
        if hit is None or time.time() - hit[0] > CORRELATION_TTL_SECONDS:
            hit = (time.time(), pair_correlation(db, a, b))
            self._corr_cache[key] = hit
        return (ASSUMED_CORRELATION, True) if hit[1] is None else (hit[1], False)

    def _correlated_peers(self, db: Session, symbol: str, others: list[str], threshold: float) -> list[tuple[str, float, bool]]:
        peers = []
        for other in others:
            if other == symbol:
                continue
            corr, assumed = self._correlation(db, symbol, other)
            if corr >= threshold:
                peers.append((other, corr, assumed))
        return peers

    def sizing_limits(self) -> "RiskLimits":
        """Limites de dimensionamento; se o banco falhar, cai para o modo fixo (comportamento padrão)."""
        try:
            with SessionLocal() as db:
                return load_limits(db)
        except Exception as exc:
            log.error("RISCO GLOBAL: falha ao ler limites de dimensionamento (%s) — usando stake fixo.", exc)
            return RiskLimits(sizing_mode="fixed")

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
        group = self._largest_correlated_group(db, [i.config.symbol for i in active], limits.correlation_threshold)
        usage = {
            "open_positions": len(active),
            "exposure_usd": round(_exposure_usd(active), 2),
            "daily_pnl_usd": round(pnl_today, 2),
            "drawdown_pct": round(dd_pct, 2),
            "correlated_group": group,
        }
        breaches = []
        if len(active) >= limits.max_open_positions:
            breaches.append("positions")
        if _exposure_usd(active) >= limits.max_exposure_usd:
            breaches.append("exposure")
        if -pnl_today >= limits.max_account_daily_loss_usd:
            breaches.append("daily_loss")
        if group >= limits.max_correlated_positions:
            breaches.append("correlation")
        if state.get("dd_halt"):
            breaches.append("drawdown")
        return {
            "limits": asdict(limits),
            "usage": usage,
            "drawdown_halt": state.get("dd_halt"),
            "breaches": breaches if limits.enabled else [],
            "blocking": bool(breaches) and limits.enabled,
        }

    def _largest_correlated_group(self, db: Session, symbols: list[str], threshold: float) -> int:
        """Tamanho do maior grupo de posições abertas ligadas por correlação >= limiar (união-busca)."""
        syms = sorted(set(symbols))
        if len(syms) < 2:
            return len(syms)
        parent = {s: s for s in syms}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, a in enumerate(syms):
            for b in syms[i + 1:]:
                if self._correlation(db, a, b)[0] >= threshold:
                    parent[find(a)] = find(b)
        sizes: dict[str, int] = {}
        for s in syms:
            sizes[find(s)] = sizes.get(find(s), 0) + 1
        return max(sizes.values())

    def check_entry(self, instances: dict[int, Any], bot_id: int, stake_usd: float, leverage: int = 1,
                    symbol: Optional[str] = None) -> RiskDecision:
        """
        Decide se o bot `bot_id` pode abrir nova posição. SÍNCRONO de propósito: o bot marca
        `_entry_inflight` antes de chamar e não há `await` entre o flag e esta checagem, então
        dois bots não passam juntos pelo mesmo limite. Falha => bloqueia (fail-closed).
        """
        try:
            with SessionLocal() as db:
                return self._check_entry(db, instances, bot_id, stake_usd, leverage, symbol)
        except Exception as exc:
            log.error("RISCO GLOBAL: falha ao avaliar limites (%s) — entrada bloqueada.", exc)
            return RiskDecision(False, "Risco global indisponível (erro ao consultar limites).", "error",
                                self._first_time("error"))

    def _check_entry(self, db: Session, instances: dict[int, Any], bot_id: int,
                     stake_usd: float, leverage: int, symbol: Optional[str] = None) -> RiskDecision:
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

        if symbol and others:
            peers = self._correlated_peers(db, symbol, [o.config.symbol for o in others], limits.correlation_threshold)
            if len(peers) + 1 > limits.max_correlated_positions:
                names = ", ".join(
                    f"{s} ({'sem histórico, assumido' if assumed else f'correlação {c:.2f}'})" for s, c, assumed in peers)
                return RiskDecision(
                    False,
                    f"Risco global: {len(peers)} posição(ões) aberta(s) em ativos correlacionados com {symbol}: {names} "
                    f"(limite {limits.max_correlated_positions} por grupo, correlação >= {limits.correlation_threshold:g}).",
                    "correlation", self._first_time("correlation"))

        return RiskDecision(True)


guard = RiskGuard()
