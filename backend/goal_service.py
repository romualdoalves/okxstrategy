"""
goal_service.py — Busca da "Meta 1% Hoje" (roda em segundo plano, com progresso).

Para cada estratégia intradiária backtestável e cada ativo LIVRE do top 5 (sem bot), roda o backtest ESTRITO (o mesmo motor
do Scanner e do bot: taxas, slippage, stop e trailing) nos candles recentes e mede a chance de bater a meta em janelas móveis
de 24 h (ver goal.py). Usa o carregador de candles e o motor de backtest_service/backtest_worker: não há motor próprio.

Um job por vez, em memória (reinício do servidor zera). O resultado fica em cache por CACHE_TTL_S.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from . import goal as G
from .backtest_service import load_candles_for, needs_external_context
from .backtest_worker import run_strict_async
from .database import BotModel, SessionLocal
from .exchanges.okx_universe import fetch_top_spot_assets
from .profit_lock import EXIT_SLIPPAGE_RATE, fee_rate
from .strategies.registry import REGISTRY

log = logging.getLogger("goal_service")

# Velas avaliadas por timeframe (o histórico local de 1m a 15m é curto de propósito; 1h cobre ~6 meses)
HISTORY_CAP = {"1m": 3000, "5m": 3000, "15m": 3000, "1h": 4400}
EXTRA_CAP = 1000          # velas dos timeframes de CONTEXTO (4H, 1D, 1W...): a estratégia só olha as últimas ~500
CACHE_TTL_S = 30 * 60
CONCURRENCY = 2
WARMUP = 100

_state: dict = {}
_task: Optional[asyncio.Task] = None


def _fresh_state() -> dict:
    return {
        "state": "idle", "progress": {"done": 0, "total": 0, "current": None}, "started_at": None,
        "finished_at": None, "finished_ts": 0.0, "assets": {"top5": [], "used": [], "free": []},
        "top": [], "closest": [], "evaluated": 0, "qualified": 0, "failed": 0, "error": None, "note": None,
    }


_state = _fresh_state()


def candidate_strategies() -> list[tuple[str, str]]:
    """(strategy_id, timeframe) das estratégias backtestáveis cujo timeframe cabe numa meta de 24 h."""
    out = []
    for sid, cls in sorted(REGISTRY.items()):
        if needs_external_context(cls):
            continue
        info = cls.info()
        if "test" in (info.tags or []):          # estratégias de diagnóstico da plataforma (ex.: T000) nunca são candidatas
            continue
        tf = info.recommended_timeframe or "15m"
        if tf.lower() in G.INTRADAY_TIMEFRAMES:
            out.append((sid, tf))
    return out


def _used_symbols() -> set[str]:
    with SessionLocal() as db:
        return {(row[0] or "").upper() for row in db.query(BotModel.symbol).all()}


def build_row(strategy_id: str, symbol: str, timeframe: str, result: dict, candles: list) -> Optional[dict]:
    """Métricas de um (estratégia, ativo) a partir do resultado do backtest estrito; None se não houver dados."""
    if result.get("error") or len(candles) <= WARMUP + 1:
        return None
    hits, windows, independent = G.window_hits(result.get("closed_trades") or [], candles)
    if not windows:
        return None
    chance = hits / windows
    stats = {
        "trades_count": result.get("trades_count", 0),
        "total_profit": result.get("total_profit", 0.0),
        "profit_factor": result.get("profit_factor", 0.0),
        "chance": chance,
        "independent_windows": independent,
    }
    ok, unmet = G.qualification(stats)
    cls = REGISTRY[strategy_id]
    return {
        "strategy_id": strategy_id,
        "strategy_name": cls.info().name,
        "symbol": symbol,
        "timeframe": timeframe,
        "chance_pct": round(100 * chance, 1),
        "chance_lb_pct": round(100 * G.wilson_lower_bound(chance, independent), 1),
        "windows_hit": hits,
        "windows_total": windows,
        "independent_windows": round(independent, 1),
        "trades_count": stats["trades_count"],
        "total_profit": round(stats["total_profit"], 2),
        "profit_factor": round(stats["profit_factor"], 2),
        "max_drawdown": round(result.get("max_drawdown", 0.0), 2),
        "win_rate": round(result.get("win_rate", 0.0), 1),
        "history_days": round((candles[-1].epoch - candles[WARMUP].epoch) / 86_400_000, 1),
        "qualifies": ok,
        "unmet": unmet,
    }


def rank(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(top, mais_próximas). Top = os que atendem a todos os critérios, melhores primeiro (pelo limite inferior da chance,
    que penaliza amostra curta). Mais próximas = as melhores entre as que NÃO atendem, só para informar."""
    qualified = sorted((r for r in rows if r["qualifies"]),
                       key=lambda r: (r["chance_lb_pct"], r["chance_pct"], r["total_profit"]), reverse=True)
    # Entre as que não atendem, as de amostra suficiente vêm primeiro: uma "chance" de 76% medida em 2 dias não é a mais próxima.
    others = sorted((r for r in rows if not r["qualifies"]),
                    key=lambda r: (r["independent_windows"] >= G.MIN_INDEPENDENT_WINDOWS, r["chance_lb_pct"],
                                   r["chance_pct"], r["total_profit"]), reverse=True)
    return qualified[:G.TOP_N], others[:G.TOP_N]


async def _run() -> None:
    global _state
    try:
        top5 = await fetch_top_spot_assets()
        if not top5:
            raise RuntimeError("Não foi possível obter o ranking de ativos da OKX.")
        used = await asyncio.to_thread(_used_symbols)
        symbols = [a["symbol"] for a in top5]
        free = [s for s in symbols if s.upper() not in used]
        _state["assets"] = {
            "top5": [{"symbol": a["symbol"], "volume_usd_24h": round(a["volume_usd_24h"]),
                      "change_24h_pct": a["change_24h_pct"]} for a in top5],
            "used": [s for s in symbols if s.upper() in used],
            "free": free,
        }
        strategies = candidate_strategies()
        _state["progress"]["total"] = len(strategies) * len(free)
        if not free:
            _state["note"] = "Todos os ativos do top 5 já têm bot: não há ativo livre para uma nova meta."
            _state.update(state="done", finished_at=datetime.now(timezone.utc).isoformat(), finished_ts=time.time())
            return

        rows: list[dict] = []
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(sid: str, symbol: str, tf: str, cache: dict) -> None:
            async with sem:
                _state["progress"]["current"] = f"{sid} · {symbol}"
                try:
                    cls = REGISTRY[sid]
                    candles = (cache.get(tf) or [])[-HISTORY_CAP.get(tf.lower(), 3000):]
                    extra_tfs = list(cls.extra_timeframes() or [])
                    if not candles or any(not cache.get(x) for x in extra_tfs):
                        _state["failed"] += 1
                        return
                    extra = {x: cache[x] for x in extra_tfs}
                    result = await run_strict_async(sid, candles, extra, None)
                    row = build_row(sid, symbol, tf, result, candles)
                    if row:
                        rows.append(row)
                    else:
                        _state["failed"] += 1
                except Exception as exc:
                    _state["failed"] += 1
                    log.warning("Meta: %s %s: %s", sid, symbol, exc)
                finally:
                    _state["progress"]["done"] += 1

        # Só as velas que a busca usa (não o histórico-alvo inteiro) e um ativo por vez na OKX (limite de requisições), mas
        # o ativo seguinte já carrega enquanto o anterior é calculado: a CPU não fica esperando a rede.
        tfs = {tf for _, tf in strategies}
        limits = {tf: HISTORY_CAP.get(tf.lower(), 3000) for tf in tfs}
        for sid, _ in strategies:
            for extra_tf in REGISTRY[sid].extra_timeframes() or []:
                tfs.add(extra_tf)
                limits.setdefault(extra_tf, EXTRA_CAP)
        loop = asyncio.get_running_loop()
        loaded = {sym: loop.create_future() for sym in free}

        async def loader() -> None:
            for sym in free:
                try:
                    loaded[sym].set_result(await load_candles_for(sym, tfs, limits))
                except Exception as exc:
                    loaded[sym].set_exception(exc)

        loader_task = asyncio.create_task(loader())
        for symbol in free:
            try:
                cache = await loaded[symbol]
            except Exception as exc:
                log.warning("Meta: candles de %s indisponíveis: %s", symbol, exc)
                _state["failed"] += len(strategies)
                _state["progress"]["done"] += len(strategies)
                continue
            await asyncio.gather(*(one(sid, symbol, tf, cache) for sid, tf in strategies))
        await loader_task

        top, closest = rank(rows)
        _state.update(top=top, closest=closest, evaluated=len(rows), qualified=sum(1 for r in rows if r["qualifies"]),
                      state="done", finished_at=datetime.now(timezone.utc).isoformat(), finished_ts=time.time())
        _state["progress"]["current"] = None
    except Exception as exc:
        log.exception("Busca da meta falhou")
        _state.update(state="error", error=str(exc), finished_at=datetime.now(timezone.utc).isoformat())


def status() -> dict:
    """Estado atual + os parâmetros reais das regras (a tela os mostra: nada fica escrito em dois lugares)."""
    out = {k: v for k, v in _state.items() if k != "finished_ts"}
    out["criteria"] = {
        "target_pct": G.TARGET_PCT, "window_hours": G.WINDOW_HOURS, "min_trades": G.MIN_TRADES,
        "min_profit_factor": G.MIN_PROFIT_FACTOR, "min_chance_pct": round(100 * G.MIN_CHANCE), "top_n": G.TOP_N,
        "min_independent_windows": G.MIN_INDEPENDENT_WINDOWS,
        "fee_rate_pct": round(100 * fee_rate(), 3), "exit_slippage_pct": round(100 * EXIT_SLIPPAGE_RATE, 3),
        "history_cap": HISTORY_CAP, "timeframes": list(G.INTRADAY_TIMEFRAMES),
        "strategies_considered": len(candidate_strategies()), "exit_margin_pct": G.EXIT_MARGIN_PCT,
    }
    out["age_seconds"] = int(time.time() - _state["finished_ts"]) if _state.get("finished_ts") else None
    return out


async def live_status() -> dict:
    """status() + o top 5 de AGORA: o top 5 é dinâmico, então cada candidato diz se o ativo ainda está nele. O servidor
    também confere isso ao criar o bot; aqui é para a tela não oferecer um botão que seria recusado."""
    out = status()
    current = [a["symbol"] for a in await fetch_top_spot_assets()]
    out["current_top5"] = current
    out["top5_available"] = bool(current)
    for key in ("top", "closest"):
        for row in out[key]:
            row["in_top5_now"] = row["symbol"] in current
    return out


async def start_scan(force: bool = False) -> dict:
    """Inicia a busca (ou devolve a em andamento / o cache recente, se `force` for falso). O cache só vale enquanto o
    top 5 for o mesmo da busca: se mudou, a busca é refeita."""
    global _task, _state
    if _state["state"] == "running":
        return await live_status()
    if not force and _state["state"] == "done" and time.time() - _state["finished_ts"] < CACHE_TTL_S:
        current = {a["symbol"] for a in await fetch_top_spot_assets()}
        scanned = {a["symbol"] for a in _state["assets"]["top5"]}
        if current and current == scanned:
            return await live_status()
    _state = _fresh_state()
    _state.update(state="running", started_at=datetime.now(timezone.utc).isoformat())
    _task = asyncio.create_task(_run())          # referência guardada: evita o coletor de lixo cancelar o job
    return await live_status()
