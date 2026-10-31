"""
fills.py — Conciliação dos fills da OKX (spot), em dólares, igual à tela oficial "Order center > Trading history".

Dois fatos da OKX spot que o app ignorava:
  * a taxa de uma COMPRA é cobrada na moeda base (ex.: -0,00000516 BTC), a de uma VENDA na moeda de cotação
    (ex.: -0,3756 USDT). Somar `fee` cru como se fosse dólar trata 0,00000516 BTC como US$ 0,000005 (na verdade ≈ US$ 0,40);
  * por isso a quantidade vendida depois é menor do que a comprada (a taxa saiu do saldo em moeda base).

Aqui cada fill vira dinheiro: taxa em US$, fluxo de caixa líquido e P&L FIFO (lotes de compra consumidos pelas vendas), de modo
que  P&L líquido = caixa recebido na venda − caixa gasto na compra  (o mesmo que o saldo da conta mostra).
Sem dependência de rede ou banco: função pura, testada com dados reais da tela da OKX.
"""
from __future__ import annotations

from collections import defaultdict, deque

QTY_EPS = 1e-12


def split_symbol(symbol: str) -> tuple[str, str]:
    parts = (symbol or "").upper().replace("/", "-").split("-")
    return (parts[0], parts[1]) if len(parts) >= 2 else (parts[0] if parts else "", "")


def _num(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def fee_in_usd(fill: dict) -> tuple[float, bool]:
    """(taxa em US$, conhecida?). Taxa na moeda de cotação vale como está; na moeda base, vezes o preço do fill.
    Qualquer outra moeda (ex.: OKB) não tem conversão confiável: devolve (0, False) para a tela avisar."""
    raw = fill.get("fee")
    if raw in (None, ""):
        return 0.0, True
    fee = abs(_num(raw))
    if fee == 0:
        return 0.0, True
    base, quote = split_symbol(fill.get("symbol", ""))
    ccy = (fill.get("fee_ccy") or fill.get("fee_currency") or "").upper()
    if ccy in ("", quote):
        return fee, True
    if ccy == base:
        return fee * _num(fill.get("price")), True
    return 0.0, False


def reconcile_fills(fills: list[dict]) -> list[dict]:
    """Enriquece os fills (em ordem cronológica) com taxa em US$, caixa e P&L FIFO. Não altera a entrada.

    Campos acrescentados: price, qty, notional, fee_usd, fee_ccy, fee_pct (taxa efetiva sobre o valor negociado),
    fee_known, base_flow / quote_flow (variação de saldo, com sinal, igual ao "Balance change" da OKX),
    gross_pnl (só preço), net_pnl (caixa), pnl_fees_usd (taxas das duas pontas na parte fechada), unmatched_qty
    (parte vendida sem compra correspondente: saldo que já existia antes do app)."""
    ordered = sorted(fills, key=lambda f: (f.get("transaction_time") or "", f.get("id") or ""))
    lots: dict[str, deque] = defaultdict(deque)
    out = []
    for f in ordered:
        sym = (f.get("symbol") or "").upper()
        side = (f.get("side") or "").lower()
        price, qty = _num(f.get("price")), _num(f.get("qty"))
        notional = price * qty
        fee_usd, known = fee_in_usd(f)
        base, quote = split_symbol(sym)
        ccy = (f.get("fee_ccy") or f.get("fee_currency") or "").upper()
        fee_native = abs(_num(f.get("fee")))
        row = {
            **f, "price": price, "qty": qty, "notional": notional, "fee_usd": fee_usd, "fee_known": known,
            "fee_ccy": ccy, "fee_pct": (fee_usd / notional * 100) if notional > 0 and known else None,
            "gross_pnl": None, "net_pnl": None, "pnl_fees_usd": None, "unmatched_qty": 0.0,
        }
        if side == "buy":
            base_in = qty - (fee_native if ccy == base else 0.0)
            cost = notional + (fee_native if ccy in ("", quote) else 0.0)
            row["base_flow"], row["quote_flow"] = base_in, -cost
            if base_in > QTY_EPS:
                lots[sym].append({"qty": base_in, "cost": cost, "price": price})
        elif side == "sell":
            proceeds = notional - fee_usd
            row["base_flow"], row["quote_flow"] = -qty, proceeds
            left, gross, net, matched_total = qty, 0.0, 0.0, 0.0
            while left > QTY_EPS and lots[sym]:
                lot = lots[sym][0]
                m = min(left, lot["qty"])
                cost_part = lot["cost"] * m / lot["qty"]
                gross += (price - lot["price"]) * m
                net += proceeds * m / qty - cost_part
                matched_total += m
                lot["qty"] -= m
                lot["cost"] -= cost_part
                left -= m
                if lot["qty"] <= QTY_EPS:
                    lots[sym].popleft()
            if matched_total > QTY_EPS:
                row["gross_pnl"], row["net_pnl"] = round(gross, 6), round(net, 6)
                row["pnl_fees_usd"] = round(gross - net, 6)
            row["unmatched_qty"] = max(0.0, left)
        else:
            row["base_flow"] = row["quote_flow"] = 0.0
        out.append(row)
    return out


def summarize(rows: list[dict]) -> dict:
    """Totais de uma lista de fills já reconciliados (normalmente os do dia exibido)."""
    sells = [r for r in rows if r["gross_pnl"] is not None]
    notional = sum(r["notional"] for r in rows if r["fee_known"])
    fees = sum(r["fee_usd"] for r in rows)
    return {
        "gross": round(sum(r["gross_pnl"] for r in sells), 4),
        "net": round(sum(r["net_pnl"] for r in sells), 4),
        "closed_fees": round(sum(r["pnl_fees_usd"] for r in sells), 4),     # taxas das duas pontas das operações fechadas
        "fees_total": round(fees, 4),                                       # todas as taxas dos fills do dia
        "notional": round(notional, 2),
        "effective_fee_pct": round(fees / notional * 100, 4) if notional > 0 else None,
        "fills": len(rows),
        "closed_sells": len(sells),
        "unmatched_sells": sum(1 for r in rows if (r.get("side") or "").lower() == "sell" and r["unmatched_qty"] > QTY_EPS),
        "unknown_fee_fills": sum(1 for r in rows if not r["fee_known"]),
    }
