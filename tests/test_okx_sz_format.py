"""A OKX recusa `sz` com casas além do lotSz ou em notação científica (51000 Parameter sz error)."""
from backend.exchanges import okx


def _lot(monkeypatch, lot, state="live"):
    monkeypatch.setitem(okx._INST_CACHE, "BTC-USDT", {"instType": "SPOT", "ctVal": 1.0, "lotSz": lot, "minSz": 1e-5, "state": state})


def test_floors_to_lot_and_never_scientific(monkeypatch):
    _lot(monkeypatch, 1e-8)
    assert okx._fmt_sz("BTC-USDT", 0.00035999645320000003) == "0.00035999"
    assert okx._fmt_sz("BTC-USDT", 1.2e-05) == "0.000012"
    assert okx._fmt_sz("BTC-USDT", 0.0004) == "0.0004"


def test_coarser_lot(monkeypatch):
    _lot(monkeypatch, 0.0001)
    assert okx._fmt_sz("BTC-USDT", 0.00039999) == "0.0003"


def test_unknown_instrument_uses_eight_decimals_not_default_lot(monkeypatch):
    _lot(monkeypatch, 1.0, state="")  # defaults de falha de rede: lotSz 1.0 não vale
    assert okx._fmt_sz("BTC-USDT", 0.123456789) == "0.12345678"
