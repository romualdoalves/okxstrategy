"""Os testes antigos de custo (trava de lucro, meta, backtest) foram escritos com 0,10% por lado: fixam essa taxa para os
números continuarem exatos. O comportamento da taxa REAL (padrão 0,40%, medida nos fills) é testado em test_fee_model.py,
que remove esta variável."""
import pytest


@pytest.fixture(autouse=True)
def _fixed_fee_rate(monkeypatch):
    from backend import fee_model
    monkeypatch.setenv("OKX_FEE_RATE", "0.001")
    monkeypatch.setattr(fee_model, "_observed", None)
    yield
