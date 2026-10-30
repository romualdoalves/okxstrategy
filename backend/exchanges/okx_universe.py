"""Universo dinâmico de ativos SPOT: top N pares X-USDT por volume em 24h na OKX."""

from __future__ import annotations

import logging
import time

import aiohttp

from .okx import OKX_BASE

log = logging.getLogger("okx_universe")

TOP_N = 5
QUOTE = "USDT"
CACHE_TTL_S = 120

# Pares que não fazem sentido para estratégias direcionais: stablecoins (sem
# tendência) e tokens lastreados em ouro (replicam outro mercado).
EXCLUDED_BASES = {
    "USDC", "USDG", "DAI", "TUSD", "FDUSD", "PYUSD", "USDD", "USDE", "USD1",
    "EURC", "EURT", "XAUT", "PAXG",
}

_cache: dict = {"ts": 0.0, "assets": []}


async def fetch_top_spot_assets(n: int = TOP_N) -> list[dict]:
    """Retorna os n pares SPOT X-USDT de maior volume (em USDT) nas últimas 24h.

    Cada item: {"symbol", "volume_usd_24h", "change_24h_pct", "last"}.
    Usa cache curto; em falha da API devolve o último resultado válido (ou []).
    """
    now = time.monotonic()
    if _cache["assets"] and now - _cache["ts"] < CACHE_TTL_S and len(_cache["assets"]) >= n:
        return _cache["assets"][:n]

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{OKX_BASE}/api/v5/market/tickers?instType=SPOT",
                             timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
        if data.get("code") != "0":
            raise RuntimeError(data.get("msg") or "resposta inválida da OKX")
    except Exception as exc:
        log.warning("fetch_top_spot_assets: falha na OKX: %s", exc)
        return _cache["assets"][:n]

    rows: list[dict] = []
    suffix = f"-{QUOTE}"
    for t in data.get("data", []):
        inst = t.get("instId", "")
        if not inst.endswith(suffix):
            continue
        base = inst[: -len(suffix)]
        if base in EXCLUDED_BASES:
            continue
        try:
            last = float(t["last"])
            open24 = float(t["open24h"])
            vol_usd = float(t["volCcy24h"])
        except (KeyError, ValueError):
            continue
        rows.append({
            "symbol": inst,
            "volume_usd_24h": vol_usd,
            "change_24h_pct": round((last / open24 - 1) * 100, 2) if open24 else 0.0,
            "last": last,
        })

    rows.sort(key=lambda r: r["volume_usd_24h"], reverse=True)
    if rows:
        _cache["ts"] = now
        _cache["assets"] = rows[:max(n, TOP_N)]
    return rows[:n]
