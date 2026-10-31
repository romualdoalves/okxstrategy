"""Duas taxas por lado, sempre lado a lado: REFERÊNCIA (conta real: avalia estudos, Scanner e meta) e COBRADA (o que a conta
conectada paga agora: dinheiro da conta). Na conta demo os fills cobram 0,40%, 4x a referência de 0,10%."""
import pytest

from backend import fee_model, goal
from backend.fills import reconcile_fills
from backend.profit_lock import breakeven_stop, fee_rate as lock_fee_rate


def _fill(i, side, px, qty, fee, ccy):
    return {"id": str(i), "transaction_time": f"2026-10-30T1{i}:00:00.000Z", "side": side, "price": str(px), "qty": str(qty),
            "fee": str(fee), "fee_currency": ccy, "symbol": "BTC-USDT", "exec_type": "Taker"}


REAL = [_fill(1, "buy", 76845.9, 0.00122284, -0.0000049, "BTC"), _fill(2, "sell", 77101.6, 0.00121794, -0.3756205, "USDT"),
        _fill(3, "buy", 76873.6, 0.00128876, -0.00000516, "BTC"), _fill(4, "sell", 76900.0, 0.0012836, -0.39485, "USDT")]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("OKX_FEE_RATE", raising=False)
    monkeypatch.setenv("OKX_DEMO", "true")
    monkeypatch.setattr(fee_model, "_observed", None)
    monkeypatch.setattr(fee_model, "_official", None)


def test_reference_is_the_published_live_rate_and_drives_goal_and_lock():
    assert fee_model.fee_rate() == 0.001 and fee_model.source() == "reference"
    assert lock_fee_rate() == 0.001
    assert goal.net_equivalent_pct(1.0) == pytest.approx(1.0 - 0.001 * 201)        # +1% bruto ≈ +0,80% líquido (taxa real de referência)
    assert breakeven_stop(100.0) > 100.0 * 1.002


def test_charged_rate_is_what_the_demo_fills_show_and_both_are_exposed():
    assert fee_model.charged_rate() == 0.001                                      # nada medido: não inventa 0,40%
    rate, n = fee_model.observed_rate(reconcile_fills(REAL))
    assert n == 4 and rate == pytest.approx(0.004, abs=0.0001)
    fee_model.observe(rate, n)
    st = fee_model.status()
    assert st["fee_rate"] == 0.001 and st["charged_rate"] == pytest.approx(0.004, abs=0.0001)    # referência x cobrada
    assert st["demo"] is True and st["demo_premium"] == pytest.approx(0.003, abs=0.0001)         # o demo cobra 0,30 p.p. a mais


def test_on_a_live_account_the_reference_is_the_charged_rate(monkeypatch):
    monkeypatch.setenv("OKX_DEMO", "false")
    fee_model.set_official(0.001, 0.0008, "Lv1")
    assert fee_model.fee_rate() == 0.001 and fee_model.source() == "official"
    fee_model.observe(0.0012, 10)
    assert fee_model.fee_rate() == pytest.approx(0.0012) and fee_model.source() == "fills"      # o app acompanha a conta real


def test_env_overrides_the_reference(monkeypatch):
    monkeypatch.setenv("OKX_FEE_RATE", "0.0015")
    fee_model.observe(0.004, 10)
    assert fee_model.fee_rate() == 0.0015 and fee_model.source() == "env" and fee_model.charged_rate() == pytest.approx(0.004)


def test_few_fills_or_absurd_rates_do_not_move_the_charged_rate():
    assert fee_model.observed_rate(reconcile_fills(REAL[:2]))[0] is None
    fee_model.observe(0.003, 2)
    fee_model.observe(0.5, 50)
    assert fee_model.charged_rate() == 0.001


def test_maker_fills_do_not_pull_the_taker_rate_down():
    fills = [dict(f, exec_type="Maker", fee=str(float(f["fee"]) / 4)) for f in REAL] + REAL
    rate, n = fee_model.observed_rate(reconcile_fills(fills))
    assert n == 4 and rate == pytest.approx(0.004, abs=0.0001)


def test_official_rate_is_shown_and_a_divergence_from_the_fills_is_flagged():
    fee_model.observe(0.004, 10)
    fee_model.set_official(0.001, 0.0008, "Lv1")
    st = fee_model.status()
    assert st["official_taker"] == 0.001 and st["official_maker"] == 0.0008 and st["mismatch"] is True
    assert fee_model.charged_rate() == pytest.approx(0.004)                      # o dinheiro da conta segue o que ela pagou
    fee_model.set_official(0.004)
    assert fee_model.status()["mismatch"] is False


def test_feeRate_recorded_by_okx_in_each_fill_is_used_when_present():
    fills = [dict(f, fee_rate_api="-0.002") for f in REAL]
    rate, n = fee_model.observed_rate(reconcile_fills(fills))
    assert n == 4 and rate == pytest.approx(0.002)


def test_backtest_uses_the_reference_rate_and_the_worker_receives_it(monkeypatch):
    from backend.backtest_engine import BacktestEngine
    from tests.test_backtest_service import synthetic_candles
    cs = synthetic_candles(900, seed=11)
    cheap = BacktestEngine("TF003-1D").run_strict_sync(cs, fee_rate=0.001)
    dear = BacktestEngine("TF003-1D").run_strict_sync(cs, fee_rate=0.004)
    assert dear["total_profit"] < cheap["total_profit"]
    assert BacktestEngine("TF003-1D").run_strict_sync(cs)["total_profit"] == pytest.approx(cheap["total_profit"])   # padrão = referência
    monkeypatch.setenv("OKX_FEE_RATE", "0.004")
    assert BacktestEngine("TF003-1D").run_strict_sync(cs)["total_profit"] == pytest.approx(dear["total_profit"])


def test_daily_pnl_and_win_count_use_the_rate_the_account_pays():
    from types import SimpleNamespace
    from backend.bot.accounting import AccountingMixin
    fee_model.observe(0.004, 10)                                                  # a conta demo cobra 0,40% por lado
    bot = SimpleNamespace(config=SimpleNamespace(stake_usd=100.0, stop_loss_usd=-50.0, id=1), _daily_pnl=0.0, _wins=0, _losses=0,
                          _halted=False, _pnl_day=None, _roll_day=lambda: None)
    bot._estimated_fees = lambda: AccountingMixin._estimated_fees(bot)
    AccountingMixin._record_pnl(bot, 0.5)             # +0,50 de preço com taxas de 0,80: perdeu dinheiro
    assert bot._daily_pnl == pytest.approx(-0.30) and (bot._wins, bot._losses) == (0, 1)
    AccountingMixin._record_pnl(bot, 1.0, fee=0.2)
    assert bot._daily_pnl == pytest.approx(0.50) and (bot._wins, bot._losses) == (1, 1)


def test_exit_message_shows_net_first_then_gross_and_fees():
    from backend.notifications.message_builder import build_exit_msg
    txt = build_exit_msg(bot_name="b", demo=True, symbol="BTC-USDT", direction="long", exit_reason="SL", entry_price=100,
                         exit_price=100.5, pnl=-0.3, gross_pnl=0.5, fees=0.8, daily_pnl=-1, stop_loss_usd=-50, wins=1, losses=2)
    assert "P&L líquido" in txt and "-0.30" in txt and "Bruto +$0.50" in txt and "taxas -$0.80" in txt
    assert "🔴" in txt


def test_trade_fee_parser_handles_both_okx_formats():
    from backend.exchanges.okx import parse_trade_fee
    old = parse_trade_fee({"level": "Lv1", "taker": "-0.0010", "maker": "-0.0008"})
    assert old["taker"] == pytest.approx(0.001) and old["maker"] == pytest.approx(0.0008) and old["source_field"] == "taker/maker"
    new = parse_trade_fee({"level": "Lv1", "taker": "-0.0005", "maker": "-0.0002",
                           "feeGroup": [{"groupId": "1", "taker": "-0.0010", "maker": "-0.0008"}]})
    assert new["taker"] == pytest.approx(0.001) and new["source_field"] == "feeGroup" and not new["ambiguous"]
    multi = parse_trade_fee({"feeGroup": [{"groupId": "1", "taker": "-0.001", "maker": "-0.0008"},
                                          {"groupId": "2", "taker": "-0.004", "maker": "-0.004"}]})
    assert multi["taker"] == pytest.approx(0.004) and multi["ambiguous"] is True
    assert "taker" not in parse_trade_fee({"level": "Lv1"})
