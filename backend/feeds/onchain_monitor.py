"""
Free on-chain flow monitor used by signal-only whale strategies.

The monitor intentionally avoids paid whale-alert providers. It uses:
- mempool.space public API for recent BTC mempool transactions.
- Etherscan API V2 for optional EVM token transfers to known exchange wallets.

It classifies events conservatively. Unknown destinations are never treated as
exchange inflows.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import aiohttp

from ..exchanges.factory import build_exchange

log = logging.getLogger("onchain_monitor")


_MEMPOOL_BASE = "https://mempool.space/api"
_ETHERSCAN_BASE = "https://api.etherscan.io/v2/api"
_ADDRESS_FILE = Path(__file__).resolve().parents[1] / "data" / "exchange_addresses.json"


@dataclass
class OnchainEvent:
    source: str
    chain: str
    symbol: str
    txid: str
    amount_native: float
    amount_usd: float
    direction: str
    confidence: str
    from_address: str = ""
    to_address: str = ""
    exchange: str | None = None
    timestamp: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OnchainMonitor:
    """Small polling monitor with in-memory dedupe and cooldown."""

    def __init__(self):
        self._seen: set[str] = set()
        self._last_poll: dict[str, float] = {}
        self._last_events: dict[str, list[dict[str, Any]]] = {}
        self._exchange_addresses = self._load_exchange_addresses()

    def _load_exchange_addresses(self) -> dict[str, Any]:
        try:
            return json.loads(_ADDRESS_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            log.warning("Exchange address file not found: %s", _ADDRESS_FILE)
        except Exception as exc:
            log.warning("Could not load exchange address file: %s", exc)
        return {"okx": {"ethereum": [], "bitcoin": []}}

    async def update(
        self,
        session: aiohttp.ClientSession,
        *,
        symbol: str,
        min_value_usd: float = 1_000_000,
        cooldown_seconds: int = 60,
        monitor_btc: bool = True,
        monitor_evm: bool = False,
    ) -> list[dict[str, Any]]:
        now = time.time()
        cache_key = symbol
        if now - self._last_poll.get(cache_key, 0) < cooldown_seconds:
            return self._last_events.get(cache_key, [])

        events: list[OnchainEvent] = []
        base_asset = symbol.split("-")[0].upper()

        if monitor_btc and base_asset == "BTC":
            events.extend(await self._fetch_btc_recent(session, min_value_usd))

        if monitor_evm and base_asset in {"ETH", "USDT", "USDC"}:
            events.extend(await self._fetch_evm_exchange_inflows(
                session, base_asset, min_value_usd))

        self._last_poll[cache_key] = now
        self._last_events[cache_key] = [e.to_dict() for e in events]
        return self._last_events[cache_key]

    async def _fetch_btc_recent(
        self,
        session: aiohttp.ClientSession,
        min_value_usd: float,
    ) -> list[OnchainEvent]:
        try:
            btc_usd = await self._market_price(session, "BTC-USDT")
            async with session.get(
                f"{_MEMPOOL_BASE}/mempool/recent",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                rows = await resp.json()
        except Exception as exc:
            log.debug("BTC on-chain monitor failed: %s", exc)
            return []

        events: list[OnchainEvent] = []
        for row in rows or []:
            txid = str(row.get("txid", ""))
            if not txid or txid in self._seen:
                continue
            value_btc = float(row.get("value") or 0) / 100_000_000
            value_usd = value_btc * btc_usd
            if value_usd < min_value_usd:
                continue

            self._seen.add(txid)
            events.append(OnchainEvent(
                source="mempool.space",
                chain="bitcoin",
                symbol="BTC",
                txid=txid,
                amount_native=value_btc,
                amount_usd=value_usd,
                direction="large_transfer",
                confidence="low",
                timestamp=int(time.time()),
            ))
        return events

    async def _fetch_evm_exchange_inflows(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        min_value_usd: float,
    ) -> list[OnchainEvent]:
        api_key = os.getenv("ETHERSCAN_API_KEY", "").strip()
        if not api_key:
            return []

        exchange_addresses = {
            a.lower()
            for a in self._exchange_addresses.get("okx", {}).get("ethereum", [])
            if isinstance(a, str)
        }
        if not exchange_addresses:
            return []

        events: list[OnchainEvent] = []
        for address in exchange_addresses:
            try:
                rows = await self._etherscan_token_transfers(session, api_key, address)
            except Exception as exc:
                log.debug("Etherscan monitor failed for %s: %s", address, exc)
                continue

            for row in rows:
                txid = str(row.get("hash", ""))
                to_addr = str(row.get("to", "")).lower()
                if not txid or txid in self._seen or to_addr not in exchange_addresses:
                    continue

                token_symbol = str(row.get("tokenSymbol", "")).upper()
                if symbol != token_symbol:
                    continue

                decimals = int(row.get("tokenDecimal") or 18)
                amount = float(row.get("value") or 0) / (10 ** decimals)
                price = 1.0 if token_symbol in {"USDT", "USDC"} else await self._market_price(
                    session, f"{token_symbol}-USDT")
                amount_usd = amount * price
                if amount_usd < min_value_usd:
                    continue

                self._seen.add(txid)
                events.append(OnchainEvent(
                    source="etherscan",
                    chain="ethereum",
                    symbol=token_symbol,
                    txid=txid,
                    amount_native=amount,
                    amount_usd=amount_usd,
                    direction="exchange_inflow",
                    confidence="high",
                    from_address=str(row.get("from", "")).lower(),
                    to_address=to_addr,
                    exchange="okx",
                    timestamp=int(row.get("timeStamp") or time.time()),
                ))
        return events

    async def _etherscan_token_transfers(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        address: str,
    ) -> list[dict[str, Any]]:
        params = {
            "chainid": "1",
            "module": "account",
            "action": "tokentx",
            "address": address,
            "page": "1",
            "offset": "25",
            "sort": "desc",
            "apikey": api_key,
        }
        async with session.get(
            _ETHERSCAN_BASE,
            params=params,
            timeout=aiohttp.ClientTimeout(total=12),
        ) as resp:
            data = await resp.json()
        if data.get("status") != "1":
            return []
        return data.get("result", [])

    async def _market_price(self, session: aiohttp.ClientSession, inst_id: str) -> float:
        ex = build_exchange(session)
        ticker = await ex.get_ticker(inst_id)
        return float(ticker.get("last") or ticker.get("close") or 0)


onchain_monitor = OnchainMonitor()
