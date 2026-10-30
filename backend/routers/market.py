"""
Rotas de mercado: regime, relógio, tradabilidade, ranking, candles e ticker.
"""

from __future__ import annotations
from fastapi import APIRouter
import logging

import asyncio
from typing import Optional
from datetime import datetime, timezone

from fastapi import HTTPException

from ..exchanges.factory import build_exchange, get_exchange_provider, map_timeframe_for_history
from ..exchanges.okx_universe import fetch_top_spot_assets
from ..common import _assert_spot_only, _get_symbol_tradability, _norm_symbol

router = APIRouter()
log = logging.getLogger("main")


@router.get("/api/market/regime")
async def get_market_regime():
    """
    Avalia o regime atual do mercado de crypto (BTC-USDT) via OKX.

    Retorna:
      regime      : 'trending' | 'ranging' | 'volatile'
      trend_dir   : 'up' | 'down' | 'flat'
      session     : 'ny' | 'london' | 'asia' | 'off'
      atr_pct     : ATR como % do preço (volatilidade)
      bbw_pct     : Bollinger Band Width %
      ema_slope   : inclinação da EMA20 normalizada
      updated_at  : timestamp UTC
    """
    import datetime as _dt
    import math as _math

    def _session_now():
        h = _dt.datetime.utcnow().hour
        if 13 <= h < 22:  return "ny"       # 09:00–18:00 EST
        if  8 <= h < 13:  return "london"   # 08:00–13:00 UTC
        if  0 <= h <  8:  return "asia"     # Tóquio/Cingapura
        return "off"

    get_exchange_provider()
    from ..exchanges.okx import OKXExchange as _OKXEx
    _ex = _OKXEx()
    _candles = await _ex.fetch_candles("BTC-USDT", "1h", limit=30)
    if len(_candles) < 10:
        raise ValueError("dados insuficientes")
    bars = [{"c": c.close, "h": c.high, "l": c.low} for c in _candles]

    try:
        closes = [b["c"] for b in bars]
        highs  = [b["h"] for b in bars]
        lows   = [b["l"] for b in bars]
        n      = len(closes)

        # ATR (14)
        trs = []
        for i in range(1, n):
            trs.append(max(highs[i] - lows[i],
                           abs(highs[i] - closes[i-1]),
                           abs(lows[i]  - closes[i-1])))
        atr     = sum(trs[-14:]) / min(14, len(trs))
        atr_pct = atr / closes[-1] * 100

        # EMA20 slope (normalizado)
        k     = 2 / 21
        ema   = closes[0]
        for c in closes[1:]:
            ema = c * k + ema * (1 - k)
        ema_prev = closes[-5] * k + closes[-6] * (1 - k)   # proxy
        ema_slope = (ema - ema_prev) / ema_prev * 100

        # Bollinger Band Width (20, 2σ)
        win20  = closes[-20:]
        mean20 = sum(win20) / 20
        std20  = _math.sqrt(sum((x - mean20) ** 2 for x in win20) / 20)
        bbw_pct = (4 * std20) / mean20 * 100   # upper-lower / mid * 100

        # Classificação do regime
        if atr_pct > 1.5 and abs(ema_slope) > 0.3:
            regime = "volatile"
        elif abs(ema_slope) >= 0.15 and atr_pct > 0.5:
            regime = "trending"
        else:
            regime = "ranging"

        trend_dir = "up" if ema_slope > 0.05 else "down" if ema_slope < -0.05 else "flat"

    except Exception as exc:
        log.warning("get_market_regime: %s", exc)
        # Fallback neutro
        return {
            "regime":    "unknown",
            "trend_dir": "flat",
            "session":   _session_now(),
            "atr_pct":   0.0,
            "bbw_pct":   0.0,
            "ema_slope": 0.0,
            "updated_at": _dt.datetime.utcnow().isoformat() + "Z",
            "error":     str(exc),
        }

    return {
        "regime":     regime,
        "trend_dir":  trend_dir,
        "session":    _session_now(),
        "atr_pct":    round(atr_pct, 3),
        "bbw_pct":    round(bbw_pct, 3),
        "ema_slope":  round(ema_slope, 4),
        "updated_at": _dt.datetime.utcnow().isoformat() + "Z",
    }

@router.get("/api/market/clock")
async def get_market_clock():
    """Retorna o estado actual do mercado: is_open, next_open, next_close."""
    import aiohttp as _aio_clk
    async with _aio_clk.ClientSession() as _sess_clk:
        exchange = build_exchange(_sess_clk)
    return await exchange.get_clock()


# ── Rotas: Mercado ────────────────────────────────────────────────────────────
@router.get("/api/market/symbol-tradability")
async def market_symbol_tradability(symbols: str, demo: bool = True):
    """Valida elegibilidade de negociação para uma lista de pares (CSV)."""
    raw = [s.strip() for s in (symbols or "").split(",") if s.strip()]
    if not raw:
        raise HTTPException(400, "Informe ao menos um símbolo em 'symbols' (CSV).")

    normalized: list[str] = []
    for s in raw:
        try:
            _assert_spot_only(s)
            normalized.append(_norm_symbol(s))
        except HTTPException as exc:
            normalized.append(_norm_symbol(s))

    unique_symbols = list(dict.fromkeys(normalized))
    statuses = await asyncio.gather(
        *[_get_symbol_tradability(sym, demo=demo) for sym in unique_symbols],
        return_exceptions=True,
    )

    rows: list[dict] = []
    for sym, st in zip(unique_symbols, statuses):
        if isinstance(st, Exception):
            rows.append({
                "symbol": sym,
                "tradable": True,
                "reason": f"Falha no precheck: {st}",
                "status": "unknown",
                "source": "exception",
            })
            continue
        rows.append(st)

    blocked = [r for r in rows if r.get("tradable") is False]
    return {
        "demo": bool(demo),
        "symbols": rows,
        "blocked_count": len(blocked),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

@router.get("/api/market/top-assets")
async def top_assets():
    """Top 5 pares SPOT X-USDT da OKX por volume em 24h (sem stablecoins)."""
    assets = await fetch_top_spot_assets()
    if not assets:
        raise HTTPException(503, "Não foi possível obter o ranking de volume da OKX")
    return {"assets": assets, "source": "okx_spot_tickers"}

@router.get("/api/market/rank-assets")
async def rank_assets(strategy_id: str = "graph_regime", timeframe: str = "15m"):
    """Classifica os 5 ativos por adequação à estratégia e timeframe atuais."""
    import pandas as pd
    import pandas_ta as ta

    top = await fetch_top_spot_assets()
    if not top:
        # Sem o ranking da OKX não há universo: nunca se usa uma lista fixa de reserva.
        raise HTTPException(503, "Ranking de ativos da OKX indisponível; não é permitido usar outra lista de ativos.")
    RANK_ASSETS = [a["symbol"] for a in top]
    bar = map_timeframe_for_history(timeframe)

    ohlcv: dict[str, list] = {}
    import aiohttp as _aio
    async with _aio.ClientSession() as session:
        ex = build_exchange(session)
        for symbol in RANK_ASSETS:
            try:
                candles = await ex.fetch_candles(symbol, bar, limit=100)
                rows = [[c.epoch, c.open, c.high, c.low, c.close, c.volume] for c in candles]
                if rows:
                    ohlcv[symbol] = rows
            except Exception as exc:
                log.warning("rank_assets: falha %s: %s", symbol, exc)

    if not ohlcv:
        raise HTTPException(503, "Não foi possível buscar dados de mercado")

    results: list[dict] = []
    graph_info: Optional[dict] = None
    warning: Optional[str] = None

    # ── Graph Regime: scoring por centralidade de eigenvector ─────────────────
    if strategy_id == "graph_regime":
        from ..graph import CorrelationGraphBuilder, compute_metrics, RegimeClassifier

        price_matrix = {s: [float(r[4]) for r in rows]
                        for s, rows in ohlcv.items()}
        G = CorrelationGraphBuilder().build(price_matrix, window=48, threshold=0.65)
        metrics = compute_metrics(G)
        regime_state = RegimeClassifier().classify(metrics)

        if "error" in metrics:
            warning = f"Grafo inválido: {metrics['error']} — sugestão indisponível."
            centrality_all: dict = {}
        else:
            centrality_all = metrics.get("centrality_all", {})

        for symbol in RANK_ASSETS:
            results.append({
                "symbol": symbol,
                "score":  round(centrality_all.get(symbol, 0.0), 4),
                "details": {"metric": "eigenvector_centrality"},
            })

        if not warning:
            density = metrics.get("graph_density", 0)
            graph_info = {
                "regime":      regime_state.name,
                "regime_conf": round(regime_state.confidence, 4),
                "density":     round(density, 4),
                "n_edges":     metrics.get("n_edges", 0),
            }
            if density < 0.25:
                warning = "Mercado fragmentado (chaos) — sugestão pouco confiável agora."
            elif regime_state.name == "transition":
                warning = "Regime em transição — aguarde estabilização antes de operar."

    # ── Pivot Sniper: scoring por estrutura de pivôs e proximidade à EMA50 ──────
    elif strategy_id == "pivot_sniper":
        # Os três factores que determinam a qualidade de um setup Pivot Sniper:
        #   ema50_slope    (40%) — inclinação da EMA50 em 20 barras / ATR: tendência activa
        #   pullback_prox  (40%) — distância do preço à EMA50: pullback iminente (1=na EMA)
        #   pivot_richness (20%) — nº de swing-points recentes: mais S/R potencial
        raw: dict[str, dict] = {}
        for symbol, rows in ohlcv.items():
            if len(rows) < 70:
                continue
            closes = [float(r[4]) for r in rows]
            highs  = [float(r[2]) for r in rows]
            lows   = [float(r[3]) for r in rows]
            df = pd.DataFrame({"close": closes, "high": highs, "low": lows})

            ema50 = ta.ema(df["close"], length=50)
            atr14 = ta.atr(df["high"], df["low"], df["close"], length=14)
            if ema50 is None or atr14 is None:
                continue

            import math as _math
            e_now    = float(ema50.iloc[-1])
            e_20ago  = float(ema50.iloc[-20])
            atr_now  = float(atr14.iloc[-1])
            cl_now   = closes[-1]
            if e_now == 0 or atr_now == 0 or _math.isnan(e_now) or _math.isnan(e_20ago) or _math.isnan(atr_now):
                continue

            # Inclinação da EMA50 em 20 barras, normalizada pelo ATR
            ema_slope = abs(e_now - e_20ago) / atr_now

            # Proximidade ao pullback: 1.0 = price na EMA, 0 = dist >= 2×ATR
            pullback_prox = max(0.0, 1.0 - abs(cl_now - e_now) / (atr_now * 2.0))

            # Riqueza de pivôs: contagem de swing-highs + swing-lows nas últimas 50 velas
            lb = 4
            win_h = highs[-50:];  win_l = lows[-50:]
            n_w = len(win_h)
            pivot_count = sum(
                1 for i in range(lb, n_w - lb)
                if all(win_h[j] <= win_h[i] for j in range(i - lb, i + lb + 1) if j != i)
                or all(win_l[j] >= win_l[i] for j in range(i - lb, i + lb + 1) if j != i)
            )
            raw[symbol] = {
                "ema_slope":    ema_slope,
                "pullback_prox": pullback_prox,
                "pivot_count":  pivot_count,
            }

        def _norm(vals: list[float]) -> list[float]:
            mn, mx = min(vals), max(vals)
            return [(v - mn) / (mx - mn) if mx > mn else 0.5 for v in vals]

        syms = list(raw.keys())
        if syms:
            n_slope  = _norm([raw[s]["ema_slope"]     for s in syms])
            n_pull   = [raw[s]["pullback_prox"] for s in syms]   # já em [0,1]
            n_pivots = _norm([raw[s]["pivot_count"]   for s in syms])
            for i, symbol in enumerate(syms):
                score = 0.40 * n_slope[i] + 0.40 * n_pull[i] + 0.20 * n_pivots[i]
                r = raw[symbol]
                results.append({
                    "symbol": symbol,
                    "score":  round(score, 4),
                    "details": {
                        "metric":          "pivot_sniper",
                        "ema50_slope_atr": round(r["ema_slope"],    3),
                        "pullback_prox":   round(r["pullback_prox"],3),
                        "pivot_count":     r["pivot_count"],
                    },
                })

        for symbol in RANK_ASSETS:
            if symbol not in {r["symbol"] for r in results}:
                results.append({
                    "symbol": symbol, "score": 0.0,
                    "details": {"metric": "pivot_sniper", "error": "dados insuficientes"},
                })

    # ── Three Line Bar: scoring por formação de padrão e condição de sobrevenda ─
    elif strategy_id == "three_line_bar" or strategy_id == "S005":
        raw: dict[str, dict] = {}
        for symbol, rows in ohlcv.items():
            if len(rows) < 220:
                continue
            closes = [float(r[4]) for r in rows]
            highs  = [float(r[2]) for r in rows]
            lows   = [float(r[3]) for r in rows]
            df = pd.DataFrame({"close": closes, "high": highs, "low": lows})

            ema200 = ta.ema(df["close"], length=200)
            rsi14  = ta.rsi(df["close"], length=14)
            if ema200 is None or rsi14 is None or pd.isna(ema200.iloc[-1]) or pd.isna(rsi14.iloc[-1]):
                continue

            ema200_v = float(ema200.iloc[-1])
            rsi_v    = float(rsi14.iloc[-1])
            cl       = closes[-1]
            hi       = highs[-1]
            lo       = lows[-1]
            hi2      = highs[-2]
            lo2      = lows[-2]
            hi3      = highs[-3]
            lo3      = lows[-3]

            # Verifica padrão 3LB: C1 (minima mais baixa), C2 > C1, C3 > C2
            c1_low   = lo3
            c2_high  = hi2
            c3_high  = hi

            # C1 é a mínima mais baixa dos últimos 5 candles?
            recent_lows = lows[-6:] if len(lows) >= 6 else lows
            c1_is_lowest = c1_low <= min(recent_lows)

            # C2 > C1 e C3 > C2?
            pattern_ok = c2_high > max(hi3, lo3 * 1.001) and c3_high > c2_high and c1_is_lowest

            # RSI < 30?
            rsi_oversold = rsi_v < 30

            # Preço acima ou abaixo da EMA200?
            in_uptrend = cl > ema200_v

            raw[symbol] = {
                "pattern_ok": int(pattern_ok),
                "rsi_oversold": int(rsi_oversold),
                "in_uptrend": int(in_uptrend),
                "rsi": rsi_v,
                "c3_high": c3_high,
                "c1_low": c1_low,
            }

        syms = list(raw.keys())
        if syms:
            for symbol in syms:
                r = raw[symbol]
                if r["pattern_ok"] and r["rsi_oversold"]:
                    score = 0.9 if not r["in_uptrend"] else 0.6
                elif r["pattern_ok"]:
                    score = 0.7 if not r["in_uptrend"] else 0.5
                else:
                    # Pontua parcialmente se C1-C2 se formaram
                    score = 0.2 if r["c3_high"] > 0 else 0.0

                results.append({
                    "symbol": symbol,
                    "score":  round(score, 4),
                    "details": {
                        "metric": "three_line_bar",
                        "pattern_ok": bool(r["pattern_ok"]),
                        "rsi_oversold": bool(r["rsi_oversold"]),
                        "in_uptrend": bool(r["in_uptrend"]),
                        "rsi": round(r["rsi"], 1),
                    },
                })

        for symbol in RANK_ASSETS:
            if symbol not in {r["symbol"] for r in results}:
                results.append({
                    "symbol": symbol, "score": 0.0,
                    "details": {"metric": "three_line_bar", "error": "dados insuficientes"},
                })

    # ── Stochastic Reversal: scoring por alinhamento recente e zona de pullback ─
    elif strategy_id == "stochastic_reversal":
        raw: dict[str, dict] = {}
        for symbol, rows in ohlcv.items():
            if len(rows) < 150:
                continue
            closes = [float(r[4]) for r in rows]
            highs  = [float(r[2]) for r in rows]
            lows   = [float(r[3]) for r in rows]
            df = pd.DataFrame({"close": closes, "high": highs, "low": lows})

            ema21 = ta.ema(df["close"], length=21)
            ema34 = ta.ema(df["close"], length=34)
            ema144 = ta.ema(df["close"], length=144)
            stoch = ta.stoch(df["high"], df["low"], df["close"], k=7, d=3, smooth_k=3)

            if ema144 is None or stoch is None or ema144.isna().iloc[-1]:
                continue
                
            k_col = [c for c in stoch.columns if 'STOCHk' in c][0]
            stoch_k = float(stoch[k_col].iloc[-1])

            cl = closes[-1]
            e21 = float(ema21.iloc[-1])
            e34 = float(ema34.iloc[-1])
            e144 = float(ema144.iloc[-1])
            
            aligned_long = (e21 > e34 > e144) and (cl > e144)
            aligned_short = (e21 < e34 < e144) and (cl < e144)
            
            is_aligned = aligned_long or aligned_short
            
            # Quão perto está da zona de reversão/pullback
            stoch_score = 0.0
            if aligned_long and stoch_k < 30:
                stoch_score = 1.0 - (stoch_k / 30.0) # Mais perto de 0 = maior score
            elif aligned_short and stoch_k > 70:
                stoch_score = (stoch_k - 70.0) / 30.0 # Mais perto de 100 = maior score
                
            # Proximidade à EMA 144 (queremos que esteja o mais perto possível para risco baixo)
            dist_ema144 = abs(cl - e144) / e144
            prox_score = max(0.0, 1.0 - (dist_ema144 / 0.05)) # Decai em 5%
            
            raw[symbol] = {
                "aligned": 1.0 if is_aligned else 0.0,
                "stoch_score": stoch_score,
                "prox_score": prox_score
            }

        syms = list(raw.keys())
        if syms:
            for symbol in syms:
                r = raw[symbol]
                score = (r["aligned"] * 0.4) + (r["stoch_score"] * 0.4) + (r["prox_score"] * 0.2)
                results.append({
                    "symbol": symbol,
                    "score":  round(score, 4),
                    "details": {
                        "metric": "stochastic_pullback",
                        "aligned": r["aligned"] > 0,
                        "stoch_pullback": round(r["stoch_score"], 2)
                    },
                })

        for symbol in RANK_ASSETS:
            if symbol not in {r["symbol"] for r in results}:
                results.append({
                    "symbol": symbol, "score": 0.0,
                    "details": {"metric": "stochastic_pullback", "error": "dados insuficientes"},
                })

    # ── Demais estratégias: scoring por tendência, ATR e momentum ────────────
    else:
        raw: dict[str, dict] = {}
        for symbol, rows in ohlcv.items():
            if len(rows) < 30:
                continue
            closes = [float(r[4]) for r in rows]
            highs  = [float(r[2]) for r in rows]
            lows   = [float(r[3]) for r in rows]
            df = pd.DataFrame({"close": closes, "high": highs, "low": lows})

            atr_s = ta.atr(df["high"], df["low"], df["close"], length=14)
            ema21 = ta.ema(df["close"], length=21)
            ema55 = ta.ema(df["close"], length=55)
            if atr_s is None or ema21 is None or ema55 is None:
                continue

            cl  = closes[-1]
            e21 = float(ema21.iloc[-1])
            e55 = float(ema55.iloc[-1])
            raw[symbol] = {
                "atr_pct":        float(atr_s.iloc[-1]) / cl,
                "trend_strength": abs(e21 - e55) / e55,
                "momentum":       abs(cl / closes[-20] - 1) if len(closes) >= 20 else 0.0,
            }

        def _norm(vals: list[float]) -> list[float]:
            mn, mx = min(vals), max(vals)
            return [(v - mn) / (mx - mn) if mx > mn else 0.5 for v in vals]

        syms = list(raw.keys())
        if syms:
            n_atr   = _norm([raw[s]["atr_pct"]        for s in syms])
            n_trend = _norm([raw[s]["trend_strength"]  for s in syms])
            n_mom   = _norm([raw[s]["momentum"]        for s in syms])
            for i, symbol in enumerate(syms):
                score = 0.40 * n_atr[i] + 0.40 * n_trend[i] + 0.20 * n_mom[i]
                r = raw[symbol]
                results.append({
                    "symbol": symbol,
                    "score":  round(score, 4),
                    "details": {
                        "metric":             "trend_momentum",
                        "atr_pct":            round(r["atr_pct"] * 100, 2),
                        "trend_strength_pct": round(r["trend_strength"] * 100, 2),
                        "momentum_pct":       round(r["momentum"] * 100, 2),
                    },
                })

        for symbol in RANK_ASSETS:
            if symbol not in {r["symbol"] for r in results}:
                results.append({
                    "symbol": symbol, "score": 0.0,
                    "details": {"metric": "trend_momentum", "error": "dados insuficientes"},
                })

    # ── Ranking final ─────────────────────────────────────────────────────────
    results.sort(key=lambda x: x["score"], reverse=True)
    rank_labels = {1: "Excelente", 2: "Bom", 3: "Moderado", 4: "Fraco", 5: "Fraco"}
    top_info = {a["symbol"]: a for a in top}
    for i, r in enumerate(results):
        r["rank"]  = i + 1
        r["label"] = rank_labels.get(i + 1, "Fraco")
        info = top_info.get(r["symbol"])
        if info:
            r["volume_usd_24h"] = info["volume_usd_24h"]
            r["change_24h_pct"] = info["change_24h_pct"]

    return {
        "strategy_id": strategy_id,
        "timeframe":   timeframe,
        "assets":      results,
        "graph_info":  graph_info,
        "warning":     warning,
        "note": {
            "graph_regime":  "Ranking por centralidade de eigenvector no grafo de correlação.",
            "pivot_sniper":  "Ranking por inclinação EMA50, proximidade ao pullback e riqueza de pivôs.",
            "whale_flow_regime": "Ranking por volatilidade ATR, força de tendência e agressão de volume.",
        }.get(strategy_id, "Ranking por volatilidade ATR, força de tendência e momentum recente."),
            "three_line_bar": "Ranking por formação do padrão 3LB (C1 < C2 < C3), RSI sobrevenda e contexto de tendência.",
            "S005": "Ranking por formação do padrão 3LB (C1 < C2 < C3), RSI sobrevenda e contexto de tendência.",
    }

@router.get("/api/market/candles")
async def market_candles(symbol: str = "BTC-USDT",
                         timeframe: str = "15m",
                         limit: int = 200):
    import aiohttp as _aiohttp
    bar = map_timeframe_for_history(timeframe)
    async with _aiohttp.ClientSession() as s:
        ex = build_exchange(s)
        try:
            candles = await ex.fetch_candles(symbol, bar, limit=limit)
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc
        # Mantém compatibilidade com o formato de resposta anterior.
        data = [
            [
                str(c.epoch),
                str(c.open),
                str(c.high),
                str(c.low),
                str(c.close),
                str(c.volume),
                "0",
                "0",
                "1",
            ]
            for c in reversed(candles)
        ]
        return {"code": "0", "msg": "", "data": data}

@router.get("/api/market/ticker")
async def market_ticker(symbol: str = "BTC-USDT"):
    import aiohttp as _aiohttp
    async with _aiohttp.ClientSession() as s:
        ex = build_exchange(s)
        try:
            ticker = await ex.get_ticker(symbol)
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"code": "0", "msg": "", "data": [ticker]}
