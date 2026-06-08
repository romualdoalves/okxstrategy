from __future__ import annotations

import os
from typing import Optional, Type

import aiohttp

from .base import BaseExchange


def get_exchange_provider() -> str:
    provider = os.getenv("EXCHANGE_PROVIDER", "okx").lower()
    if provider != "okx":
        raise RuntimeError("OKXStrategy permite somente EXCHANGE_PROVIDER=okx.")
    return provider


def get_default_demo_mode() -> bool:
    return True


def map_timeframe_for_history(timeframe: str) -> str:
    tf_map = {
        "1m": "1m", "5m": "5m", "15m": "15m",
        "1h": "1H", "4h": "4H", "1D": "1D",
    }
    return tf_map.get(timeframe, "15m")


def map_timeframe_for_ws_channel(timeframe: str) -> str:
    return map_timeframe_for_history(timeframe)


def get_ranked_assets_universe() -> list[str]:
    get_exchange_provider()
    return ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT",
            "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "DOT-USDT", "LINK-USDT"]


def build_exchange(
    session: aiohttp.ClientSession,
    *,
    demo: Optional[bool] = None,
) -> BaseExchange:
    provider = get_exchange_provider()
    if provider == "okx":
        from .okx import OKXExchange
        return OKXExchange()
    raise RuntimeError("OKXStrategy permite somente exchange OKX.")


def get_public_stream_class() -> Type:
    provider = get_exchange_provider()
    if provider == "okx":
        from .okx import OKXStream
        return OKXStream
    raise RuntimeError("OKXStrategy permite somente stream OKX.")


def get_private_stream_class() -> Type:
    provider = get_exchange_provider()
    if provider == "okx":
        from .okx import OKXPrivateStream
        return OKXPrivateStream
    raise RuntimeError("OKXStrategy permite somente stream privado OKX.")


async def get_exchange_maintenance(session: aiohttp.ClientSession) -> Optional[dict]:
    return None
