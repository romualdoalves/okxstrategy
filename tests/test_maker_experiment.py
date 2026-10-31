"""Experimento de ordem a limite (só backtest): preenchimento conservador e custo de oportunidade."""
from types import SimpleNamespace as C

import pytest

from backend.validation.maker_experiment import MAKER_FEE, SLIPPAGE, TAKER_FEE, simulate_maker


def bars(spec):
    return [C(open=o, high=h, low=l, close=c) for o, h, l, c in spec]


def trade(entry_idx, exit_idx, signal_close, exit_px, exit_type="stop", stop=None):
    entry = signal_close * (1 + SLIPPAGE)
    qty = 100 / entry
    fees = (qty * entry + qty * exit_px) * TAKER_FEE
    return {"entry_time": entry_idx, "exit_time": exit_idx, "entry_price": entry, "exit_price": exit_px,
            "profit": qty * (exit_px - entry) - fees, "exit_type": exit_type, "stop_price": stop}


def test_touching_the_limit_price_is_not_a_fill_and_the_missed_winner_is_counted():
    cs = bars([(100, 101, 99, 100), (100, 103, 100.0, 103)] + [(103, 104, 102, 103)] * 5)     # mínima do candle seguinte = o limite
    t = trade(0, 3, 100.0, 110.0, "trailing")
    r = simulate_maker([t], cs, buffer_pct=0.0)
    assert r["filled"] == 0 and r["fill_rate_pct"] == 0.0
    assert r["missed_winners_profit"] == pytest.approx(t["profit"], abs=0.01) and r["maker"]["trades"] == 0
    assert r["delta_profit"] == pytest.approx(-t["profit"], abs=0.01) and r["verdict"] == "maker descartado"


def test_a_fill_below_the_limit_saves_fees_and_slippage_on_the_entry():
    cs = bars([(100, 101, 99, 100), (100, 103, 99.5, 103)] + [(103, 104, 102, 103)] * 5)      # mínima 99,5 < 100 × (1 − 0,05%)
    t = trade(0, 3, 100.0, 110.0, "trailing")
    r = simulate_maker([t], cs, buffer_pct=0.05)
    assert r["filled"] == 1 and r["fill_rate_pct"] == 100.0
    assert r["maker"]["profit"] > r["baseline"]["profit"]                      # entra sem slippage e paga 0,1% em vez de 0,4%
    assert r["fee_and_slippage_saved_on_filled"] > 0 and r["opportunity_net"] == 0


def test_missing_a_losing_trade_is_an_avoided_loss_not_a_cost():
    cs = bars([(100, 101, 99, 100), (100, 103, 100.0, 103)] + [(103, 104, 102, 103)] * 5)
    t = trade(0, 3, 100.0, 95.0, "stop")
    r = simulate_maker([t], cs, buffer_pct=0.0)
    assert r["missed_losers_avoided"] == pytest.approx(-t["profit"], abs=0.01) and r["missed_winners_profit"] == 0
    assert r["delta_profit"] > 0


def test_maker_exit_falls_back_to_the_market_when_the_limit_is_not_reached_or_the_stop_hits_first():
    base = [(100, 101, 99, 100), (100, 103, 99.5, 103)] + [(103, 104, 102, 103)] * 2        # candles 0..3; o 4 é o seguinte à saída
    # saída por sinal no candle 3 (fecha 103): o candle 4 não passa de 103 × 1,0005 -> vende a mercado no fechamento
    cs = bars(base + [(103, 103.0, 102, 102.5)] + [(102, 103, 101, 102)] * 3)
    t = trade(0, 3, 100.0, 103 * (1 - SLIPPAGE), "signal", stop=95.0)
    r = simulate_maker([t], cs, maker_exit=True, buffer_pct=0.05)
    assert r["exit_limit_fills"] == 0 and r["exit_fallbacks"] == 1
    # candle seguinte sobe acima do limite de venda: preenche como maker
    cs2 = bars(base + [(103, 104, 102, 103.5)] + [(102, 103, 101, 102)] * 3)
    r2 = simulate_maker([t], cs2, maker_exit=True, buffer_pct=0.05)
    assert r2["exit_limit_fills"] == 1 and r2["maker"]["profit"] > r["maker"]["profit"]
    # o stop é tocado no candle seguinte: sai no stop, a mercado
    cs3 = bars(base + [(103, 104, 94, 100)] + [(102, 103, 101, 102)] * 3)
    r3 = simulate_maker([t], cs3, maker_exit=True, buffer_pct=0.05)
    assert r3["exit_fallbacks"] == 1 and r3["maker"]["profit"] < r2["maker"]["profit"]
