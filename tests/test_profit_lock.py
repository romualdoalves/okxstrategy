"""Trava de lucro em degraus (+0,5% a cada marco): stop só sobe, 1º degrau = breakeven LÍQUIDO, P&L exibido líquido."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from backend import profit_lock as pl
from backend.bot.position import PositionMixin


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    monkeypatch.setenv("OKX_LOCK_STEP_PCT", "0.005")      # a regra vem desligada; os testes exercitam ela ligada
    monkeypatch.delenv("OKX_FEE_RATE", raising=False)


ENTRY = 76_000.0


def test_first_step_is_net_breakeven_not_entry():
    be = pl.breakeven_stop(ENTRY)
    assert be > ENTRY * 1.002                      # cobre taxa de entrada + saída + slippage
    exit_px = be * (1 - pl.EXIT_SLIPPAGE_RATE)
    net = pl.net_pct_at_price(ENTRY, exit_px)
    assert abs(net) < 0.01                         # sair no stop dá ~0 líquido, não prejuízo


def test_no_step_before_half_percent():
    assert pl.ratchet_stop(ENTRY, ENTRY * 1.0049) is None
    assert pl.ratchet_stop(ENTRY, ENTRY) is None


def test_stop_climbs_one_step_per_half_percent():
    assert pl.ratchet_stop(ENTRY, ENTRY * 1.005) == pytest.approx(pl.breakeven_stop(ENTRY))
    assert pl.ratchet_stop(ENTRY, ENTRY * 1.0099) == pytest.approx(pl.breakeven_stop(ENTRY))
    assert pl.ratchet_stop(ENTRY, ENTRY * 1.010) == pytest.approx(ENTRY * 1.005)
    assert pl.ratchet_stop(ENTRY, ENTRY * 1.0155) == pytest.approx(ENTRY * 1.010)
    assert pl.ratchet_stop(ENTRY, ENTRY * 1.030) == pytest.approx(ENTRY * 1.025)


def test_step_of_stop_roundtrip_and_view():
    for k in range(1, 8):
        assert pl.step_of_stop(ENTRY, pl.stop_of_step(ENTRY, k)) == k
    assert pl.step_of_stop(ENTRY, ENTRY * 0.97) == 0          # stop inicial abaixo da entrada
    view = pl.steps_view(ENTRY, ENTRY * 1.010, reached_at={1: "t1"})
    assert view["reached"] == 3
    assert view["steps"][0]["stop_pct_net"] == pytest.approx(0.0, abs=0.01)      # 1º degrau: sem prejuízo, não lucro
    assert view["steps"][1]["stop_pct_net"] == pytest.approx(0.5 - 0.2 - 0.05, abs=0.02)   # degrau 2: +0,5% − taxas − slippage
    assert view["steps"][2]["stop_pct_net"] == pytest.approx(1.0 - 0.2 - 0.05, abs=0.02)   # degrau 3: +1,0% − custos
    assert [s["step"] for s in view["steps"]] == list(range(1, view["reached"] + 1))
    assert view["steps"][0]["reached_at"] == "t1"
    assert view["next"]["trigger_price"] == pytest.approx(ENTRY * (1 + (view["reached"] + 1) * 0.005))


def test_off_by_default_and_by_env(monkeypatch):
    monkeypatch.delenv("OKX_LOCK_STEP_PCT", raising=False)
    assert pl.ratchet_stop(ENTRY, ENTRY * 1.05) is None
    monkeypatch.setenv("OKX_LOCK_STEP_PCT", "0")
    assert pl.ratchet_stop(ENTRY, ENTRY * 1.05) is None
    assert pl.steps_view(ENTRY, ENTRY * 0.97)["enabled"] is False


def test_net_pnl_discounts_both_fees():
    qty = 100.0 / ENTRY
    usd, pct = pl.net_unrealized(ENTRY, ENTRY * 1.0093, qty)      # o +0,93% bruto do relato
    assert 0.70 < pct < 0.75                                      # ~0,93% − 0,2% de taxas
    assert usd == pytest.approx(pct)                              # stake de US$100
    assert pl.net_unrealized(ENTRY, ENTRY, qty)[1] < 0            # entrada=saída é prejuízo de taxas


def test_trailing_callback_never_looser_than_locked_stop():
    price = ENTRY * 1.03
    locked = ENTRY * 1.025
    cb = pl.capped_callback(0.05, locked, price, ENTRY)
    assert price * (1 - cb) >= locked - 1e-6
    # sem stop travado (ainda abaixo da entrada) segue a estratégia
    assert pl.capped_callback(0.05, ENTRY * 0.97, price, ENTRY) == 0.05


# ── bot ao vivo ─────────────────────────────────────────────────────────────────────────────────────────────────
class Bot(PositionMixin):
    def __init__(self):
        self.config = NS(id=1, symbol="BTC-USDT", name="b")
        self._direction, self._entry_price, self._sz = 1, ENTRY, 0.0013
        self._sl_price, self._sl_algo_id, self._peak_price = ENTRY * 0.9, "old1", 0.0
        self._tp1_done, self._lock_retry_at, self._lock_events = False, 0.0, {}
        self._ts_pct, self._ts_callback_ratio, self._current_atr, self._tp1_price = 0.0, 0.0, 0.0, 0.0
        self.broadcasts = []

    async def _broadcast(self, m): self.broadcasts.append(m)
    def _persist_trailing_stop_level(self): pass
    def _record_order_rejection(self, **kw): self.rejected = kw


class Ex:
    def __init__(self, accept=True):
        self.accept, self.calls, self.last_order_error = accept, [], {"message": "recusado"}

    async def place_stop_loss(self, symbol, side, size, px):
        self.calls.append(("place", px))
        return f"new{len(self.calls)}" if self.accept else None

    async def cancel_algo(self, symbol, algo_id):
        self.calls.append(("cancel", algo_id))
        return True


def run(c): return asyncio.run(c)


def test_live_lock_places_new_stop_before_cancelling_old_and_only_climbs():
    bot, ex = Bot(), Ex()
    run(bot._check_profit_lock(ENTRY * 1.004, ex))
    assert ex.calls == []                                           # abaixo de +0,5%: nada
    run(bot._check_profit_lock(ENTRY * 1.006, ex))
    assert [c[0] for c in ex.calls] == ["place", "cancel"]          # novo primeiro, depois cancela o antigo
    assert bot._sl_price == pytest.approx(pl.breakeven_stop(ENTRY), rel=1e-6)
    assert bot._sl_algo_id == "new1" and 1 in bot._lock_events
    n = len(ex.calls)
    bot._lock_retry_at = 0.0
    run(bot._check_profit_lock(ENTRY * 1.0065, ex))                 # mesmo degrau: não mexe
    assert len(ex.calls) == n
    bot._peak_price = ENTRY * 1.0105                                # 2º degrau (+1,0%) → stop em +0,5%
    run(bot._check_profit_lock(ENTRY * 1.0102, ex))
    assert bot._sl_price == pytest.approx(ENTRY * 1.005)
    assert {1, 2} <= set(bot._lock_events)
    run(bot._check_profit_lock(ENTRY * 1.002, ex))                  # preço recuou: o stop NÃO desce
    assert bot._sl_price == pytest.approx(ENTRY * 1.005)


def test_live_lock_refused_keeps_old_stop_and_does_not_hammer_the_api():
    bot, ex = Bot(), Ex(accept=False)
    run(bot._check_profit_lock(ENTRY * 1.006, ex))
    assert bot._sl_algo_id == "old1" and bot._sl_price == ENTRY * 0.9
    assert bot.rejected["order_type"] == "profit_lock"
    run(bot._check_profit_lock(ENTRY * 1.006, ex))                  # dentro dos 30s: não tenta de novo
    assert len([c for c in ex.calls if c[0] == "place"]) == 1


def test_profit_view_is_net_and_guaranteed_only_when_locked():
    bot = Bot()
    v = bot._profit_view(ENTRY * 1.0093)
    assert 0.70 < v["unrealized_net_pct"] < 0.75 and v["guaranteed_pnl"] == 0.0
    bot._sl_price = pl.breakeven_stop(ENTRY)
    v = bot._profit_view(ENTRY * 1.0093)
    assert v["profit_lock"]["reached"] == 1
    assert v["guaranteed_pnl"] == pytest.approx(0.0, abs=0.01)     # breakeven líquido: garante NÃO perder, não lucrar
    bot._sl_price = ENTRY * 1.010                                  # 3º degrau → garante ~+0,75% líquido
    assert bot._profit_view(ENTRY * 1.02)["guaranteed_pnl"] == pytest.approx(0.75, abs=0.02)


def test_trailing_view_reports_the_parameters_the_bot_adopts():
    bot = Bot()
    bot._tp1_price, bot._ts_pct, bot._ts_callback_ratio, bot._current_atr = ENTRY * 1.40, 0.0, 0.0, 800.0
    bot._sl_price = ENTRY * 0.90
    v = bot._trailing_view(ENTRY * 1.0093)
    assert v["armed"] is False and v["arm_pct"] == pytest.approx(40.0)
    assert v["callback_source"] == "atr" and v["callback_pct"] == pytest.approx(1.5 * 800 / (ENTRY * 1.0093) * 100, abs=0.01)
    assert v["stop_pct_net"] < 0                                # stop inicial: perda de ~10%
    bot._ts_pct = 0.02                                           # a estratégia define a distância
    assert bot._trailing_view(ENTRY)["callback_pct"] == 2.0 and bot._trailing_view(ENTRY)["callback_source"] == "strategy"
    bot._tp1_done, bot._ts_callback_ratio = True, 0.031          # já armado: mostra o valor enviado à OKX
    assert bot._trailing_view(ENTRY * 1.4)["callback_pct"] == 3.1 and bot._trailing_view(ENTRY * 1.4)["armed"] is True


def test_live_price_drives_status_not_the_last_closed_candle():
    """Regressão: bot 1D mostrava líquido +0,74% com bruto +0,01% porque o líquido usava o fechamento do último
    candle fechado (defasado até 24h) e o bruto o preço ao vivo."""
    from backend.bot.instance import BotInstance
    bot = BotInstance.__new__(BotInstance)
    bot.__dict__.update(
        config=NS(id=1, symbol="BTC-USDT", name="b", timeframe="1D"),
        status="running", _candles=[NS(close=ENTRY * 1.0094, epoch=1)], _last_price=ENTRY * 1.0001,
        _direction=1, _entry_price=ENTRY, _sz=0.0013, _sl_price=ENTRY * 0.9, _tp1_price=ENTRY * 1.36,
        _tp1_done=False, _ts_pct=0.0, _ts_callback_ratio=0.0, _current_atr=800.0, _lock_events={},
        _peak_price=0.0, _daily_pnl=0.0, _wins=0, _losses=0, _halted=False, _hold_reason="", _last_indicators={},
        _last_order_criteria={}, _last_order_error=None, _maintenance=None, _started_at=None,
        _closed_candle_count=0, _last_closed_candle_at=None, _graph_state=None, _onchain_events=[],
        _goal_loaded=False, _goal_done=False, _goal_realized_pct=0.0, _goal_outcome=None, _last_meta={},
    )
    st = bot.get_status()
    assert st["last_price"] == pytest.approx(ENTRY * 1.0001)
    assert st["unrealized_net_pct"] < 0                       # +0,01% bruto − ~0,2% de taxas
    assert st["unrealized_net_pct"] <= (st["last_price"] / ENTRY - 1) * 100   # líquido nunca excede o bruto
    assert st["fee_rate"] == pl.fee_rate()


def test_trailing_policy_is_available_without_a_position():
    bot = Bot()
    bot._direction, bot._entry_price, bot._sl_price = 0, 0.0, 0.0
    bot._last_meta = {"sl_price": ENTRY * 0.93, "tp1_price": ENTRY * 1.36}
    bot._ts_pct, bot._ts_callback_ratio, bot._current_atr = 0.0, 0.0, 2000.0
    v = bot._trailing_view(ENTRY)
    assert v["flat"] is True and v["armed"] is False
    assert v["arm_price"] == pytest.approx(ENTRY * 1.36) and v["arm_pct"] == pytest.approx(36.0)
    assert v["callback_source"] == "atr" and v["callback_pct"] == pytest.approx(3.95, abs=0.01)   # 1,5×2000/76000
    assert v["stop_price"] == pytest.approx(ENTRY * 0.93) and v["stop_pct_net"] < -7
    assert v["lock_step_pct"] == 0.5                                # fixture liga os degraus; desligado → None
    bot._last_meta = {"tp1_price": ENTRY * 1.05, "ts_pct": 0.02}
    v = bot._trailing_view(ENTRY)
    assert v["callback_source"] == "strategy" and v["callback_pct"] == 2.0
    nopx = Bot()
    nopx._direction, nopx._entry_price = 0, 0.0
    assert nopx._trailing_view(0.0) is None                        # sem preço algum não inventa números
