"""session_vwap: o VWAP que substitui ta.vwap (que devolvia None sem DatetimeIndex)."""
from types import SimpleNamespace as NS

import numpy as np

from backend.strategies.indicators import session_vwap
from backend.strategies.registry import REGISTRY

H = 3_600_000
DAY = 24 * H
T0 = 1_600_000_000_000 - (1_600_000_000_000 % DAY)


def _c(epoch, high, low, close, vol):
    return NS(epoch=epoch, high=high, low=low, close=close, volume=vol)


def test_anchors_at_the_candle_day_and_resets_each_day():
    day1 = [_c(T0 + i * H, 11, 9, 10, 100) for i in range(6)]            # tudo em 10
    day2 = [_c(T0 + DAY + i * H, 22, 18, 20, 100) for i in range(6)]     # tudo em 20
    v = session_vwap(day1 + day2)
    assert abs(v.iloc[5] - 10) < 1e-9
    assert abs(v.iloc[-1] - 20) < 1e-9                                   # não carrega o dia anterior


def test_is_volume_weighted_and_deterministic():
    cs = [_c(T0 + i * H, 10, 10, 10, 1) for i in range(5)] + [_c(T0 + 5 * H, 20, 20, 20, 1000)]
    v = session_vwap(cs)
    assert v.iloc[-1] > 19.5
    assert np.array_equal(v.values, session_vwap(cs).values)


def test_first_candles_of_the_day_use_rolling_fallback_not_nan():
    cs = [_c(T0 + i * H, 11, 9, 10, 100) for i in range(30)]
    v = session_vwap(cs)
    assert not v.isna().any() and abs(v.iloc[-1] - 10) < 1e-9


def test_tf018_now_produces_a_result_instead_of_none():
    import random
    rnd, price, cs = random.Random(2), 100.0, []
    for i in range(400):
        o, c = price, price * (1 + rnd.gauss(0, 0.01))
        cs.append(NS(epoch=T0 + i * H, open=o, high=max(o, c) * 1.003, low=min(o, c) * 0.997, close=c, volume=rnd.uniform(50, 500)))
        price = c
    res = REGISTRY["TF018"]().compute_with_context(cs)
    assert res is not None, "TF018 devolvia None por causa do ta.vwap"
    assert abs(res.indicators["vwap"] - cs[-1].close) / cs[-1].close < 0.2
