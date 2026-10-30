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

from ..backtest_service import (
    compose_predictive_recommendation as _compose_predictive_recommendation,  # os testes de veredito importam daqui
    load_candles_for,
    needs_external_context,
    run_one,
    unsupported_result,
)
from ..verdict_gate import is_suitable_to_start

router = APIRouter()
log = logging.getLogger("main")


class CategoryBacktestRequest(BaseModel):
    category: str
    symbol: str
    exclude_strategies: list[str] = []
    # Se informado, roda SÓ estas estratégias da categoria (as que o usuário marcou na tela).
    include_strategies: Optional[list[str]] = None
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


@router.post("/api/backtest/category")
async def run_category_backtest(req: CategoryBacktestRequest):
    """Executa backtest de todas as estratégias de uma categoria para um símbolo."""
    return await _run_category_backtest_core(
        category=req.category,
        symbol=req.symbol,
        exclude_strategies=req.exclude_strategies,
        include_strategies=req.include_strategies,
        stop_when_suitable=req.stop_when_suitable,
    )


async def _run_category_backtest_core(
    category: str,
    symbol: str,
    exclude_strategies: list[str] | None = None,
    stop_when_suitable: bool = False,
    include_strategies: list[str] | None = None,
) -> dict:
    """
    Núcleo do backtest de categoria (endpoint /api/backtest/category). Só orquestra: carregamento de candles,
    execução e pontuação ficam em backtest_service, o mesmo código do backtest de um bot.
    """
    from ..strategies.registry import REGISTRY

    exclude_strategies = exclude_strategies or []
    prefix = category.upper()
    strategy_ids = [sid for sid in REGISTRY if sid.upper().startswith(prefix) and sid not in exclude_strategies]
    if include_strategies is not None:
        wanted = set(include_strategies)
        strategy_ids = [sid for sid in strategy_ids if sid in wanted]
    if not strategy_ids:
        raise HTTPException(404, f"Nenhuma estratégia encontrada para categoria '{prefix}'")

    # Separa estratégias que precisam de contexto externo (não backtestáveis)
    na_ids: list[str] = []
    tf_groups: dict[str, list[str]] = {}
    all_timeframes: set[str] = set()
    for sid in sorted(strategy_ids):
        cls = REGISTRY[sid]
        if needs_external_context(cls):
            na_ids.append(sid)
            continue
        from ..validation.reports import validated_timeframe
        tf = validated_timeframe(sid, cls.info().recommended_timeframe)     # o mesmo timeframe do bot
        tf_groups.setdefault(tf, []).append(sid)
        all_timeframes.add(tf)
        all_timeframes.update(cls.extra_timeframes() or [])

    # Candles uma vez por timeframe único (cache local primeiro, OKX para o que faltar)
    candle_cache = await load_candles_for(symbol, all_timeframes)

    tasks = [run_one(sid, symbol, tf, candle_cache) for tf, sids in tf_groups.items() for sid in sids]
    results, stopped_by, skipped = await gather_until_suitable(tasks, stop_when_suitable)

    # Acrescenta entradas N/A para estratégias com contexto externo
    for sid in na_ids:
        results.append(unsupported_result(sid, [
            "Requer contexto externo ao vivo que o Scanner não reconstrói historicamente.",
            "Exemplos: GEX, grafo de correlação vivo, onchain, DEX ou fluxo de players.",
        ]))

    results.sort(key=lambda x: (
        _VERDICT_ORDER.get(x["recommendation"]["verdict"], 9),
        -(x["recommendation"].get("score") or 0),
    ))

    return {
        "category": prefix, "symbol": symbol, "results": results, "total": len(results),
        "stopped_early": stopped_by is not None, "stopped_by": stopped_by, "skipped": skipped,
    }
