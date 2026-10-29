"""Teto do stop inicial, compartilhado pelo bot (bot/entry.py) e pelo backtest (backtest_engine.py).

O mesmo código nos dois lados garante que o backtest meça o que o bot executa: uma regra duplicada divergiu
antes (o bot limitava o stop e o backtest não, e os estudos 1D saíram otimistas).
`OKX_MAX_SL_PCT` (fração do preço; 0 desliga o limite percentual) permite medir alternativas sem mudar código.
"""
from __future__ import annotations

import os

MAX_SL_ATR = 4.0


def _max_sl_pct() -> float:
    try:
        return float(os.environ.get("OKX_MAX_SL_PCT", "0.05"))
    except ValueError:
        return 0.05


def max_sl_distance(entry: float, atr: float) -> float:
    """Distância máxima do stop: o mais apertado entre MAX_SL_ATR x ATR e MAX_SL_PCT do preço."""
    pct = _max_sl_pct()
    limits = [atr * MAX_SL_ATR] + ([entry * pct] if pct > 0 else [])
    return min(limits)


def cap_initial_stop(entry: float, sl: float, tp1: float, atr: float, direction: int) -> tuple[float, float, bool]:
    """Corta o stop acima do teto e reescala o TP1 para manter a relação risco-retorno. -> (sl, tp1, cortou)."""
    max_dist = max_sl_distance(entry, atr)
    dist = abs(entry - sl)
    if not (dist > max_dist > 0):
        return sl, tp1, False
    rr = abs(tp1 - entry) / dist
    sign = 1 if direction == 1 else -1
    return entry - sign * max_dist, entry + sign * max_dist * rr, True
