"""Candle fechado é a única verdade: backtest sem lookahead multi-TF e live sem candle parcial."""
from types import SimpleNamespace as NS

from backend.backtest_engine import BacktestEngine
from backend.candle_store import drop_open_candle, timeframe_ms
from backend.strategies.base import BaseStrategy, Signal, StrategyInfo, StrategyResult

DAY = 86_400_000
WEEK = 7 * DAY
T0 = 1_600_000_000_000 - (1_600_000_000_000 % WEEK)   # início de uma semana


def _candle(epoch, price=100.0):
    return NS(epoch=epoch, open=price, high=price * 1.01, low=price * 0.99, close=price, volume=1.0)


class _Recorder(BaseStrategy):
    """Registra o que a estratégia enxerga em cada passo; nunca opera."""
    seen: list = []

    @classmethod
    def info(cls):
        return StrategyInfo(id="X", name="x", description="x", params={})

    def compute(self, candles):
        return None

    def set_params(self, params):
        pass

    def compute_with_context(self, candles, context=None):
        self.seen.append((candles[-1].epoch, [c.epoch for c in (context or {}).get("extra_candles", {}).get("1W", [])]))
        return StrategyResult(signal=Signal.HOLD, indicators={})


def test_extra_timeframe_only_exposes_closed_candles():
    base = [_candle(T0 + i * DAY) for i in range(200)]
    weekly = [_candle(T0 + w * WEEK) for w in range(30)]
    eng = object.__new__(BacktestEngine)
    eng.strategy = _Recorder()
    eng.strategy.seen = []
    eng.balance, eng.events, eng.closed_trades = 1000.0, [], []

    eng.run_strict_sync(base, extra_candles={"1W": weekly})

    assert eng.strategy.seen, "a estratégia deveria ter sido chamada"
    for bar_epoch, weekly_epochs in eng.strategy.seen:
        bar_close = bar_epoch + DAY
        for w in weekly_epochs:
            assert w + WEEK <= bar_close, f"semana aberta em {w} visível antes de fechar (barra {bar_epoch})"
    # e a semana que fecha exatamente no fechamento do candle diário JÁ é visível
    last_bar, last_weeks = eng.strategy.seen[-1]
    expected = max(w for w in (T0 + k * WEEK for k in range(30)) if w + WEEK <= last_bar + DAY)
    assert last_weeks[-1] == expected


def test_drop_open_candle_removes_only_the_forming_one():
    now = T0 + 3 * DAY + 5 * 3_600_000          # quinta, 05:00
    closed = [_candle(T0 + i * DAY) for i in range(3)]          # seg, ter, qua (fechados)
    forming = _candle(T0 + 3 * DAY)                              # quinta (em formação)
    assert drop_open_candle(closed + [forming], "1D", now_ms=now) == closed
    assert drop_open_candle(closed, "1D", now_ms=now) == closed  # nada a remover
    assert drop_open_candle([], "1D", now_ms=now) == []
    assert drop_open_candle(closed + [forming], "banana", now_ms=now) == closed + [forming]


def test_timeframe_ms_knows_weekly():
    assert timeframe_ms("1W") == WEEK and timeframe_ms("1D") == DAY and timeframe_ms("4H") == 4 * 3_600_000
