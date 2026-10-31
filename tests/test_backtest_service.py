"""Backtest centralizado: um só motor (estrito), um só serviço e uma só regra de veredito para o Scanner e para o bot.

Antes havia um motor "clássico" (sem taxas, slippage, stops nem contexto semanal: a TF008 dava 0 operações) usado pelo botão
de backtest do bot e pelo otimizador, e uma cópia da pontuação em JavaScript."""
import asyncio
import pathlib
import random
import re
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from backend import backtest_service as svc
from backend.backtest_engine import BacktestEngine
from backend.backtest_worker import run_strict_job
from backend.exchanges.base import CandleBar
from backend.routers import backtest as scanner_router
from backend.routers import bot_analysis

BACKEND = pathlib.Path(svc.__file__).resolve().parent
FRONTEND_SRC = BACKEND.parent / "frontend" / "src"


def synthetic_candles(n, seed=1, step_ms=3_600_000):
    rng = random.Random(seed)
    p, out = 100.0, []
    for i in range(n):
        p *= 1 + rng.gauss(0.0002, 0.006)
        o = p
        c = p * (1 + rng.gauss(0, 0.002))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.002)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, 0.002)))
        out.append(CandleBar(epoch=1_700_000_000_000 + i * step_ms, open=o, high=h, low=lo, close=c,
                             volume=1000 + rng.random() * 500))
    return out


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def inline_worker(monkeypatch):
    """Executa o job no próprio processo (sem pool) e registra o que o serviço mandou ao motor."""
    calls = []

    async def fake(strategy_id, candles, extra_candles=None, params=None):
        calls.append({"sid": strategy_id, "n": len(candles), "extra": sorted(extra_candles or {}), "params": params})
        return run_strict_job(strategy_id, candles, extra_candles, params)

    monkeypatch.setattr(svc, "run_strict_async", fake)
    return calls


# ── só existe um motor ───────────────────────────────────────────────────────────────────────────────────────────
def test_the_legacy_classic_engine_is_gone():
    assert not hasattr(BacktestEngine, "run")
    assert hasattr(BacktestEngine, "run_strict_sync")


def test_no_backend_code_calls_a_second_engine_or_the_old_optimizer():
    offenders = []
    for path in BACKEND.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bengine\.run\(|\.run\(candles|\.optimize\(", text):
            offenders.append(path.name)
        if path.name in ("bot_analysis.py", "backtest.py") and "optimizer" in text:
            offenders.append(path.name + ":optimizer")
    assert offenders == []


def test_the_frontend_no_longer_has_its_own_copy_of_the_scoring():
    text = "\n".join(p.read_text(encoding="utf-8") for p in FRONTEND_SRC.rglob("*.jsx"))
    assert "getBacktestScore" not in text and "getBacktestRecommendation" not in text
    assert "optimizeBotParams" not in (FRONTEND_SRC / "api.js").read_text(encoding="utf-8")


# ── o serviço é o mesmo caminho para todos ──────────────────────────────────────────────────────────────────────
def test_service_result_matches_the_engine_exactly(inline_worker):
    cs = synthetic_candles(700, seed=2)
    direct = BacktestEngine("TF003").run_strict_sync(cs)
    via = run(svc.run_one("TF003", "BTC-USDT", "1h", {"1h": cs}))
    for key in ("total_profit", "trades_count", "profit_factor", "max_drawdown", "win_rate", "final_balance"):
        assert via[key] == direct[key], key
    assert via["backtest_mode"] == "strict" and via["candles_count"] == 700


def test_scanner_and_bot_detail_agree_and_only_detail_keeps_the_trades(inline_worker):
    cs = synthetic_candles(700, seed=3)
    scan = run(svc.run_one("TF003", "BTC-USDT", "1h", {"1h": cs}, detail=False))
    bot = run(svc.run_one("TF003", "BTC-USDT", "1h", {"1h": cs}, detail=True))
    assert scan["recommendation"] == bot["recommendation"]            # mesmo veredito e mesma pontuação
    assert scan["total_profit"] == bot["total_profit"]
    assert "trades" not in scan and "closed_trades" not in scan and "assumptions" not in scan
    assert bot["trades"] and bot["assumptions"]


def test_bot_params_reach_the_engine(inline_worker):
    cs = synthetic_candles(700, seed=4)
    run(svc.run_one("TF003", "BTC-USDT", "1h", {"1h": cs}, params={"macd_fast": 8, "macd_slow": 21}))
    assert inline_worker[-1]["params"] == {"macd_fast": 8, "macd_slow": 21}


def test_missing_extra_timeframe_is_na_and_never_runs_the_engine(inline_worker):
    cs = synthetic_candles(700, seed=5, step_ms=86_400_000)
    res = run(svc.run_one("TF008", "BTC-USDT", "1D", {"1D": cs, "1W": []}))      # TF008 exige a EMA semanal
    assert res["recommendation"]["verdict"] == "N/A" and "1W" in res["recommendation"]["reasons"][0]
    assert inline_worker == []


def test_tf008_gets_its_weekly_context_instead_of_zero_trades(inline_worker, monkeypatch):
    """Regressão: o motor antigo não entregava o contexto semanal e a TF008 mostrava 0 operações."""
    daily = synthetic_candles(900, seed=6, step_ms=86_400_000)
    weekly = synthetic_candles(130, seed=7, step_ms=7 * 86_400_000)
    res = run(svc.run_one("TF008", "BTC-USDT", "1D", {"1D": daily, "1W": weekly}))
    assert inline_worker[-1]["extra"] == ["1W"]
    assert "N/A" != res["recommendation"]["verdict"]


# ── o bot e o Scanner chamam o serviço ──────────────────────────────────────────────────────────────────────────
class FakeDb:
    def __init__(self, bot):
        self.bot = bot

    def get(self, _model, _id):
        return self.bot


def test_bot_endpoint_delegates_to_the_service(monkeypatch):
    seen = {}

    async def fake(strategy_id, symbol, timeframe, params=None):
        seen.update(sid=strategy_id, symbol=symbol, tf=timeframe, params=params)
        return {"ok": True}

    monkeypatch.setattr(bot_analysis, "run_for_symbol", fake)
    bot = NS(strategy_id="TF008", symbol="BTC-USDT", timeframe="1D", strategy_params={"sl_mult": 3.0})
    assert run(bot_analysis.run_bot_backtest(1, FakeDb(bot))) == {"ok": True}
    assert seen == {"sid": "TF008", "symbol": "BTC-USDT", "tf": "1D", "params": {"sl_mult": 3.0}}


def test_bot_endpoint_reports_unavailable_backtests_as_422(monkeypatch):
    async def fake(*a, **k):
        raise svc.BacktestUnavailable("requer contexto externo")

    monkeypatch.setattr(bot_analysis, "run_for_symbol", fake)
    bot = NS(strategy_id="IF001", symbol="BTC-USDT", timeframe="1h", strategy_params=None)
    with pytest.raises(HTTPException) as e:
        run(bot_analysis.run_bot_backtest(1, FakeDb(bot)))
    assert e.value.status_code == 422 and "contexto externo" in e.value.detail


def test_scanner_only_orchestrates_and_delegates_every_run_to_the_service(monkeypatch):
    calls = []

    async def fake_load(symbol, timeframes):
        return {tf: ["candles"] for tf in timeframes}

    async def fake_run_one(sid, symbol, tf, cache, **kw):
        calls.append((sid, symbol, tf, sorted(cache)))
        return {"strategy_id": sid, "recommendation": {"verdict": "CUIDADO", "score": 5.0}}

    monkeypatch.setattr(scanner_router, "load_candles_for", fake_load)
    monkeypatch.setattr(scanner_router, "run_one", fake_run_one)
    out = run(scanner_router._run_category_backtest_core("TF", "ETH-USDT"))
    assert calls and all(c[1] == "ETH-USDT" for c in calls)
    assert out["total"] >= len(calls)                                  # + as N/A de contexto externo
    assert len({c[0] for c in calls}) == len(calls)                    # cada estratégia roda uma vez


def test_scanner_runs_only_the_strategies_the_user_ticked(monkeypatch):
    calls = []

    async def fake_load(symbol, timeframes):
        return {tf: ["candles"] for tf in timeframes}

    async def fake_run_one(sid, symbol, tf, cache, **kw):
        calls.append(sid)
        return {"strategy_id": sid, "recommendation": {"verdict": "CUIDADO", "score": 5.0}}

    monkeypatch.setattr(scanner_router, "load_candles_for", fake_load)
    monkeypatch.setattr(scanner_router, "run_one", fake_run_one)
    req = scanner_router.CategoryBacktestRequest(category="TF", symbol="BTC-USDT", include_strategies=["TF008", "TF003"])
    out = run(scanner_router.run_category_backtest(req))
    assert sorted(calls) == ["TF003", "TF008"] and out["total"] == 2
    # sem o filtro continua rodando a categoria inteira
    calls.clear()
    run(scanner_router._run_category_backtest_core("TF", "BTC-USDT"))
    assert len(calls) > 2


def test_scanner_with_nothing_ticked_is_a_clear_404(monkeypatch):
    with pytest.raises(HTTPException) as e:
        run(scanner_router._run_category_backtest_core("TF", "BTC-USDT", include_strategies=[]))
    assert e.value.status_code == 404


def test_engine_closed_trades_carry_gross_net_and_peak(inline_worker):
    cs = synthetic_candles(900, seed=11)
    res = BacktestEngine("TF003").run_strict_sync(cs)
    assert res["closed_trades"]
    for t in res["closed_trades"]:
        assert t["gross_profit_pct"] - t["profit_pct"] == pytest.approx(t["fees"])         # stake 100: bruto = líquido + taxas
        assert t["max_gross_pct"] + 1e-9 >= t["gross_profit_pct"]                          # o pico nunca fica abaixo do fechamento


def test_scanner_never_recommends_a_strategy_the_study_retired(inline_worker):
    """TF013 (aposentada) saía como a melhor no Scanner e a criação do bot a recusava: o estudo manda no veredito."""
    from backend.backtest_service import apply_study_verdict
    rec = {"verdict": "INICIAR", "level": "success", "score": 8.1, "reasons": ["Lucro positivo."]}
    out = apply_study_verdict("TF013", rec)
    assert out["verdict"] == "NÃO INICIAR" and out["study_retired"] and out["score"] == 8.1
    assert out["reasons"][0].startswith("Aposentada pelo estudo:")
    assert rec["verdict"] == "INICIAR"                                    # não altera o original
    assert apply_study_verdict("TF008", rec)["verdict"] == "INICIAR"      # candidata: intacta
    assert apply_study_verdict("TF013", {"verdict": "N/A"})["verdict"] == "N/A"
