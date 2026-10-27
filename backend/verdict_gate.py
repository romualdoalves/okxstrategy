"""
verdict_gate.py — Freio de robustez para o veredito "INICIAR" do Scanner.

O estudo com dados reais da OKX (seleção no passado, desempenho nos 500 candles seguintes) mostrou que
um score alto num período curto não prevê lucro futuro: com poucos trades, o melhor entre ~40
estratégias é, em boa parte, sorte. Por isso "INICIAR" só vale quando a amostra sustenta a conclusão:
  - pelo menos MIN_TRADES_START trades no período; e
  - fora da amostra (últimos 30% do histórico, nunca usados na escolha) positivo, com trades suficientes.
Se falhar, o veredito é limitado a "CUIDADO" (o score não é alterado além do teto).
"""
from __future__ import annotations

from typing import Optional

from .backtest_stats import MIN_OOS_TRADES, MIN_TRADES_START

SCORE_CAP_WITHOUT_ROBUSTNESS = 6.66   # logo abaixo do limiar de INICIAR (6,67)


def is_suitable_to_start(result: dict) -> bool:
    """
    Estratégia "adequada para iniciar": veredito INICIAR **e** robustez aprovada (>= 30 trades e fora da
    amostra positivo). Sem o bloco de robustez (backtest clássico) nunca é considerada adequada.
    """
    rec = result.get("recommendation") or {}
    gate = rec.get("robust_gate") or {}
    return rec.get("verdict") == "INICIAR" and bool(gate.get("applied")) and bool(gate.get("passed"))


def robustness_gate(robust: Optional[dict]) -> dict:
    """Retorna {"applied": bool, "passed": bool, "failed": [motivos]}. Sem bloco `robust`, não se aplica."""
    if not robust or not robust.get("all"):
        return {"applied": False, "passed": True, "failed": []}

    failed: list[str] = []
    n = robust["all"]["trades"]
    if n < MIN_TRADES_START:
        failed.append(f"apenas {n} trades no período (mínimo de {MIN_TRADES_START} para confiar no resultado)")

    oos = robust.get("out_of_sample") or {}
    if not robust.get("oos_conclusive"):
        failed.append(f"fora da amostra inconclusivo ({oos.get('trades', 0)} trades; mínimo de {MIN_OOS_TRADES})")
    elif not robust.get("oos_positive"):
        failed.append(
            f"fora da amostra negativo ou sem vantagem (lucro ${oos.get('profit', 0):.2f}, "
            f"profit factor {oos.get('profit_factor', 0):.2f})"
        )
    return {"applied": True, "passed": not failed, "failed": failed}
