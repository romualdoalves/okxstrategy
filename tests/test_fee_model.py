"""A taxa por lado é uma só para o app inteiro: env > medida nos fills reais > padrão (0,40%, o que a conta paga)."""
import asyncio

import pytest

from backend import fee_model, goal
from backend.fills import reconcile_fills
from backend.profit_lock import breakeven_stop, fee_rate as lock_fee_rate


def _fill(i, side, px, qty, fee, ccy):
    return {"id": str(i), "transaction_time": f"2026-10-30T1{i}:00:00.000Z", "side": side, "price": str(px), "qty": str(qty),
            "fee": str(fee), "fee_currency": ccy, "symbol": "BTC-USDT", "exec_type": "Taker"}


REAL = [_fill(1, "buy", 76845.9, 0.00122284, -0.0000049, "BTC"), _fill(2, "sell", 77101.6, 0.00121794, -0.3756205, "USDT"),
        _fill(3, "buy", 76873.6, 0.00128876, -0.00000516, "BTC"), _fill(4, "sell", 76900.0, 0.0012836, -0.39485, "USDT")]


def test_default_is_the_rate_the_account_actually_pays(monkeypatch):
    monkeypatch.delenv("OKX_FEE_RATE", raising=False)
    assert fee_model.fee_rate() == 0.004 and fee_model.source() == "default"
    assert lock_fee_rate() == 0.004                                    # trava de lucro, cards e meta leem a mesma fonte
    assert goal.net_equivalent_pct(1.0) == pytest.approx(1.0 - 0.004 * 201)   # +1% bruto ≈ +0,20% líquido, não +0,80%
    assert breakeven_stop(100.0) > 100.0 * 1.008                       # o breakeven cobre as duas taxas de 0,4%


def test_env_overrides_everything(monkeypatch):
    monkeypatch.setenv("OKX_FEE_RATE", "0.0015")
    fee_model.observe(0.004, 10)
    assert fee_model.fee_rate() == 0.0015 and fee_model.source() == "env"


def test_rate_measured_in_real_fills_replaces_the_default(monkeypatch):
    monkeypatch.delenv("OKX_FEE_RATE", raising=False)
    rate, n = fee_model.observed_rate(reconcile_fills(REAL))
    assert n == 4 and rate == pytest.approx(0.004, abs=0.0001)
    fee_model.observe(rate, n)
    assert fee_model.source() == "fills" and fee_model.fee_rate() == pytest.approx(0.004, abs=0.0001)
    fee_model.observe(0.0012, n)                                      # a conta mudou de nível: o app acompanha
    assert fee_model.fee_rate() == pytest.approx(0.0012)


def test_few_fills_or_absurd_rates_do_not_move_the_rate(monkeypatch):
    monkeypatch.delenv("OKX_FEE_RATE", raising=False)
    assert fee_model.observed_rate(reconcile_fills(REAL[:2]))[0] is None
    fee_model.observe(0.003, 2)
    fee_model.observe(0.5, 50)
    assert fee_model.fee_rate() == 0.004 and fee_model.source() == "default"


def test_maker_fills_do_not_pull_the_taker_rate_down(monkeypatch):
    monkeypatch.delenv("OKX_FEE_RATE", raising=False)
    fills = [dict(f, exec_type="Maker", fee=str(float(f["fee"]) / 4)) for f in REAL] + REAL
    rate, n = fee_model.observed_rate(reconcile_fills(fills))
    assert n == 4 and rate == pytest.approx(0.004, abs=0.0001)


def test_backtest_charges_the_current_rate_and_the_worker_receives_it(monkeypatch):
    from backend.backtest_engine import BacktestEngine
    from tests.test_backtest_service import synthetic_candles
    cs = synthetic_candles(900, seed=11)
    cheap = BacktestEngine("TF003-1D").run_strict_sync(cs, fee_rate=0.001)
    dear = BacktestEngine("TF003-1D").run_strict_sync(cs, fee_rate=0.004)
    assert dear["total_profit"] < cheap["total_profit"]
    monkeypatch.setenv("OKX_FEE_RATE", "0.004")
    assert BacktestEngine("TF003-1D").run_strict_sync(cs)["total_profit"] == pytest.approx(dear["total_profit"])


def test_daily_pnl_and_win_count_are_net_of_fees():
    from types import SimpleNamespace
    from backend.bot.accounting import AccountingMixin
    bot = SimpleNamespace(config=SimpleNamespace(stake_usd=100.0, stop_loss_usd=-50.0, id=1), _daily_pnl=0.0, _wins=0, _losses=0,
                          _halted=False, _pnl_day=None, _roll_day=lambda: None)
    bot._estimated_fees = lambda: AccountingMixin._estimated_fees(bot)
    AccountingMixin._record_pnl(bot, 0.15)          # +0,15 de preço com taxas de 0,20: perdeu dinheiro
    assert bot._daily_pnl == pytest.approx(-0.05) and (bot._wins, bot._losses) == (0, 1)
    AccountingMixin._record_pnl(bot, 1.0, fee=0.2)
    assert bot._daily_pnl == pytest.approx(0.75) and (bot._wins, bot._losses) == (1, 1)


def test_exit_message_shows_net_first_then_gross_and_fees():
    from backend.notifications.message_builder import build_exit_msg
    txt = build_exit_msg(bot_name="b", demo=True, symbol="BTC-USDT", direction="long", exit_reason="SL", entry_price=100,
                         exit_price=100.5, pnl=-0.3, gross_pnl=0.5, fees=0.8, daily_pnl=-1, stop_loss_usd=-50, wins=1, losses=2)
    assert "P&L líquido" in txt and "-0.30" in txt and "Bruto +$0.50" in txt and "taxas -$0.80" in txt
    assert "🔴" in txt                                    # +0,50 de preço com 0,80 de taxas é prejuízo
