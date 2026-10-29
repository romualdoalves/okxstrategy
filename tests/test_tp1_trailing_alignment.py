"""O bot arma o trailing como o backtest: no TP1 da estratégia e com o ts_pct dela (sem breakeven nem teto fixo)."""
import asyncio
from types import SimpleNamespace

from backend.bot.position import PositionMixin


class _Bot(PositionMixin):
    def __init__(self, direction=1, entry=100.0, tp1=110.0, ts_pct=0.0, atr=0.0):
        self._direction, self._entry_price, self._tp1_price = direction, entry, tp1
        self._ts_pct, self._current_atr = ts_pct, atr
        self._tp1_done = False
        self._sz, self._sl_price, self._sl_algo_id, self._ts_algo_id = 1.0, 95.0, "sl1", None
        self._peak_price, self._current_trade_id, self._ts_callback_ratio = 0.0, None, 0.0
        self.config = SimpleNamespace(id=1, symbol="BTC-USDT", name="b")
        self.calls = []

    def _record_order_rejection(self, **k): pass
    def _save_trade(self, *a, **k): pass
    def _persist_trailing_stop_level(self): pass
    async def _broadcast(self, *_): pass
    _notifier = SimpleNamespace(send=lambda m: asyncio.sleep(0))


class _Ex:
    def __init__(self): self.calls = []
    async def place_trailing_stop(self, sym, side, sz, cb, px): self.calls.append(("ts", cb)); return "ts1"
    async def cancel_algo(self, sym, aid): self.calls.append(("cancel", aid)); return True


def test_callback_uses_strategy_ts_pct_else_atr_clamped():
    assert _Bot(ts_pct=0.03)._trailing_callback(100) == 0.03
    assert _Bot(atr=0.1)._trailing_callback(100) == 0.005          # piso
    assert _Bot(atr=2.0)._trailing_callback(100) == 0.03           # 1,5 ATR
    assert _Bot(atr=20.0)._trailing_callback(100) == 0.05          # teto


def test_tp1_reached_follows_strategy_price_not_fixed_one_percent():
    b = _Bot(tp1=110.0)
    assert not b._tp1_reached(101.5)      # +1,5%: antes já armava o trailing
    assert b._tp1_reached(110.0)
    assert _Bot(direction=-1, tp1=90.0)._tp1_reached(89.0)


def test_no_trailing_before_tp1_and_arms_at_tp1():
    ex, b = _Ex(), _Bot(tp1=110.0, ts_pct=0.03)
    asyncio.run(b._check_tp1(103.0, ex))
    assert ex.calls == [] and not b._tp1_done                       # sem breakeven nem trailing aos +3%
    asyncio.run(b._check_tp1(110.5, ex))
    assert ex.calls == [("ts", 0.03), ("cancel", "sl1")] and b._tp1_done
