"""
backtest_stats.py — Estatística de robustez para resultados de backtest.

O score histórico antigo olhava o profit factor "pontual" de uma única janela. Com ~10 trades e
dezenas de estratégias testadas por ativo, escolher o melhor score é escolher, em boa parte, sorte.
Aqui calculamos:
  - separação dentro/fora da amostra (IS/OOS) por tempo: as estratégias não têm parâmetros
    ajustados no scan, então uma única passada particionada pelo candle de entrada equivale a
    avaliar cada trecho separadamente;
  - bootstrap dos P&L dos trades: o 5º percentil do profit factor penaliza amostras pequenas;
  - checagens mínimas (nº de trades e OOS positivo) que limitam o veredito "INICIAR".
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

PF_CAP = 10.0               # profit factor sem perdas é limitado, para não dominar médias
BOOTSTRAP_RESAMPLES = 2000
OOS_FRACTION = 0.30
MIN_TRADES_START = 30       # trades totais mínimos para o veredito INICIAR
MIN_OOS_TRADES = 5          # trades OOS mínimos para a validação fora da amostra valer


def profit_factor(profits: Sequence[float]) -> float:
    gross_profit = sum(p for p in profits if p > 0)
    gross_loss = abs(sum(p for p in profits if p <= 0))
    if gross_loss > 0:
        return min(gross_profit / gross_loss, PF_CAP)
    return PF_CAP if gross_profit > 0 else 0.0


def summarize(profits: Sequence[float]) -> dict:
    n = len(profits)
    wins = sum(1 for p in profits if p > 0)
    return {
        "trades": n,
        "wins": wins,
        "win_rate": round(wins / n * 100, 2) if n else 0.0,
        "profit": round(float(sum(profits)), 2),
        "profit_factor": round(profit_factor(profits), 3),
    }


def bootstrap_profit_factor(profits: Sequence[float], resamples: int = BOOTSTRAP_RESAMPLES, seed: int = 7) -> dict:
    """Reamostra os trades com reposição e devolve a distribuição do profit factor."""
    arr = np.asarray(profits, dtype=float)
    if arr.size < 2:
        return {"pf_p5": 0.0, "pf_median": 0.0, "prob_pf_gt_1": 0.0, "resamples": 0}
    rng = np.random.default_rng(seed)
    sample = rng.choice(arr, size=(resamples, arr.size), replace=True)
    gross_profit = np.where(sample > 0, sample, 0.0).sum(axis=1)
    gross_loss = np.abs(np.where(sample <= 0, sample, 0.0).sum(axis=1))
    with np.errstate(divide="ignore", invalid="ignore"):
        pf = np.where(gross_loss > 0, gross_profit / gross_loss, np.where(gross_profit > 0, PF_CAP, 0.0))
    pf = np.minimum(pf, PF_CAP)
    return {
        "pf_p5": round(float(np.percentile(pf, 5)), 3),
        "pf_median": round(float(np.percentile(pf, 50)), 3),
        "prob_pf_gt_1": round(float((pf > 1.0).mean()), 3),
        "resamples": resamples,
    }


def robust_summary(closed_trades: Sequence[dict], total_bars: int, warmup: int,
                   oos_fraction: float = OOS_FRACTION) -> dict:
    """
    Particiona os trades fechados pelo candle de ENTRADA (`entry_time`, índice no array de candles):
    antes do ponto de corte = dentro da amostra (IS), depois = fora da amostra (OOS).
    """
    split_bar = warmup + int((total_bars - warmup) * (1.0 - oos_fraction))
    all_profits = [float(t["profit"]) for t in closed_trades]
    is_profits = [float(t["profit"]) for t in closed_trades if t["entry_time"] < split_bar]
    oos_profits = [float(t["profit"]) for t in closed_trades if t["entry_time"] >= split_bar]

    boot = bootstrap_profit_factor(all_profits)
    oos = summarize(oos_profits)
    oos_conclusive = oos["trades"] >= MIN_OOS_TRADES
    return {
        "oos_fraction": oos_fraction,
        "split_bar": split_bar,
        "bars_evaluated": max(0, total_bars - warmup),
        "all": summarize(all_profits),
        "in_sample": summarize(is_profits),
        "out_of_sample": oos,
        "bootstrap": boot,
        "min_trades_met": len(all_profits) >= MIN_TRADES_START,
        "oos_conclusive": oos_conclusive,
        "oos_positive": bool(oos_conclusive and oos["profit"] > 0 and oos["profit_factor"] > 1.0),
    }
