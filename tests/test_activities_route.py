"""/api/activities concilia os fills da OKX em dólares (taxa em BTC na compra, em USDT na venda), com os números reais da
tela do Order center, e compara bruto com bruto e líquido com líquido contra o banco do app."""
import asyncio
import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import BotModel, TradeModel
from backend.routers import trades as trades_router


def fill(i, ts, side, px, qty, fee, ccy):
    return {"id": str(i), "transaction_time": ts, "side": side, "price": str(px), "qty": str(qty), "fee": str(fee),
            "fee_currency": ccy, "symbol": "BTC-USDT", "order_id": f"o{i}", "exec_type": "Taker"}


FILLS = [
    fill(1, "2026-10-30T14:13:59.000Z", "buy", 76845.9, 0.00122284, -0.0000049, "BTC"),
    fill(2, "2026-10-30T17:53:44.000Z", "sell", 77101.6, 0.00121794, -0.3756205, "USDT"),
    fill(3, "2026-10-30T20:00:34.000Z", "buy", 76873.6, 0.00128876, -0.00000516, "BTC"),
]


class FakeOKX:
    calls = []

    async def get_activities(self, after=None, until=None, strict=False, **_):
        FakeOKX.calls.append((after, until, strict))
        return list(FILLS)

    async def get_account_summary(self):
        return {"equity": 10000.0, "last_equity": 10000.0, "cash": 10000.0}

    async def get_portfolio_history(self, *_a, **_k):
        return {}

    async def get_open_orders(self):
        return []


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (BotModel, TradeModel):
        model.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(trades_router, "build_exchange", lambda *a, **k: FakeOKX())
    session.add(BotModel(id=1, name="BTC bot", strategy_id="TF008", symbol="BTC-USDT"))
    # o app gravou a saída do dia 30 com P&L de preço e a taxa sincronizada em dólares (as duas pontas)
    session.add(TradeModel(id=1, bot_id=1, symbol="BTC-USDT", type="exit", direction="LONG", pnl=0.31, fee=0.7522,
                           timestamp=dt.datetime(2026, 10, 30, 17, 53, 44), closed_at=dt.datetime(2026, 10, 30, 17, 53, 44)))
    session.commit()
    yield session
    session.close()


def test_activities_reconciles_in_dollars_like_the_okx_order_center(db):
    out = asyncio.run(trades_router.get_activities(date="2026-10-30", db=db))
    rec = out["reconciliation"]
    assert FakeOKX.calls[-1][2] is True                                   # strict: erro da OKX nunca vira "zero fills"
    assert [f["side"] for f in out["fills"]] == ["buy", "sell", "buy"]      # mais recente primeiro
    assert out["fills"][0]["id"] == "3"
    # OKX: bruto +0,31 (preço), taxas das duas pontas ≈ 0,7522, líquido −0,44 (caixa)
    assert rec["exchange_realized_pl"] == pytest.approx(0.31, abs=0.01)
    assert rec["total_fees"] == pytest.approx(0.7522, abs=0.003)
    assert rec["net_pnl_after_fees"] == pytest.approx(-0.44, abs=0.01)
    # app: mesmo bruto e mesma taxa → líquido igual ao da OKX; nada de diferença
    assert rec["app_net_pnl"] == pytest.approx(0.31 - 0.7522, abs=0.01)
    assert abs(rec["discrepancy"]) < 0.01 and abs(rec["discrepancy_net"]) < 0.01
    # taxa efetiva da OKX (0,40% por lado) contra a que o app assume (0,10%): o descompasso aparece
    assert rec["effective_fee_pct"] == pytest.approx(0.4, abs=0.01) and rec["model_fee_pct"] == pytest.approx(0.1)
    assert rec["fills_count"] == 3 and rec["unmatched_sells"] == 0


def test_activities_flags_pending_app_fees_and_a_net_difference(db):
    t = db.get(TradeModel, 1)
    t.fee = None                                                           # taxa ainda não sincronizada
    db.commit()
    rec = asyncio.run(trades_router.get_activities(date="2026-10-30", db=db))["reconciliation"]
    assert rec["app_fees_pending"] == 1
    assert rec["discrepancy_net"] == pytest.approx(-0.44 - 0.31, abs=0.01)  # sem a taxa, o líquido do app fica otimista
