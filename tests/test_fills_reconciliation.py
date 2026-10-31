"""Conciliação com a tela oficial da OKX (Order center > Trading history, BTC/USDT de 30/10/2026): os números abaixo
foram copiados dela. Compra: taxa em BTC. Venda: taxa em USDT."""
import pytest

from backend.fills import fee_in_usd, reconcile_fills, summarize


def fill(i, ts, side, px, qty, fee, ccy, sym="BTC-USDT"):
    return {"id": str(i), "transaction_time": ts, "side": side, "price": str(px), "qty": str(qty), "fee": str(fee),
            "fee_currency": ccy, "symbol": sym}


BUY_1 = fill(1, "2026-10-30T14:13:59.000Z", "buy", 76845.9, 0.00122284, -0.00000490, "BTC")
SELL_1 = fill(2, "2026-10-30T17:53:44.000Z", "sell", 77101.6, 0.00121794, -0.37562050, "USDT")
BUY_2 = fill(3, "2026-10-30T20:00:34.000Z", "buy", 76873.6, 0.00128876, -0.00000516, "BTC")


def test_buy_fee_in_btc_is_converted_to_dollars_not_summed_as_dollars():
    usd, known = fee_in_usd(BUY_2)
    assert known and usd == pytest.approx(0.00000516 * 76873.6)             # ≈ US$ 0,40, não US$ 0,000005
    assert fee_in_usd(SELL_1) == (pytest.approx(0.3756205), True)           # venda: já está em USDT
    assert fee_in_usd(fill(9, "t", "buy", 1, 1, -0.5, "OKB")) == (0.0, False)  # moeda sem conversão confiável


def test_round_trip_matches_the_okx_cash_balance():
    rows = reconcile_fills([BUY_2, SELL_1, BUY_1])                          # fora de ordem de propósito
    buy1, sell1, buy2 = rows
    # caixa da OKX: comprou pagando 93,97024035 USDT; vendeu recebendo 93,5295 USDT (já sem a taxa)
    assert buy1["quote_flow"] == pytest.approx(-93.97024035, abs=1e-6)
    assert sell1["quote_flow"] == pytest.approx(93.5295, abs=1e-4)
    # a taxa da compra saiu do saldo em BTC: 0,00122284 − 0,0000049 = 0,00121794 (a quantidade que foi vendida)
    assert buy1["base_flow"] == pytest.approx(0.00121794, abs=1e-9)
    assert sell1["unmatched_qty"] == 0
    # P&L: bruto só de preço = +0,3114; líquido = 93,5295 − 93,9702 = −0,4407; as taxas das duas pontas explicam a diferença
    assert sell1["gross_pnl"] == pytest.approx(0.3114, abs=1e-3)
    assert sell1["net_pnl"] == pytest.approx(-0.4407, abs=1e-3)
    assert sell1["pnl_fees_usd"] == pytest.approx(0.3766 + 0.3756, abs=2e-3)
    # a última compra segue aberta: sem P&L realizado
    assert buy2["gross_pnl"] is None and buy2["net_pnl"] is None


def test_effective_fee_rate_is_visible():
    rows = reconcile_fills([BUY_1, SELL_1, BUY_2])
    assert all(r["fee_pct"] == pytest.approx(0.4, abs=0.01) for r in rows)   # a OKX cobrou 0,40% por lado nestes fills
    s = summarize(rows)
    assert s["effective_fee_pct"] == pytest.approx(0.4, abs=0.01)
    assert s["closed_sells"] == 1 and s["unmatched_sells"] == 0
    assert s["net"] == pytest.approx(-0.4407, abs=1e-3) and s["gross"] == pytest.approx(0.3114, abs=1e-3)


def test_sell_without_a_matching_buy_is_flagged_not_priced():
    """DOT de 29/10: vendas de saldo que já existia antes do app (sem compra correspondente) não têm P&L inventado."""
    rows = reconcile_fills([fill(1, "2026-10-29T18:31:44.000Z", "sell", 0.979, 25.499032, -0.09985421, "USDT", "DOT-USDT")])
    r = rows[0]
    assert r["gross_pnl"] is None and r["net_pnl"] is None and r["unmatched_qty"] == pytest.approx(25.499032)
    assert r["quote_flow"] == pytest.approx(25.499032 * 0.979 - 0.09985421)
    assert summarize(rows)["unmatched_sells"] == 1


def test_partial_sell_consumes_lots_fifo_and_keeps_the_rest():
    buys = [fill(1, "2026-10-30T10:00:00.000Z", "buy", 100, 1.0, -0.4, "USDT"),
            fill(2, "2026-10-30T11:00:00.000Z", "buy", 110, 1.0, -0.44, "USDT")]
    sell = fill(3, "2026-10-30T12:00:00.000Z", "sell", 120, 1.5, -0.72, "USDT")
    r = reconcile_fills(buys + [sell])[2]
    # lote 1 inteiro (custo 100,40) + metade do lote 2 (custo 110,44 / 2 = 55,22); recebe 180 − 0,72 = 179,28
    assert r["net_pnl"] == pytest.approx(179.28 - (100.4 + 55.22), abs=1e-6)
    assert r["gross_pnl"] == pytest.approx((120 - 100) * 1.0 + (120 - 110) * 0.5)
