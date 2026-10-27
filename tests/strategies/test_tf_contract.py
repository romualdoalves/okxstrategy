"""Contrato mínimo das estratégias TF (checklist da auditoria, automatizado).

- Pureza: o resultado só depende dos candles (nada de relógio do servidor) -> backtest == live.
- Coerência: criteria_met <= criteria_total; BUY traz SL abaixo e TP acima do preço.
- Robustez: não explode com séries curtas/planas.
"""
import inspect
import math
import random
from types import SimpleNamespace as NS

from backend.exchanges.base import CandleBar

import pytest

from backend.candle_store import timeframe_ms
from backend.strategies.base import Signal
from backend.strategies.registry import REGISTRY

TF_IDS = sorted(s for s in REGISTRY if s.startswith("TF"))
ALL_IDS = sorted(REGISTRY)


def _walk(n, step_ms, seed, start=1_600_000_000_000, p0=100.0, drift=0.0005):
    rnd, price, out = random.Random(seed), p0, []
    for i in range(n):
        ret = drift + rnd.gauss(0, 0.012)
        o, c = price, price * math.exp(ret)
        out.append(CandleBar(epoch=start + i * step_ms, open=o, high=max(o, c) * 1.004, low=min(o, c) * 0.996,
                             close=c, volume=rnd.uniform(50, 500)))
        price = c
    return out


def _context(cls, base_step):
    extra = {}
    for tf in cls.extra_timeframes() or []:
        step = timeframe_ms(tf)
        extra[tf] = _walk(max(300, int(600 * base_step / step) + 60), step, seed=hash(tf) % 1000,
                          start=1_600_000_000_000 - 100 * step)
    return {"extra_candles": extra}


@pytest.mark.parametrize("sid", TF_IDS)
def test_tf_is_pure_and_coherent(sid):
    cls = REGISTRY[sid]
    if any(getattr(cls, a, False) for a in dir(cls) if a.startswith("needs_")):
        pytest.skip("depende de contexto externo")
    tf = (cls.info().recommended_timeframe or "15m")
    step = timeframe_ms(tf) or 900_000
    candles = _walk(700, step, seed=11)
    ctx = _context(cls, step)

    for end in range(450, 700, 25):          # várias fatias: BUY/SELL/HOLD aparecem em algumas
        window = candles[:end]
        a = cls().compute_with_context(window, ctx)
        b = cls().compute_with_context(window, ctx)
        if a is None:
            assert b is None
            continue
        assert (a.signal, a.indicators, a.metadata, a.hold_reason) == (b.signal, b.indicators, b.metadata, b.hold_reason), \
            f"{sid}: resultado não determinístico (depende de relógio/estado externo?)"
        assert 0 <= a.criteria_met <= max(a.criteria_total, 1), f"{sid}: criteria_met fora de 0..total"
        if a.signal == Signal.BUY:
            close = window[-1].close
            sl, tp = a.metadata.get("sl_price"), a.metadata.get("tp1_price")
            if sl is not None:
                assert 0 < sl < close, f"{sid}: SL de BUY deve ficar abaixo do preço"
            if tp is not None:
                assert tp > close, f"{sid}: TP1 de BUY deve ficar acima do preço"


@pytest.mark.parametrize("sid", TF_IDS)
def test_tf_does_not_read_wall_clock(sid):
    src = inspect.getsource(inspect.getmodule(REGISTRY[sid]))
    assert "datetime.now" not in src and "time.time(" not in src and "utcnow" not in src, \
        f"{sid}: usa relógio do servidor — quebra backtest/reprocessamento"


@pytest.mark.parametrize("sid", TF_IDS)
def test_tf_short_series_returns_none_or_hold(sid):
    cls = REGISTRY[sid]
    if any(getattr(cls, a, False) for a in dir(cls) if a.startswith("needs_")):
        pytest.skip("depende de contexto externo")
    r = cls().compute_with_context(_walk(8, 900_000, seed=3), {"extra_candles": {}})
    assert r is None or r.signal == Signal.HOLD


@pytest.mark.parametrize("sid", ALL_IDS)
def test_every_strategy_runs_on_real_candlebars(sid):
    """Qualquer estratégia (não só TF) tem de rodar com CandleBar de verdade: antes a SC005 e a PA011 liam
    c.time (inexistente) e lançavam AttributeError em toda chamada, sem ninguém notar."""
    cls = REGISTRY[sid]
    if any(getattr(cls, a, False) for a in dir(cls) if a.startswith("needs_")):
        pytest.skip("depende de contexto externo")
    step = timeframe_ms(cls.info().recommended_timeframe or "15m") or 900_000
    candles = _walk(500, step, seed=21)
    for end in (350, 450, 500):
        cls().compute_with_context(candles[:end], _context(cls, step))
