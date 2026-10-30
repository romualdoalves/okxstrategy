"""Holdings do usuário (baseline) não são posição do bot. Em spot, get_position devolve o saldo TOTAL da moeda;
sem descontar o baseline o bot (a) nunca entrava, (b) registrava o BTC do usuário como tamanho da posição
(o TP1 parcial venderia o BTC dele) e (c) nunca conseguia confirmar que a saída zerou."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from backend.bot import criteria as criteria_mod
from backend.bot.criteria import OrderGateMixin


class Bot(OrderGateMixin):
    def __init__(self, symbol="BTC-USDT", baseline=1.0, direction=0):
        self.config = NS(id=1, symbol=symbol, baseline_balance=baseline, stake_usd=100.0, leverage=1)
        self._direction = direction
        self._sz = 0.0
        self._sz_remaining = 0.0
        self.closed = []

    async def _on_position_closed(self, price, size, exchange):
        self.closed.append((price, size))


class Ex:
    def __init__(self, sizes):
        self.sizes = list(sizes)

    async def get_position(self, symbol):
        size = self.sizes.pop(0) if len(self.sizes) > 1 else self.sizes[0]
        return NS(size=size, side="long", avg_price=99999.0) if size else None   # avg_price = último preço (spot)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def fast(_):
        return None
    monkeypatch.setattr(criteria_mod.asyncio, "sleep", fast)


def run(coro):
    return asyncio.run(coro)


def test_net_size_discounts_baseline_only_on_spot():
    spot = Bot("BTC-USDT", baseline=1.0)
    assert spot._net_size(NS(size=1.0)) == 0.0
    assert abs(spot._net_size(NS(size=1.001)) - 0.001) < 1e-12
    swap = Bot("BTC-USDT-SWAP", baseline=1.0)
    assert swap._net_size(NS(size=1.0)) == 1.0


def test_flat_bot_with_only_user_holdings_may_enter():
    bot = Bot(baseline=1.0, direction=0)
    assert bot._exchange_has_bot_position(NS(size=1.0)) is False          # só o baseline: entra
    assert bot._exchange_has_bot_position(NS(size=1.001)) is True         # sobra de verdade: bloqueia/trata
    assert bot._exchange_has_bot_position(None) is False
    assert Bot(baseline=1.0, direction=1)._exchange_has_bot_position(NS(size=1.0)) is True   # trade local aberto


def test_entry_size_is_the_net_size_not_the_users_balance():
    bot = Bot(baseline=1.0)
    assert abs(run(bot._entry_size_from_exchange(Ex([1.001]), 0.00123)) - 0.001) < 1e-12


def test_entry_fill_uses_fill_price_and_net_size_on_spot():
    bot = Bot(baseline=1.0)
    price, size = run(bot._sync_entry_fill_from_exchange(Ex([1.001]), "oid", 80_000.0, 0.00125))
    assert price == 80_000.0                      # não o "avg_price" (último preço) da OKX
    assert abs(size - 0.001) < 1e-12              # não 1.001 (o BTC do usuário junto)


def test_entry_fill_waits_until_the_balance_reflects_the_fill():
    bot = Bot(baseline=1.0)
    price, size = run(bot._sync_entry_fill_from_exchange(Ex([1.0, 1.0, 1.001]), "oid", 80_000.0, 0.00125))
    assert abs(size - 0.001) < 1e-12              # ignorou as leituras só com o baseline


def test_exit_is_confirmed_when_only_the_baseline_remains():
    bot = Bot(baseline=1.0, direction=1)
    assert run(bot._confirm_flat_then_close(80_000.0, 0.001, Ex([1.0]))) is True
    assert bot.closed == [(80_000.0, 0.001)]


def test_exit_not_confirmed_while_position_remains():
    bot = Bot(baseline=1.0, direction=1)
    assert run(bot._confirm_flat_then_close(80_000.0, 0.001, Ex([1.001]))) is False
    assert bot.closed == [] and abs(bot._sz - 0.001) < 1e-12


def test_dust_residue_after_baseline_is_not_a_position():
    # saldo total ≈ baseline: sobra de 1e-8 BTC (~US$ 0,001) é arredondamento, não posição
    bot = Bot(baseline=0.05, direction=0)
    pos = NS(size=0.05000001, side="long", avg_price=76_000.0)
    assert bot._net_size(pos) == 0.0
    assert bot._exchange_has_bot_position(pos) is False
    assert bot._can_adopt_spot_position(pos, context="test") is False
    # posição real (≈ US$ 100) continua contando
    assert abs(bot._net_size(NS(size=0.05132, side="long", avg_price=76_000.0)) - 0.00132) < 1e-12


class PosBot(criteria_mod.OrderGateMixin):
    """Liquidação manual: ordem recusada não pode zerar P&L nem o estado."""
    from backend.bot.position import PositionMixin as _P
    manual_liquidate = _P.manual_liquidate

    def __init__(self):
        self.config = NS(id=1, symbol="BTC-USDT", baseline_balance=0.0, name="b")
        self._direction, self._sz, self._entry_price = 1, 0.001, 76_000.0
        self._candles, self.events = [], []

    async def _broadcast(self, msg):
        self.events.append(msg)

    def _save_trade(self, *a, **k):
        raise AssertionError("não deve gravar trade quando a ordem foi recusada")

    def _reset(self):
        raise AssertionError("não deve zerar o estado quando a ordem foi recusada")


class FailingEx:
    last_order_error = {"message": "OKX: insufficient balance"}

    async def get_ticker(self, symbol):
        return {"last": 76_500.0}

    async def get_position(self, symbol):
        return NS(size=0.001, side="long", avg_price=76_500.0)

    async def market_order(self, **kw):
        return None


def test_failed_manual_close_keeps_position_and_pnl():
    bot = PosBot()
    assert run(bot.manual_liquidate(FailingEx())) is False
    assert bot._direction == 1 and bot._entry_price == 76_000.0
    assert "insufficient" in bot.last_close_error
    assert bot.events[0]["error"]["kind"] == "manual_close_failed"
