"""
Public OKX market-player sentiment feed.

Uses Trading Statistics (Rubik) endpoints to compare the broad long/short
account ratio with top-trader position ratios.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, asdict
from typing import Any

log = logging.getLogger("okx_market_players")


@dataclass(frozen=True)
class MarketPlayersSnapshot:
    inst_id: str
    ccy: str
    ts: int
    retail_long_short_ratio: float
    top_long_ratio: float
    top_short_ratio: float
    pressure: float
    scenario: str
    confirms_buy: bool
    confirms_sell: bool
    age_sec: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OkxMarketPlayersFeed:
    BASE_URL = "https://www.okx.com"
    TTL_SEC = 300

    def __init__(self) -> None:
        self._cache: dict[str, MarketPlayersSnapshot] = {}

    async def fetch(self, session, inst_id: str) -> dict[str, Any] | None:
        ccy = self._currency(inst_id)
        now = int(time.time())
        cached = self._cache.get(ccy)
        if cached and now - int(cached.ts / 1000) < self.TTL_SEC:
            data = cached.to_dict()
            data["age_sec"] = max(0, now - int(cached.ts / 1000))
            return data

        try:
            ratio_payload, top_payload = await self._fetch_pair(session, ccy)
            snapshot = self._build_snapshot(inst_id, ccy, ratio_payload, top_payload)
            if snapshot:
                self._cache[ccy] = snapshot
                return snapshot.to_dict()
        except Exception as exc:
            log.warning("OKX market players feed failed for %s: %s", inst_id, exc)
        return None

    async def _fetch_pair(self, session, ccy: str) -> tuple[dict, dict]:
        headers = {"Content-Type": "application/json"}
        ratio_url = (
            f"{self.BASE_URL}/api/v5/rubik/stat/contracts/"
            f"long-short-account-ratio?ccy={ccy}"
        )
        top_url = (
            f"{self.BASE_URL}/api/v5/rubik/stat/contracts/"
            f"top-traders-position-ratio?ccy={ccy}"
        )

        async with session.get(ratio_url, headers=headers, timeout=10) as response:
            ratio_payload = await response.json(content_type=None)
        async with session.get(top_url, headers=headers, timeout=10) as response:
            top_payload = await response.json(content_type=None)
        return ratio_payload, top_payload

    def _build_snapshot(
        self,
        inst_id: str,
        ccy: str,
        ratio_payload: dict,
        top_payload: dict,
    ) -> MarketPlayersSnapshot | None:
        if ratio_payload.get("code") != "0" or top_payload.get("code") != "0":
            log.warning(
                "OKX market players non-zero response for %s: ratio=%s top=%s",
                ccy,
                ratio_payload.get("code"),
                top_payload.get("code"),
            )
            return None

        ratio_rows = ratio_payload.get("data") or []
        top_rows = top_payload.get("data") or []
        if not ratio_rows or not top_rows:
            return None

        retail_row = ratio_rows[0]
        top_row = top_rows[0]
        retail_ratio = float(retail_row.get("ratio") or 0)
        top_long = float(top_row.get("longRatio") or 0)
        top_short = float(top_row.get("shortRatio") or 0)
        ts = int(retail_row.get("ts") or top_row.get("ts") or int(time.time() * 1000))

        confirms_sell = retail_ratio > 1.4 and top_short > 0.55
        confirms_buy = retail_ratio < 0.8 and top_long > 0.55
        scenario = "balanced"
        if confirms_sell:
            scenario = "retail_long_top_short"
        elif confirms_buy:
            scenario = "retail_short_top_long"

        # Positive pressure means trapped/overexposed longs; negative means
        # trapped/overexposed shorts. Viana uses the losing side as fuel.
        if confirms_sell:
            pressure = retail_ratio - 1.0 + (top_short - top_long)
        elif confirms_buy:
            pressure = -((1.0 - retail_ratio) + (top_long - top_short))
        else:
            pressure = (retail_ratio - 1.0) + (top_short - top_long)

        return MarketPlayersSnapshot(
            inst_id=inst_id,
            ccy=ccy,
            ts=ts,
            retail_long_short_ratio=retail_ratio,
            top_long_ratio=top_long,
            top_short_ratio=top_short,
            pressure=pressure,
            scenario=scenario,
            confirms_buy=confirms_buy,
            confirms_sell=confirms_sell,
        )

    @staticmethod
    def _currency(inst_id: str) -> str:
        return (inst_id or "BTC-USDT").split("-")[0].upper()
