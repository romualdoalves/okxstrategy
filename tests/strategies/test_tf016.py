"""TF016: BUY de pullback com RR real, volume exigindo acima da média e SELL = perda do viés."""
import random
from types import SimpleNamespace as NS

import pandas as pd
import pandas_ta as ta

from backend.strategies.base import Signal
from backend.strategies.registry import REGISTRY

STEP = 3_600_000


def _bar(i, o, h, l, c, v):
    return NS(epoch=1_600_000_000_000 + i * STEP, open=o, high=h, low=l, close=c, volume=v)


def _trend(n, slope):
    rnd, price, out = random.Random(5), 100.0, []
    for i in range(n):
        o, c = price, price * (1 + slope + rnd.gauss(0, 0.002))
        out.append(_bar(i, o, max(o, c) * 1.001, min(o, c) * 0.999, c, 100.0))
        price = c
    return out


def _pullback_candle(candles, volume):
    """Último candle: toca a EMA lenta, pavio inferior longo, fecha em alta."""
    df = pd.DataFrame({"close": [c.close for c in candles]})
    ema = float(ta.ema(df["close"], length=21).iloc[-1])
    atr = float(ta.atr(pd.Series([c.high for c in candles]), pd.Series([c.low for c in candles]),
                       df["close"], length=14).iloc[-1])
    o = ema + 0.3 * atr
    c = o + 0.15 * atr
    l = ema - 0.1 * atr
    h = c + 0.02 * atr
    return _bar(len(candles), o, h, l, c, volume)


def _strategy():
    return REGISTRY["TF016"]()


def test_buy_has_honest_risk_reward():
    base = _trend(200, 0.002)
    candles = base + [_pullback_candle(base, volume=300.0)]
    res = _strategy().compute_with_context(candles)
    assert res.signal == Signal.BUY, res.hold_reason
    close, sl, tp = candles[-1].close, res.metadata["sl_price"], res.metadata["tp1_price"]
    assert sl < close < tp
    assert abs((tp - close) / (close - sl) - 2.0) < 0.01            # tp1_rr real sobre o stop efetivo
    atr = res.indicators["atr"]
    assert close - sl >= 2.0 * atr - 1e-3                             # stop nunca mais curto que sl_mult×ATR


def test_below_average_volume_no_longer_counts_as_dominance():
    base = _trend(200, 0.002)
    candles = base + [_pullback_candle(base, volume=70.0)]            # 70% da média: passava com o limiar antigo (65%)
    res = _strategy().compute_with_context(candles)
    assert res.signal == Signal.HOLD and "Volume insuficiente" in res.hold_reason


def test_sell_means_exit_when_bias_is_lost():
    candles = _trend(200, 0.002) + _trend(80, -0.006)                  # tendência de alta seguida de queda forte
    res = _strategy().compute_with_context(candles)
    assert res.signal == Signal.SELL and res.hold_reason.startswith("Saída")
    s = _strategy()
    s.set_params({"exit_on_bias_loss": 0})
    assert s.compute_with_context(candles).signal == Signal.HOLD
