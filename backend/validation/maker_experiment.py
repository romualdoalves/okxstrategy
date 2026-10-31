"""
maker_experiment.py — Quanto a ordem a LIMITE (maker) ajudaria de verdade? Experimento só no backtest; o bot não muda.

Parte das operações do backtest estrito (taker: entra no fechamento do candle do sinal, sai por stop/trailing/trava a mercado ou
por sinal no fechamento) e refaz cada uma como se a entrada (e, no cenário B, a saída por sinal) fosse uma ordem a limite:

  ENTRADA: limite de compra no fechamento do candle do sinal (menos `offset_pct`). A ordem vive UM candle (o seguinte). Só conta como
    preenchida se a MÍNIMA desse candle for ESTRITAMENTE menor que o preço menos `buffer_pct` (nunca "tocou na ponta da fila").
    Preenchida: entra no preço do limite, sem slippage, com taxa maker; o resto da operação (stop, alvo, trailing) é o do backtest.
    Não preenchida: a operação NÃO existe. É o custo de oportunidade: o que ela teria rendido no backtest taker.
  SAÍDA POR SINAL (cenário B): limite de venda no fechamento do candle do sinal. Preenche só se a MÁXIMA do candle seguinte for
    estritamente maior que o preço mais o buffer, e antes disso o stop não foi tocado. Senão vende a mercado (taxa taker, slippage)
    no fechamento do candle seguinte, ou no stop se ele foi tocado. Saídas por stop/trailing/trava continuam a mercado.

Limitações assumidas, todas CONTRA a ordem a limite ou neutras: ordem de 1 candle só; sem reposição; a ordem não altera o preço; o
efeito de "ficar de fora" sobre sinais posteriores (a posição não ocupa o ativo) não é simulado, só o resultado de cada operação.
"""
from __future__ import annotations

from typing import Iterable

TAKER_FEE = 0.004
MAKER_FEE = 0.001
SLIPPAGE = 0.0005


def _stats(trades: list[dict]) -> dict:
    wins = sum(t["profit"] for t in trades if t["profit"] > 0)
    loss = -sum(t["profit"] for t in trades if t["profit"] <= 0)
    return {"trades": len(trades), "profit": round(sum(t["profit"] for t in trades), 2),
            "pf": round(wins / loss, 2) if loss > 0 else (round(wins, 2) if wins else 0.0)}


def simulate_maker(closed_trades: list[dict], candles: list, *, maker_exit: bool = False, buffer_pct: float = 0.05,
                   offset_pct: float = 0.0, maker_fee: float = MAKER_FEE, taker_fee: float = TAKER_FEE,
                   slippage: float = SLIPPAGE, stake: float = 100.0) -> dict:
    """Refaz as operações do backtest taker com entrada (e opcionalmente saída por sinal) a limite. Devolve os resultados e o custo de
    oportunidade. `buffer_pct`/`offset_pct` em % (0,05 = 0,05%)."""
    buf, off = buffer_pct / 100.0, offset_pct / 100.0
    kept, missed = [], []
    exit_fills = exit_fallbacks = 0
    for t in closed_trades:
        i, j = t["entry_time"], t["exit_time"]
        signal_close = t["entry_price"] / (1 + slippage)                    # o backtest taker entrou no fechamento + slippage
        base_profit = t["profit"]
        limit = signal_close * (1 - off)
        nxt = candles[i + 1] if i + 1 < len(candles) else None
        if nxt is None or not float(nxt.low) < limit * (1 - buf):          # estritamente abaixo: não basta tocar o preço
            missed.append({**t, "base_profit": base_profit})
            continue
        qty = stake / limit
        exit_px, exit_fee = t["exit_price"], taker_fee
        if maker_exit and t.get("exit_type") == "signal" and j + 1 < len(candles):
            sell_limit = t["exit_price"] / (1 - slippage)                   # fechamento do candle do sinal de saída
            after = candles[j + 1]
            stop = t.get("stop_price") or 0.0
            if stop and float(after.low) <= stop:                           # o stop veio antes: sai no stop, a mercado
                exit_px, exit_fee = stop * (1 - slippage), taker_fee
                exit_fallbacks += 1
            elif float(after.high) > sell_limit * (1 + buf):
                exit_px, exit_fee = sell_limit, maker_fee
                exit_fills += 1
            else:                                                           # não preencheu: vende a mercado no fechamento seguinte
                exit_px, exit_fee = float(after.close) * (1 - slippage), taker_fee
                exit_fallbacks += 1
        fees = qty * limit * maker_fee + qty * exit_px * exit_fee
        profit = qty * (exit_px - limit) - fees
        kept.append({**t, "profit": profit, "entry_price": limit, "exit_price": exit_px, "base_profit": base_profit})

    base = _stats([{"profit": t["profit"]} for t in closed_trades])
    maker = _stats(kept)
    placed = len(closed_trades)
    missed_win = sum(t["base_profit"] for t in missed if t["base_profit"] > 0)
    missed_loss = sum(t["base_profit"] for t in missed if t["base_profit"] <= 0)
    same_trades_base = sum(t["base_profit"] for t in kept)                  # as MESMAS operações executadas, no backtest taker
    return {
        "orders": placed, "filled": len(kept), "fill_rate_pct": round(100 * len(kept) / placed, 1) if placed else None,
        "baseline": base, "maker": maker, "delta_profit": round(maker["profit"] - base["profit"], 2),
        "fee_and_slippage_saved_on_filled": round(maker["profit"] - same_trades_base, 2),   # ganho nas operações executadas
        "missed_winners_profit": round(missed_win, 2),                       # lucro que ficou de fora
        "missed_losers_avoided": round(-missed_loss, 2),                     # prejuízo que se evitou
        "opportunity_net": round(missed_win + missed_loss, 2),               # o que as operações perdidas teriam rendido (líquido)
        "maker_exit": maker_exit, "exit_limit_fills": exit_fills, "exit_fallbacks": exit_fallbacks,
        "verdict": "maker compensa" if maker["profit"] > base["profit"] else "maker descartado",
    }
