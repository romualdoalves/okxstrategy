"""
backtest_service.py — ÚNICO ponto que carrega dados, executa (motor estrito) e pontua um backtest.

Todos os botões de backtest do app passam por aqui: o Scanner (/api/backtest/category) e o backtest de um bot
(/api/bots/{id}/backtest). Assim há um só motor (BacktestEngine.run_strict_sync: taxas, slippage, SL, TP1/trailing e
contexto de múltiplos timeframes), um só carregador de candles e uma só regra de pontuação/veredito. Antes havia um motor
"clássico" sem custos nem stops e uma cópia da pontuação em JavaScript, que divergiam do que o bot executa.
Os estudos (validation/run.py) usam o mesmo motor diretamente em lote e não passam por aqui.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from .backtest_engine import backtest_recommendation
from .backtest_worker import run_strict_async
from .candle_store import history_target, load_candles
from .common import market_data_svc
from .database import SessionLocal
from .exchanges.factory import build_exchange, map_timeframe_for_history
from .predictive_scoring import PredictiveScoringService
from .strategies.registry import REGISTRY
from .verdict_gate import SCORE_CAP_WITHOUT_ROBUSTNESS, robustness_gate

log = logging.getLogger("backtest_service")

# Estratégias que dependem de contexto externo ao vivo (não reconstruível historicamente): não backtestáveis.
CONTEXT_FLAGS = (
    "needs_gex_context",
    "needs_graph_context",
    "needs_onchain_context",
    "needs_dex_context",
    "needs_market_players_context",
)

_predictive: Optional[PredictiveScoringService] = None


class BacktestUnavailable(Exception):
    """O backtest não pode rodar (sem dados, contexto externo, estratégia desconhecida). Carrega o motivo."""


def needs_external_context(strategy_cls) -> bool:
    return any(getattr(strategy_cls, flag, False) for flag in CONTEXT_FLAGS)


def verdict_from_score(score: float) -> tuple[str, str]:
    if score >= 6.67:
        return "INICIAR", "success"
    if score >= 3.33:
        return "CUIDADO", "warning"
    return "NÃO INICIAR", "danger"

def apply_study_verdict(strategy_id: str, rec: dict) -> dict:
    """O veredito do ESTUDO (walk-forward de anos) manda sobre o backtest recente: estratégia aposentada nunca é
    INICIAR no Scanner, senão ele recomenda o que a criação do bot depois recusa. O score é mantido (para consulta)."""
    from .validation.reports import study_summary
    study = study_summary(strategy_id)
    if study["study_verdict"] != "retired" or rec.get("verdict") in (None, "N/A"):
        return rec
    rec = dict(rec)
    rec["study_retired"] = True
    if rec.get("verdict") != "NÃO INICIAR":
        rec["verdict"], rec["level"] = "NÃO INICIAR", "danger"
    rec["reasons"] = [f"Aposentada pelo estudo: {study['study_reason']}"] + list(rec.get("reasons") or [])
    return rec


def compose_predictive_recommendation(result: dict, predictive: dict) -> dict:
    rec = dict(result.get("recommendation") or {})
    if rec.get("verdict") == "N/A":
        return rec

    historical_score = float(rec.get("score") or 0.0)
    predictive_score = float(predictive.get("predictive_score") or 0.0)
    trades_count = int(result.get("trades_count") or 0)
    coverage = result.get("coverage") or {}
    rejections = int(result.get("ignored_sell_signals") or 0)

    robustness_score = 0.0
    if trades_count >= 10:
        robustness_score = 10.0
    elif trades_count >= 5:
        robustness_score = 7.0
    elif trades_count >= 3:
        robustness_score = 5.0
    elif trades_count > 0:
        robustness_score = 2.5

    operational_score = 10.0 if coverage.get("level") == "full" else 6.0
    if result.get("error"):
        operational_score = 0.0
    elif rejections > 8:
        operational_score = max(0.0, operational_score - 2.0)

    final_score = round(
        0.60 * historical_score
        + 0.25 * predictive_score
        + 0.10 * robustness_score
        + 0.05 * operational_score,
        2,
    )

    # Guardrail: backtest negativo ou sem trades não vira INICIAR só por contexto recente.
    if (result.get("trades_count") or 0) == 0 or (result.get("total_profit") or 0.0) <= 0:
        final_score = min(final_score, 3.32)

    # Freio de robustez: INICIAR exige amostra suficiente e fora da amostra positivo.
    gate = robustness_gate(result.get("robust"), result.get("timeframe"))
    capped = gate["applied"] and not gate["passed"] and final_score >= SCORE_CAP_WITHOUT_ROBUSTNESS
    if capped:
        final_score = SCORE_CAP_WITHOUT_ROBUSTNESS

    verdict, level = verdict_from_score(final_score)
    reasons = list(rec.get("reasons") or [])
    if capped:
        reasons.insert(0, "Veredito limitado a CUIDADO: " + "; ".join(gate["failed"]) + ".")
    pred_reasons = predictive.get("reasons") or []
    if pred_reasons:
        reasons.append(
            f"Score preditivo {predictive_score:.2f}/10 ({predictive.get('regime', 'regime indefinido')}, confiança {float(predictive.get('confidence') or 0.0) * 100:.0f}%): "
            + pred_reasons[0]
        )
    reasons.append(
        f"Score final combina histórico ({historical_score:.2f}), preditivo ({predictive_score:.2f}), robustez ({robustness_score:.2f}) e operacional ({operational_score:.2f})."
    )

    rec.update({
        "verdict": verdict,
        "level": level,
        "score": final_score,
        "historical_score": round(historical_score, 2),
        "predictive_score": round(predictive_score, 2),
        "robustness_score": round(robustness_score, 2),
        "operational_score": round(operational_score, 2),
        "robust_gate": gate,
        "score_model": "composite_v1",
        "score_weights": {
            "historical": 0.60,
            "predictive": 0.25,
            "robustness": 0.10,
            "operational": 0.05,
        },
        "reasons": reasons,
    })
    return rec


def unsupported_result(strategy_id: str, reasons: list[str], timeframe: str | None = None) -> dict:
    """Resultado 'N/A' (não contemplado) no mesmo formato do Scanner."""
    cls = REGISTRY.get(strategy_id)
    info = cls.info() if cls else None
    return {
        "strategy_id": strategy_id,
        "strategy_name": info.name if info else strategy_id,
        "timeframe": timeframe or (info.recommended_timeframe if info else None) or "—",
        "backtest_mode": "strict",
        "coverage": {"mode": "strict", "level": "unsupported", "label": "Não contemplado", "reasons": reasons},
        "recommendation": {"verdict": "N/A", "level": "na", "reasons": reasons},
    }


async def load_candles_for(symbol: str, timeframes, limits: dict[str, int] | None = None) -> dict[str, list]:
    """Candles por timeframe: cache local (banco) primeiro, OKX para o que faltar. Atualiza o cache em segundo plano.

    `limits` (opcional) = quantas velas buscar por timeframe; sem ele vale o histórico-alvo (history_target), que para 1h
    são 20.000 velas (200 páginas da OKX). Quem só usa as últimas N velas deve pedir só N."""
    limits = limits or {}
    def _limit(tf: str) -> int:
        return int(limits.get(tf) or history_target(tf))
    import aiohttp

    candle_cache: dict[str, list] = {}
    timeframes = set(timeframes)

    def _from_db(tf: str, limit: int):
        with SessionLocal() as db_local:
            return load_candles(db_local, symbol, tf, limit=limit)

    for tf in timeframes:
        try:
            # Em thread: a leitura do banco é bloqueante e o laço de eventos é o mesmo dos bots.
            local = await asyncio.to_thread(_from_db, tf, _limit(tf))
            if local:
                candle_cache[tf] = local
        except Exception as exc:
            log.warning("Backtest: erro lendo o cache %s/%s: %s", symbol, tf, exc)

    missing = [tf for tf in timeframes if not candle_cache.get(tf)]
    if missing:
        try:
            async with aiohttp.ClientSession() as session:
                ex = build_exchange(session)
                for tf in missing:
                    try:
                        bar = map_timeframe_for_history(tf)
                        candle_cache[tf] = await ex.fetch_history_candles(symbol, bar, limit=_limit(tf)) or []
                    except Exception as exc:
                        log.warning("Backtest: erro buscando candles %s/%s: %s", symbol, tf, exc)
                        candle_cache[tf] = []
        except Exception as exc:
            log.warning("Backtest: erro preparando a OKX para %s: %s", symbol, exc)
            for tf in missing:
                candle_cache.setdefault(tf, [])

    # Atualiza o cache local com o que veio da OKX (não bloqueia a resposta).
    fresh = {tf: candle_cache[tf] for tf in missing if candle_cache.get(tf)}
    if fresh:
        asyncio.create_task(market_data_svc.persist_candles(symbol, fresh))
    for tf in timeframes:
        candle_cache.setdefault(tf, [])
    return candle_cache


async def run_one(
    strategy_id: str,
    symbol: str,
    timeframe: str,
    candle_cache: dict[str, list],
    *,
    params: dict | None = None,
    detail: bool = False,
) -> dict:
    """Backtest estrito + pontuação (histórica + preditiva + robustez) de UMA estratégia num símbolo/timeframe.

    `candle_cache` traz os candles já carregados (do timeframe e dos extras da estratégia). `detail=True` mantém a lista
    de operações e as premissas (tela do bot); o Scanner as descarta para reduzir o payload."""
    global _predictive
    cls = REGISTRY[strategy_id]
    base = {
        "strategy_id": strategy_id,
        "strategy_name": cls.info().name,
        "timeframe": timeframe,
        "symbol": symbol,
        "backtest_mode": "strict",
    }
    candles = candle_cache.get(timeframe, [])
    if not candles:
        return {**base, **unsupported_result(
            strategy_id, [f"Dados históricos indisponíveis para {symbol} no timeframe {timeframe}."], timeframe)}
    extra_tfs = list(cls.extra_timeframes() or [])
    missing_extra = [tf for tf in extra_tfs if not candle_cache.get(tf)]
    if missing_extra:
        return {**base, **unsupported_result(
            strategy_id, [f"Backtest estrito requer candles extras ausentes: {', '.join(missing_extra)}."], timeframe)}
    try:
        extra_candles = {tf: candle_cache.get(tf, []) for tf in extra_tfs}
        result = await run_strict_async(strategy_id, candles, extra_candles, params)
        result.update(base)
        result["candles_count"] = len(candles)
        if params:
            result["params_used"] = dict(params)
        if _predictive is None:
            _predictive = PredictiveScoringService()
        result["predictive"] = _predictive.score(strategy_id, symbol, timeframe, candles, backtest_result=result)
        result["recommendation"] = backtest_recommendation(result)
        result["recommendation"] = compose_predictive_recommendation(result, result["predictive"])
        result["recommendation"] = apply_study_verdict(strategy_id, result["recommendation"])
        if not detail:
            for k in ("trades", "closed_trades", "assumptions"):
                result.pop(k, None)
        return result
    except Exception as exc:
        log.warning("Backtest %s %s %s: %s", strategy_id, symbol, timeframe, exc)
        return {**base, **unsupported_result(strategy_id, [f"Erro na execução: {exc}"], timeframe)}


async def run_for_symbol(strategy_id: str, symbol: str, timeframe: str, params: dict | None = None) -> dict:
    """Backtest de uma estratégia num símbolo, com o histórico completo e os timeframes extras (tela do bot)."""
    cls = REGISTRY.get(strategy_id)
    if cls is None:
        raise BacktestUnavailable(f"Estratégia desconhecida: {strategy_id}.")
    if needs_external_context(cls):
        raise BacktestUnavailable(
            "Esta estratégia requer contexto externo ao vivo (GEX, grafo, on-chain, DEX ou fluxo de players) "
            "que o backtest não reconstrói historicamente.")
    timeframes = {timeframe, *(cls.extra_timeframes() or [])}
    cache = await load_candles_for(symbol, timeframes)
    result = await run_one(strategy_id, symbol, timeframe, cache, params=params, detail=True)
    if (result.get("recommendation") or {}).get("verdict") == "N/A":
        raise BacktestUnavailable("; ".join(result["recommendation"].get("reasons") or ["Backtest indisponível."]))
    return result
