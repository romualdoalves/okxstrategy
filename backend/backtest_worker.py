"""
backtest_worker.py — Execução do backtest estrito fora do event loop.

O motor é CPU-bound (recalcula os indicadores a cada candle). Rodá-lo no event loop do servidor
trava os bots, que vivem no mesmo loop. Aqui há um pool de processos (contexto "spawn": o servidor
tem threads e asyncio, e fork nesse cenário é frágil) com poucos workers para não disputar CPU com
os bots. Se o pool não estiver disponível, cai para uma thread (não bloqueia o loop, mas disputa o
GIL).

O módulo é leve de propósito: cada worker importa só o motor, não os routers.
"""
from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Optional

log = logging.getLogger("backtest_worker")

_pool: Optional[ProcessPoolExecutor] = None


def _workers() -> int:
    try:
        return max(1, int(os.getenv("BACKTEST_WORKERS", "2")))
    except ValueError:
        return 2


def run_strict_job(strategy_id: str, candles: list, extra_candles: Optional[dict]) -> dict:
    """Executa um backtest estrito completo (roda no processo worker)."""
    from .backtest_engine import BacktestEngine
    return BacktestEngine(strategy_id).run_strict_sync(candles, extra_candles=extra_candles)


def _get_pool() -> ProcessPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=_workers(), mp_context=multiprocessing.get_context("spawn"))
    return _pool


def shutdown_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


async def run_strict_async(strategy_id: str, candles: list, extra_candles: Optional[dict] = None) -> dict:
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(_get_pool(), run_strict_job, strategy_id, candles, extra_candles)
    except (BrokenProcessPool, OSError, RuntimeError) as exc:
        log.warning("Pool de backtest indisponível (%s); usando thread.", exc)
        shutdown_pool()
        return await asyncio.to_thread(run_strict_job, strategy_id, candles, extra_candles)
