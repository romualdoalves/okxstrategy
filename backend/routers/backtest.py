"""
Rotas de backtest por categoria (Scanner).
"""

from __future__ import annotations
from fastapi import APIRouter
import logging

import asyncio

from typing import Optional

from pydantic import BaseModel
from fastapi import HTTPException

from ..candle_store import load_candles
from ..database import SessionLocal
from ..backtest_engine import BacktestEngine, backtest_recommendation
from ..backtest_worker import run_strict_async
from ..market_data_service import HISTORY_TARGET_CANDLES
from ..verdict_gate import SCORE_CAP_WITHOUT_ROBUSTNESS, is_suitable_to_start, robustness_gate
from ..predictive_scoring import PredictiveScoringService
from ..exchanges.factory import build_exchange, map_timeframe_for_history
from ..common import market_data_svc

router = APIRouter()
log = logging.getLogger("main")


class CategoryBacktestRequest(BaseModel):
    category: str
    symbol: str
    exclude_strategies: list[str] = []
    strict: bool = True
    # Se verdadeiro, o scan da categoria para assim que uma estratégia for "adequada para iniciar"
    # (veredito INICIAR com robustez aprovada); as demais que ainda não começaram são canceladas.
    stop_when_suitable: bool = False


async def gather_until_suitable(tasks: list, stop_when_suitable: bool) -> tuple[list[dict], Optional[str], int]:
    """
    Aguarda os backtests. Sem `stop_when_suitable`, espera todos (como antes). Com ele, devolve assim que um
    resultado for adequado para iniciar (ver verdict_gate.is_suitable_to_start), cancela os que ainda não
    terminaram e conta quantos foram pulados.
    Retorna (resultados, strategy_id que disparou a parada ou None, nº de estratégias puladas).
    """
    if not stop_when_suitable:
        return list(await asyncio.gather(*tasks)), None, 0

    futures = [asyncio.ensure_future(t) for t in tasks]
    results: list[dict] = []
    stopped_by: Optional[str] = None
    try:
        for finished in asyncio.as_completed(futures):
            result = await finished
            results.append(result)
            if is_suitable_to_start(result):
                stopped_by = result.get("strategy_id")
                break
    finally:
        skipped = 0
        for fut in futures:
            if not fut.done():
                fut.cancel()
                skipped += 1
        # recolhe as tarefas canceladas para não deixar exceções pendentes
        await asyncio.gather(*futures, return_exceptions=True)
    return results, stopped_by, skipped

_VERDICT_ORDER = {"INICIAR": 0, "CUIDADO": 1, "NÃO INICIAR": 2, "N/A": 3}

_CONTEXT_FLAGS = (
    "needs_gex_context",
    "needs_graph_context",
    "needs_onchain_context",
    "needs_dex_context",
    "needs_market_players_context",
)

def _verdict_from_score(score: float) -> tuple[str, str]:
    if score >= 6.67:
        return "INICIAR", "success"
    if score >= 3.33:
        return "CUIDADO", "warning"
    return "NÃO INICIAR", "danger"

def _compose_predictive_recommendation(result: dict, predictive: dict) -> dict:
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

    verdict, level = _verdict_from_score(final_score)
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

@router.post("/api/backtest/category")
async def run_category_backtest(req: CategoryBacktestRequest):
    """Executa backtest de todas as estratégias de uma categoria para um símbolo."""
    return await _run_category_backtest_core(
        category=req.category,
        symbol=req.symbol,
        exclude_strategies=req.exclude_strategies,
        strict=req.strict,
        stop_when_suitable=req.stop_when_suitable,
    )

async def _run_category_backtest_core(
    category: str,
    symbol: str,
    exclude_strategies: list[str] | None = None,
    strict: bool = True,
    stop_when_suitable: bool = False,
) -> dict:
    """
    Núcleo do backtest de categoria, usado pelo endpoint HTTP (/api/backtest/category).
    """
    import aiohttp as _aiohttp
    from types import SimpleNamespace
    from ..strategies.registry import REGISTRY

    exclude_strategies = exclude_strategies or []
    req = SimpleNamespace(category=category, symbol=symbol, exclude_strategies=exclude_strategies, strict=strict)

    prefix = req.category.upper()
    strategy_ids = [sid for sid in REGISTRY if sid.upper().startswith(prefix) and sid not in req.exclude_strategies]
    if not strategy_ids:
        raise HTTPException(404, f"Nenhuma estratégia encontrada para categoria '{prefix}'")

    # Separa estratégias que precisam de contexto externo (não backtestáveis)
    na_ids: list[str] = []
    tf_groups: dict[str, list[str]] = {}
    extra_tf_by_sid: dict[str, list[str]] = {}
    for sid in sorted(strategy_ids):
        cls = REGISTRY[sid]
        if any(getattr(cls, flag, False) for flag in _CONTEXT_FLAGS):
            na_ids.append(sid)
            continue
        tf = cls.info().recommended_timeframe or "15m"
        tf_groups.setdefault(tf, []).append(sid)
        extra_tf_by_sid[sid] = list(cls.extra_timeframes() or [])

    # Busca candles uma vez por timeframe único (prioriza cache local de MarketData)
    candle_cache: dict[str, list] = {}
    all_timeframes = set(tf_groups.keys())
    for extra_tfs in extra_tf_by_sid.values():
        all_timeframes.update(extra_tfs)

    def _candles_from_db(symbol: str, timeframe: str, limit: int = HISTORY_TARGET_CANDLES):
        with SessionLocal() as db_local:
            return load_candles(db_local, symbol, timeframe, limit=limit)

    for tf in all_timeframes:
        try:
            local = _candles_from_db(req.symbol, tf, limit=HISTORY_TARGET_CANDLES)
            if local:
                candle_cache[tf] = local
        except Exception as exc:
            log.warning("Category backtest cache read error %s/%s (%s): %s", req.symbol, prefix, tf, exc)

    missing_tfs = [tf for tf in all_timeframes if not candle_cache.get(tf)]
    try:
        if missing_tfs:
            async with _aiohttp.ClientSession() as session:
                ex = build_exchange(session)
                for tf in missing_tfs:
                    try:
                        bar = map_timeframe_for_history(tf)
                        candle_cache[tf] = await ex.fetch_history_candles(req.symbol, bar, limit=HISTORY_TARGET_CANDLES) or []
                    except Exception as exc:
                        log.warning(
                            "Category backtest candle fetch error %s/%s (%s): %s",
                            req.symbol,
                            prefix,
                            tf,
                            exc,
                        )
                        candle_cache[tf] = []
    except Exception as exc:
        log.warning(
            "Category backtest market setup error %s/%s: %s",
            req.symbol,
            prefix,
            exc,
        )
        for tf in missing_tfs:
            candle_cache[tf] = candle_cache.get(tf, [])

    # Atualiza MarketData incrementalmente com os candles recém-buscados da OKX.
    # Fire-and-forget: não bloqueia a resposta do Scanner.
    _fresh = {tf: candle_cache[tf] for tf in missing_tfs if candle_cache.get(tf)}
    if _fresh:
        asyncio.create_task(market_data_svc.persist_candles(req.symbol, _fresh))

    for tf in all_timeframes:
        candle_cache.setdefault(tf, [])

    # Executa backtests em paralelo
    predictive_service = PredictiveScoringService()

    async def _run(sid: str, tf: str) -> dict:
        candles = candle_cache.get(tf, [])
        info = REGISTRY[sid].info()
        base = {
            "strategy_id": sid,
            "strategy_name": info.name,
            "timeframe": tf,
            "backtest_mode": "strict" if req.strict else "classic",
        }
        if not candles:
            return {**base, "recommendation": {
                "verdict": "N/A", "level": "na",
                "reasons": [f"Dados históricos indisponíveis para {req.symbol} no timeframe {tf}."],
            }, "coverage": {
                "mode": "strict" if req.strict else "classic",
                "level": "unsupported",
                "label": "Não contemplado",
                "reasons": [f"Sem candles para {req.symbol} em {tf}."],
            }}
        try:
            engine = BacktestEngine(sid)
            if req.strict:
                missing_extra = [extra_tf for extra_tf in extra_tf_by_sid.get(sid, []) if not candle_cache.get(extra_tf)]
                if missing_extra:
                    return {**base, "recommendation": {
                        "verdict": "N/A", "level": "na",
                        "reasons": [f"Backtest estrito requer candles extras ausentes: {', '.join(missing_extra)}."],
                    }, "coverage": {
                        "mode": "strict",
                        "level": "unsupported",
                        "label": "Não contemplado",
                        "reasons": [f"Timeframes extras indisponíveis: {', '.join(missing_extra)}."],
                    }}
                extra_candles = {
                    extra_tf: candle_cache.get(extra_tf, [])
                    for extra_tf in extra_tf_by_sid.get(sid, [])
                }
                result = await run_strict_async(sid, candles, extra_candles)
            else:
                result = await engine.run(candles)
                result["coverage"] = {
                    "mode": "classic",
                    "level": "partial",
                    "label": "Parcial",
                    "reasons": ["Backtest clássico: não simula SL/TP1/trailing, custos ou contexto extra."],
                }
            result.update(base)
            result["predictive"] = predictive_service.score(
                sid,
                req.symbol,
                tf,
                candles,
                backtest_result=result,
            )
            result["recommendation"] = backtest_recommendation(result)
            result["recommendation"] = _compose_predictive_recommendation(result, result["predictive"])
            result.pop("trades", None)
            result.pop("closed_trades", None)
            result.pop("assumptions", None)
            return result
        except Exception as exc:
            log.warning("Category backtest error %s: %s", sid, exc)
            return {**base, "recommendation": {
                "verdict": "N/A", "level": "na",
                "reasons": [f"Erro na execução: {exc}"],
            }, "coverage": {
                "mode": "strict" if req.strict else "classic",
                "level": "unsupported",
                "label": "Não contemplado",
                "reasons": [f"Erro na execução: {exc}"],
            }}

    tasks = [_run(sid, tf) for tf, sids in tf_groups.items() for sid in sids]
    results, stopped_by, skipped = await gather_until_suitable(tasks, stop_when_suitable)

    # Acrescenta entradas N/A para estratégias com contexto externo
    for sid in na_ids:
        info = REGISTRY[sid].info()
        results.append({
            "strategy_id": sid,
            "strategy_name": info.name,
            "timeframe": info.recommended_timeframe or "—",
            "backtest_mode": "strict" if req.strict else "classic",
            "coverage": {
                "mode": "strict" if req.strict else "classic",
                "level": "unsupported",
                "label": "Não contemplado",
                "reasons": [
                    "Requer contexto externo ao vivo que o Scanner não reconstrói historicamente.",
                    "Exemplos: GEX, grafo de correlação vivo, onchain, DEX ou fluxo de players.",
                ],
            },
            "recommendation": {
                "verdict": "N/A", "level": "na",
                "reasons": ["Não contemplada pelo Backtest Estrito: requer contexto externo ao vivo."],
            },
        })

    results.sort(key=lambda x: (
        _VERDICT_ORDER.get(x["recommendation"]["verdict"], 9),
        -(x["recommendation"].get("score") or 0),
    ))

    return {
        "category": prefix, "symbol": req.symbol, "results": results, "total": len(results),
        "stopped_early": stopped_by is not None, "stopped_by": stopped_by, "skipped": skipped,
    }
