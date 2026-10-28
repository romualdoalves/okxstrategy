"""MR007 lia `candle.time` (inexistente em CandleBar) e nunca operava; agora usa `epoch` (ms)."""
from backend.exchanges.base import CandleBar
from backend.strategies.base import Signal
from backend.strategies.registry import REGISTRY

DAY_MS = 86_400_000
T0 = 1_577_836_800_000  # 2020-01-01


def _bars(price):
    return [CandleBar(epoch=T0 + i * DAY_MS, open=price, high=price * 1.01, low=price * 0.99, close=price, volume=1.0)
            for i in range(40)]


def test_mr007_uses_epoch_and_signals():
    s = REGISTRY["MR007"]()
    cheap = s.compute(_bars(1000.0))            # muito abaixo do valor justo -> compra
    assert cheap.signal == Signal.BUY and "tempo ausente" not in cheap.hold_reason
    assert cheap.indicators["days_alive"] > 4000   # ~4 mil dias desde 2009 (epoch em ms convertido para s)
    rich = s.compute(_bars(500_000.0))          # muito acima -> venda (saída)
    assert rich.signal == Signal.SELL
