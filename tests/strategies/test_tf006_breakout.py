"""TF006: o rompimento do Donchian é medido contra o canal ANTERIOR e vale por `breakout_weeks` semanas.
Antes o canal incluía a própria semana: o fechamento só 'rompia' se coincidisse com a máxima do ano."""
import math
import random

from backend.exchanges.base import CandleBar
from backend.strategies.registry import REGISTRY

DAY, WEEK = 86_400_000, 7 * 86_400_000
T0 = 1_500_000_000_000 - (1_500_000_000_000 % WEEK)


def _weekly(closes, spread=0.01):
    out = []
    for i, c in enumerate(closes):
        out.append(CandleBar(T0 + i * WEEK, c, c * (1 + spread), c * (1 - spread), c, 1000.0))
    return out


def _daily(n, price, seed=1):
    rnd, p, out = random.Random(seed), price, []
    for i in range(n):
        o, c = p, p * math.exp(rnd.gauss(0, 0.01))
        out.append(CandleBar(T0 + i * DAY, o, max(o, c) * 1.003, min(o, c) * 0.997, c, 100.0))
        p = c
    return out


def _break_flag(closes, **params):
    weekly = _weekly(closes)
    daily = _daily(len(closes) * 7, closes[-1])
    s = REGISTRY["TF006"]()
    s.set_params(params)
    res = s.compute_with_context(daily, {"extra_candles": {"1W": weekly}})
    return res.indicators["donchian_break"]


def test_close_above_the_prior_channel_is_a_breakout_even_if_not_at_the_weekly_high():
    flat = [100.0] * 70
    # 3 semanas de alta forte; a última fecha ABAIXO da própria máxima semanal (antes: não contava)
    closes = flat + [108.0, 116.0, 124.0]
    assert _break_flag(closes, breakout_weeks=1) == 1


def test_breakout_stays_valid_for_breakout_weeks_then_expires():
    closes = [100.0] * 70 + [120.0] + [121.0, 121.5, 121.0, 120.5, 121.0, 121.0, 121.0, 121.0, 121.0]
    assert _break_flag(closes, breakout_weeks=12) == 1       # o rompimento (semana 71) ainda vale
    assert _break_flag(closes, breakout_weeks=3) == 0        # já passaram mais de 3 semanas
    # (nas semanas seguintes ao rompimento o fechamento segue ACIMA do canal anterior, mas o canal em si
    #  já incorporou a máxima do rompimento — por isso `breakout_weeks=3` não vê um novo rompimento)


def test_downside_breakout_is_negative():
    closes = [100.0] * 70 + [92.0, 84.0, 76.0]
    assert _break_flag(closes, breakout_weeks=1) == -1
