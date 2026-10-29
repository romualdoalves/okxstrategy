"""RG004: `_label_states` vetorizado tem de dar exatamente os mesmos estados do laço original barra a barra."""
import random

import numpy as np
import pandas as pd
import pandas_ta as ta
import pytest

from backend.strategies.factory import rg004 as M
from backend.strategies.factory.rg004 import (
    GrafosDeMarkovStrategy, STATE_CHEAP, STATE_EQUILIBRIUM, STATE_EXPANSION_DOWN, STATE_EXPANSION_UP,
    STATE_EXPENSIVE, STATE_LIQUIDITY_BUY, STATE_LIQUIDITY_SELL, STATE_NEUTRAL,
)


def _reference(self, df, atr_s):
    """Cópia do laço original (antes da vetorização)."""
    states = np.full(len(df), STATE_NEUTRAL, dtype=int)
    for i in range(len(df)):
        atr = self._safe_float(atr_s.iloc[i], 0.0)
        if atr <= 0:
            continue
        window = df.iloc[:i + 1]
        row = df.iloc[i]
        location_score, day_open, macro_mid = self._location_score(window, atr)
        candle_range = max(float(row["high"] - row["low"]), 0.0)
        if candle_range <= 0:
            continue
        body_high = max(float(row["open"]), float(row["close"]))
        body_low = min(float(row["open"]), float(row["close"]))
        lower_wick_ratio = (body_low - float(row["low"])) / candle_range
        upper_wick_ratio = (float(row["high"]) - body_high) / candle_range
        vol_signature = candle_range / atr
        prev = window.iloc[max(0, i - int(self.liquidity_lookback)):i]
        prev_low = float(prev["low"].min()) if not prev.empty else float(row["low"])
        prev_high = float(prev["high"].max()) if not prev.empty else float(row["high"])
        if lower_wick_ratio >= float(self.wick_ratio) and float(row["low"]) <= prev_low:
            states[i] = STATE_LIQUIDITY_BUY
        elif upper_wick_ratio >= float(self.wick_ratio) and float(row["high"]) >= prev_high:
            states[i] = STATE_LIQUIDITY_SELL
        elif vol_signature >= float(self.expansion_threshold):
            states[i] = STATE_EXPANSION_UP if float(row["close"]) >= float(row["open"]) else STATE_EXPANSION_DOWN
        elif vol_signature <= float(self.equilibrium_threshold):
            states[i] = STATE_EQUILIBRIUM
        elif location_score <= -float(self.location_z) or (float(row["close"]) < macro_mid and float(row["close"]) < day_open):
            states[i] = STATE_CHEAP
        elif location_score >= float(self.location_z) or (float(row["close"]) > macro_mid and float(row["close"]) > day_open):
            states[i] = STATE_EXPENSIVE
        else:
            states[i] = STATE_NEUTRAL
    return states


def _frame(seed, n, flat_every=0):
    rnd = random.Random(seed)
    p, rows = 100.0, []
    for i in range(n):
        o = p
        c = p * (1 + rnd.gauss(0, 0.004))
        h = max(o, c) * (1 + abs(rnd.gauss(0, 0.003)))
        l = min(o, c) * (1 - abs(rnd.gauss(0, 0.003)))
        if flat_every and i % flat_every == 0:
            h = l = o = c                      # vela sem range (ramo candle_range <= 0)
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1.0})
        p = c
    return pd.DataFrame(rows)


@pytest.mark.parametrize("seed,n,params,flat", [
    (1, 300, {}, 0),
    (2, 300, {"bars_per_day": 24, "macro_days": 2, "liquidity_lookback": 8}, 0),
    (3, 250, {"bars_per_day": 96, "macro_days": 3, "wick_ratio": 0.3, "liquidity_lookback": 40}, 7),
    (4, 120, {"bars_per_day": 30, "location_z": 0.1, "expansion_threshold": 1.0, "equilibrium_threshold": 0.2}, 0),
    (5, 300, {"liquidity_lookback": 96, "macro_days": 10}, 11),
])
def test_vectorized_states_equal_reference(seed, n, params, flat):
    s = GrafosDeMarkovStrategy()
    s.set_params(params)
    df = _frame(seed, n, flat)
    atr = ta.atr(df["high"], df["low"], df["close"], length=int(s.atr_period)).bfill().ffill()
    new = s._label_states(df, atr)
    old = _reference(s, df, atr)
    assert (new == old).all(), np.flatnonzero(new != old)[:10]
    assert len(set(new.tolist())) > 2          # o teste exercita vários estados, não só o neutro


def test_zero_or_nan_atr_bars_stay_neutral():
    s = GrafosDeMarkovStrategy()
    df = _frame(7, 200)
    atr = pd.Series(np.where(np.arange(200) % 5 == 0, np.nan, 1.0))
    atr.iloc[3] = 0.0
    assert (s._label_states(df, atr) == _reference(s, df, atr)).all()
