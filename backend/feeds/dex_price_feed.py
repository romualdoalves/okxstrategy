"""
feeds/dex_price_feed.py — Agregador de preços cross-DEX/CEX para arbitragem.

Busca preços de múltiplas fontes públicas (CoinGecko, DexScreener) e
normaliza para comparação.  Todas as APIs usadas são gratuitas e não
exigem chave obrigatória.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import aiohttp

log = logging.getLogger("dex_price_feed")


# ── Constantes ───────────────────────────────────────────────────────────────

_COINGECKO_BASE = "https://api.coingecko.com/api/v3"
_DEXSCREENER_BASE = "https://api.dexscreener.com"

# Mapeamento de símbolos OKX → id CoinGecko
_CG_ID_MAP: dict[str, str] = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "LTC": "litecoin", "BCH": "bitcoin-cash", "UNI": "uniswap",
    "LINK": "chainlink", "DOGE": "dogecoin", "XRP": "ripple",
    "BNB": "binancecoin", "ADA": "cardano", "AVAX": "avalanche-2",
    "MATIC": "matic-network", "DOT": "polkadot", "ARB": "arbitrum",
    "OP": "optimism", "AAVE": "aave", "MKR": "maker",
}


@dataclass
class PriceSource:
    """Preço normalizado de uma única fonte."""
    source: str          # nome da fonte, ex: "coingecko", "uniswap_v3"
    price: float         # preço em USD
    bid: Optional[float] = None
    ask: Optional[float] = None
    liquidity_usd: Optional[float] = None
    timestamp: int = field(default_factory=lambda: int(time.time()))
    raw: dict = field(default_factory=dict)


@dataclass
class SpreadOpportunity:
    """Uma oportunidade de arbitragem entre duas fontes."""
    buy_source: PriceSource
    sell_source: PriceSource
    spread_pct: float           # spread bruto (%)
    net_spread_pct: float       # spread após fees estimados (%)
    estimated_profit_usd: float # lucro estimado para stake padrão


class DexPriceFeed:
    """
    Agregador de preços multi-fonte para detecção de arbitragem.

    Uso:
        feed = DexPriceFeed()
        prices = await feed.fetch_prices(session, "BTC-USDT")
        spreads = feed.calculate_spreads(prices, min_spread_pct=0.3)
    """

    def __init__(
        self,
        min_liquidity_usd: float = 100_000.0,
        slippage_pct: float = 0.1,
        gas_cost_usd: float = 5.0,
        cooldown_seconds: int = 30,
    ):
        self.min_liquidity_usd = min_liquidity_usd
        self.slippage_pct = slippage_pct
        self.gas_cost_usd = gas_cost_usd
        self.cooldown_seconds = cooldown_seconds
        self._last_poll: dict[str, float] = {}
        self._cache: dict[str, dict[str, Any]] = {}

    # ── Público ──────────────────────────────────────────────────────────────

    async def fetch_prices(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
    ) -> dict[str, Any]:
        """
        Retorna dict com preços por fonte:
        {
            "sources": [PriceSource, ...],
            "okx":  {"bid": x, "ask": y, "last": z},
            "timestamp": 1234567890,
        }
        """
        cache_key = symbol.upper()
        now = time.time()
        if now - self._last_poll.get(cache_key, 0) < self.cooldown_seconds:
            return self._cache.get(cache_key, self._empty_result())

        base_asset = self._extract_base(symbol)
        sources: list[PriceSource] = []

        # 1. CoinGecko (CEX spot aggregate)
        cg = await self._fetch_coingecko(session, base_asset)
        if cg:
            sources.append(cg)

        # 2. DexScreener (pools DEX na Ethereum mainnet)
        dex = await self._fetch_dexscreener(session, base_asset)
        sources.extend(dex)

        result = {
            "sources": sources,
            "okx": None,   # preenchido pelo BotManager se disponível
            "timestamp": int(now),
        }

        self._cache[cache_key] = result
        self._last_poll[cache_key] = now
        return result

    def set_okx_price(self, symbol: str, bid: float, ask: float, last: float) -> None:
        """Permite ao BotManager injetar o preço OKX para comparação."""
        cache_key = symbol.upper()
        if cache_key not in self._cache:
            self._cache[cache_key] = self._empty_result()
        self._cache[cache_key]["okx"] = {
            "bid": bid,
            "ask": ask,
            "last": last,
        }

    def calculate_spreads(
        self,
        prices: dict[str, Any],
        min_spread_pct: float = 0.3,
        stake_usd: float = 1_000.0,
    ) -> list[SpreadOpportunity]:
        """
        Calcula todos os pares de spread possíveis entre as fontes.
        Retorna lista ordenada pelo spread líquido (maior primeiro).
        """
        sources: list[PriceSource] = list(prices.get("sources", []))
        okx = prices.get("okx")

        # Injeta OKX como fonte se disponível
        if okx:
            sources.append(PriceSource(
                source="okx",
                price=okx.get("last") or okx.get("ask") or okx.get("bid") or 0.0,
                bid=okx.get("bid"),
                ask=okx.get("ask"),
                liquidity_usd=999_999_999.0,  # assume liquidez infinita na CEX
            ))

        if len(sources) < 2:
            return []

        opportunities: list[SpreadOpportunity] = []

        for i, buy_src in enumerate(sources):
            for j, sell_src in enumerate(sources):
                if i == j:
                    continue

                # Preço de compra (usamos ask se disponível, senão price)
                buy_price = buy_src.ask if buy_src.ask else buy_src.price
                # Preço de venda (usamos bid se disponível, senão price)
                sell_price = sell_src.bid if sell_src.bid else sell_src.price

                if buy_price <= 0 or sell_price <= 0:
                    continue

                spread_pct = (sell_price - buy_price) / buy_price * 100.0
                if spread_pct < min_spread_pct:
                    continue

                # Liquidez mínima
                if (buy_src.liquidity_usd or 0) < self.min_liquidity_usd:
                    continue
                if (sell_src.liquidity_usd or 0) < self.min_liquidity_usd:
                    continue

                # Spread líquido
                fee_cost = self.slippage_pct * 2  # compra + venda
                gas_cost_pct = (self.gas_cost_usd / stake_usd) * 100 if stake_usd > 0 else 0
                net_spread_pct = spread_pct - fee_cost - gas_cost_pct

                if net_spread_pct <= 0:
                    continue

                est_profit = (net_spread_pct / 100.0) * stake_usd

                opportunities.append(SpreadOpportunity(
                    buy_source=buy_src,
                    sell_source=sell_src,
                    spread_pct=round(spread_pct, 4),
                    net_spread_pct=round(net_spread_pct, 4),
                    estimated_profit_usd=round(est_profit, 2),
                ))

        opportunities.sort(key=lambda x: x.net_spread_pct, reverse=True)
        return opportunities

    # ── Interno: CoinGecko ───────────────────────────────────────────────────

    async def _fetch_coingecko(
        self,
        session: aiohttp.ClientSession,
        base_asset: str,
    ) -> Optional[PriceSource]:
        cg_id = _CG_ID_MAP.get(base_asset.upper())
        if not cg_id:
            log.debug("CoinGecko: id não mapeado para %s", base_asset)
            return None

        url = f"{_COINGECKO_BASE}/simple/price"
        params = {"ids": cg_id, "vs_currencies": "usd", "include_24hr_vol": "true"}
        try:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 429:
                    log.warning("CoinGecko rate limit atingido")
                    return None
                if resp.status >= 400:
                    log.debug("CoinGecko erro %s", resp.status)
                    return None
                data = await resp.json()
                entry = data.get(cg_id, {})
                price = entry.get("usd")
                if price is None:
                    return None
                return PriceSource(
                    source="coingecko",
                    price=float(price),
                    liquidity_usd=None,  # CoinGecko simple/price não retorna liquidez
                    raw=entry,
                )
        except Exception as exc:
            log.debug("CoinGecko falhou: %s", exc)
            return None

    # ── Interno: DexScreener ─────────────────────────────────────────────────

    async def _fetch_dexscreener(
        self,
        session: aiohttp.ClientSession,
        base_asset: str,
    ) -> list[PriceSource]:
        """Busca pools na Ethereum mainnet via DexScreener."""
        # Formato de busca: ex "BTC" ou "ETH"
        query = f"{base_asset.upper()} USDC"
        url = f"{_DEXSCREENER_BASE}/latest/dex/search"
        try:
            async with session.get(url, params={"q": query}, timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status >= 400:
                    log.debug("DexScreener erro %s", resp.status)
                    return []
                data = await resp.json()
        except Exception as exc:
            log.debug("DexScreener falhou: %s", exc)
            return []

        sources: list[PriceSource] = []
        pairs = data.get("pairs", [])
        if not pairs:
            return []

        for pair in pairs:
            # Filtra apenas Ethereum mainnet e pares contra stablecoins
            chain = (pair.get("chainId") or "").lower()
            if chain != "ethereum":
                continue

            quote = (pair.get("quoteToken", {}).get("symbol") or "").upper()
            if quote not in {"USDC", "USDT", "DAI"}:
                continue

            price_str = pair.get("priceUsd")
            if price_str is None:
                continue
            try:
                price = float(price_str)
            except (ValueError, TypeError):
                continue

            liq = pair.get("liquidity", {}).get("usd")
            liquidity_usd = float(liq) if liq else 0.0

            # Ignora pools sem liquidez suficiente
            if liquidity_usd < self.min_liquidity_usd:
                continue

            dex = pair.get("dexId", "unknown_dex")
            sources.append(PriceSource(
                source=f"dexscreener:{dex}",
                price=price,
                liquidity_usd=liquidity_usd,
                raw=pair,
            ))

        # Dedup por DEX: mantém o pool com maior liquidez de cada dex
        best_by_dex: dict[str, PriceSource] = {}
        for src in sources:
            dex_name = src.source.split(":", 1)[1]
            if dex_name not in best_by_dex or (src.liquidity_usd or 0) > (best_by_dex[dex_name].liquidity_usd or 0):
                best_by_dex[dex_name] = src

        return list(best_by_dex.values())

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_base(symbol: str) -> str:
        """Extrai o asset base de símbolos como 'BTC/USD', 'BTC-USDT', 'BTCUSD'."""
        s = symbol.upper().replace("/", "-").replace(".", "-")
        for sep in ("-", "USDT", "USD", "EUR", "BRL"):
            if sep in s:
                return s.split(sep)[0]
        return s

    @staticmethod
    def _empty_result() -> dict[str, Any]:
        return {"sources": [], "okx": None, "timestamp": 0}
