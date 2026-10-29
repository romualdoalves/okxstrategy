"""O backtest aplica o mesmo teto de stop inicial do bot (min(4 ATR, 5% do preço)), mantendo o risco-retorno."""
from types import SimpleNamespace

from backend.backtest_engine import BacktestEngine as E


def _r(sl, tp1):
    return SimpleNamespace(metadata={"sl_price": sl, "tp1_price": tp1})


def test_wide_stop_is_capped_at_5_percent_and_rr_kept():
    sl, tp1, _ = E._risk_from_result(_r(90.0, 130.0), 100.0, 1, atr=3.0)  # stop 10%, ATR 3 -> teto 5% (4 ATR = 12%)
    assert round(sl, 6) == 95.0 and round(tp1, 6) == 115.0                # RR 3 mantido


def test_cap_by_four_atr_when_tighter_than_five_percent():
    sl, tp1, _ = E._risk_from_result(_r(90.0, 120.0), 100.0, 1, atr=1.5)  # teto min(6, 5) = 5
    assert round(sl, 6) == 95.0
    sl2, _, _ = E._risk_from_result(_r(90.0, 120.0), 1000.0 / 10, 1, atr=1.0)  # 4 ATR = 4 < 5
    assert round(sl2, 6) == 96.0


def test_stop_inside_limits_is_untouched():
    sl, tp1, _ = E._risk_from_result(_r(97.0, 106.0), 100.0, 1, atr=1.5)
    assert (sl, tp1) == (97.0, 106.0)


def test_env_override_disables_or_widens_the_percent_cap(monkeypatch):
    monkeypatch.setenv("OKX_MAX_SL_PCT", "0")           # só o limite de 4 ATR
    sl, _, _ = E._risk_from_result(_r(90.0, 130.0), 100.0, 1, atr=3.0)
    assert round(sl, 6) == 90.0                          # 10% < 4 ATR (12%)
    monkeypatch.setenv("OKX_MAX_SL_PCT", "0.08")
    sl, _, _ = E._risk_from_result(_r(90.0, 130.0), 100.0, 1, atr=3.0)
    assert round(sl, 6) == 92.0
