"""Teto do stop inicial, compartilhado pelo bot (bot/entry.py) e pelo backtest (backtest_engine.py).

O mesmo código nos dois lados garante que o backtest meça o que o bot executa: uma regra duplicada divergiu
antes (o bot limitava o stop e o backtest não, e os estudos 1D saíram otimistas).
O limite é só de 4 ATR (relativo à volatilidade do ativo). Um teto fixo de 5% do preço foi testado e REPROVADO: nas estratégias 1D
o stop de 3 ATR passa de 5% no BTC, o teto o apertava, o preço o batia e a estratégia reentrava (TF008 foi de 26 para 127 trades,
PF de 3,5 para 1,07). `OKX_MAX_SL_PCT` (fração do preço; 0 = sem limite percentual, padrão) permite medir alternativas.
"""
from __future__ import annotations

import os

MAX_SL_ATR = 4.0


def _max_sl_pct() -> float:
    try:
        return float(os.environ.get("OKX_MAX_SL_PCT", "0"))
    except ValueError:
        return 0.0


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
