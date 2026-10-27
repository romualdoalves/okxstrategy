"""
verdict_gate.py — Freio de robustez para o veredito "INICIAR" do Scanner.

O estudo com dados reais da OKX (seleção no passado, desempenho nos 500 candles seguintes) mostrou que
um score alto num período curto não prevê lucro futuro: com poucos trades, o melhor entre ~40
estratégias é, em boa parte, sorte. Por isso "INICIAR" só vale quando a amostra sustenta a conclusão:
  - pelo menos MIN_TRADES_START trades no período; e
  - fora da amostra (últimos 30% do histórico, nunca usados na escolha) positivo, com trades suficientes.
Se falhar, o veredito é limitado a "CUIDADO" (o score não é alterado além do teto).

O mínimo de trades depende do timeframe: uma estratégia de 1D faz poucos trades por ano (a TF008 fez 19 a
28 por ativo em 8 anos), então exigir 30 a barraria por construção, enquanto as de timeframe curto, que
acumulam centenas de trades, continuariam passando. Para timeframes mais altos o mínimo cai, mas em troca
exige-se mais evidência estatística (chance de profit factor > 1 no bootstrap), porque menos trades
significam um resultado mais ruidoso.
"""
from __future__ import annotations

from typing import Optional

from .backtest_stats import MIN_OOS_TRADES, MIN_TRADES_START

SCORE_CAP_WITHOUT_ROBUSTNESS = 6.66   # logo abaixo do limiar de INICIAR (6,67)

# Trades mínimos por timeframe (chave em minúsculas). Padrão = MIN_TRADES_START (30).
MIN_TRADES_BY_TIMEFRAME = {
    "1m": 30, "3m": 30, "5m": 30, "15m": 30, "30m": 30,
    "1h": 30, "2h": 30,
    "4h": 20, "6h": 20, "12h": 20,
    "1d": 15, "1w": 15,
}
# Com menos de MIN_TRADES_START trades exigidos, o bootstrap precisa dar esta chance mínima de PF > 1.
LOW_SAMPLE_MIN_PROB_PF_GT_1 = 0.90


def min_trades_for(timeframe: Optional[str]) -> int:
    """Mínimo de trades para o timeframe informado (padrão 30 se desconhecido)."""
    return MIN_TRADES_BY_TIMEFRAME.get((timeframe or "").strip().lower(), MIN_TRADES_START)


def is_suitable_to_start(result: dict) -> bool:
    """
    Estratégia "adequada para iniciar": veredito INICIAR **e** robustez aprovada (>= 30 trades e fora da
    amostra positivo). Sem o bloco de robustez (backtest clássico) nunca é considerada adequada.
    """
    rec = result.get("recommendation") or {}
    gate = rec.get("robust_gate") or {}
    return rec.get("verdict") == "INICIAR" and bool(gate.get("applied")) and bool(gate.get("passed"))


def robustness_gate(robust: Optional[dict], timeframe: Optional[str] = None) -> dict:
    """
    Retorna {"applied", "passed", "failed": [motivos], "min_trades", "timeframe"}.
    Sem bloco `robust`, não se aplica.
    """
    min_n = min_trades_for(timeframe)
    if not robust or not robust.get("all"):
        return {"applied": False, "passed": True, "failed": [], "min_trades": min_n, "timeframe": timeframe}

    failed: list[str] = []
    n = robust["all"]["trades"]
    if n < min_n:
        failed.append(f"apenas {n} trades no período (mínimo de {min_n} para este timeframe)")
    elif min_n < MIN_TRADES_START:
        prob = float((robust.get("bootstrap") or {}).get("prob_pf_gt_1", 0.0))
        if prob < LOW_SAMPLE_MIN_PROB_PF_GT_1:
            failed.append(
                f"com poucos trades por timeframe alto, a chance de profit factor > 1 é {prob * 100:.0f}% "
                f"(mínimo de {LOW_SAMPLE_MIN_PROB_PF_GT_1 * 100:.0f}%)"
            )

    oos = robust.get("out_of_sample") or {}
    if not robust.get("oos_conclusive"):
        failed.append(f"fora da amostra inconclusivo ({oos.get('trades', 0)} trades; mínimo de {MIN_OOS_TRADES})")
    elif not robust.get("oos_positive"):
        failed.append(
            f"fora da amostra negativo ou sem vantagem (lucro ${oos.get('profit', 0):.2f}, "
            f"profit factor {oos.get('profit_factor', 0):.2f})"
        )
    return {"applied": True, "passed": not failed, "failed": failed, "min_trades": min_n, "timeframe": timeframe}
