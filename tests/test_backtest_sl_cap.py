"""O backtest aplica o mesmo teto de stop inicial do bot (min(4 ATR, 5% do preço)), mantendo o risco-retorno."""
from types import SimpleNamespace

from backend.backtest_engine import BacktestEngine as E


def _r(sl, tp1):
    return SimpleNamespace(metadata={"sl_price": sl, "tp1_price": tp1})


def test_default_has_no_percent_cap_only_four_atr():
    sl, tp1, _ = E._risk_from_result(_r(90.0, 130.0), 100.0, 1, atr=3.0)  # stop 10% < 4 ATR (12%): intocado
    assert (sl, tp1) == (90.0, 130.0)


def test_cap_by_four_atr_keeps_rr():
    sl, tp1, _ = E._risk_from_result(_r(80.0, 140.0), 100.0, 1, atr=3.0)  # stop 20% > 12%: corta, RR 2 mantido
    assert round(sl, 6) == 88.0 and round(tp1, 6) == 124.0


def test_stop_inside_limits_is_untouched():
    sl, tp1, _ = E._risk_from_result(_r(97.0, 106.0), 100.0, 1, atr=1.5)
    assert (sl, tp1) == (97.0, 106.0)


def test_env_override_enables_percent_cap(monkeypatch):
    monkeypatch.setenv("OKX_MAX_SL_PCT", "0.05")
    sl, tp1, _ = E._risk_from_result(_r(90.0, 130.0), 100.0, 1, atr=3.0)
    assert round(sl, 6) == 95.0 and round(tp1, 6) == 115.0
