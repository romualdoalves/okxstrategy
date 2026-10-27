"""
strategies/indicators.py — indicadores compartilhados que dependem do horário dos candles.

`ta.vwap()` do pandas_ta exige um DataFrame com DatetimeIndex; as estratégias montam o DataFrame com índice
numérico, então ele imprime "VWAP requires an ordered DatetimeIndex" e devolve None (TF018 nunca operava,
PA013 usava vwap = close). Este VWAP é calculado a partir dos próprios candles, ancorado no dia UTC de cada um.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

_DAY_MS = 86_400_000


def session_vwap(candles: list, min_bars: int = 3, fallback_window: int = 20) -> pd.Series:
    """
    VWAP ancorado à meia-noite UTC do dia de cada candle (Série alinhada aos candles, índice 0..n-1).
    Nos primeiros `min_bars` candles do dia — e em timeframes >= 1D, onde cada dia tem 1 candle — ancorar
    daria 1-2 pontos; usa então o VWAP móvel dos últimos `fallback_window` candles.
    Depende só dos candles (nada de relógio do servidor): backtest == live.
    """
    n = len(candles)
    if n == 0:
        return pd.Series(dtype=float)
    high = np.array([float(c.high) for c in candles])
    low = np.array([float(c.low) for c in candles])
    close = np.array([float(c.close) for c in candles])
    vol = np.array([float(c.volume) for c in candles])
    epoch = np.array([int(c.epoch) for c in candles], dtype=np.int64)
    epoch = np.where(epoch < 10_000_000_000, epoch * 1000, epoch)       # segundos -> ms

    typical = (high + low + close) / 3.0
    df = pd.DataFrame({"day": epoch // _DAY_MS, "pv": typical * vol, "v": vol})
    grp = df.groupby("day")
    anchored = grp["pv"].cumsum() / grp["v"].cumsum().replace(0.0, np.nan)
    bars_in_day = grp.cumcount() + 1

    pv, v = pd.Series(df["pv"]), pd.Series(df["v"])
    rolling = pv.rolling(fallback_window, min_periods=1).sum() / v.rolling(fallback_window, min_periods=1).sum().replace(0.0, np.nan)

    out = anchored.where(bars_in_day >= min_bars, rolling)
    return out.fillna(pd.Series(typical)).astype(float)
