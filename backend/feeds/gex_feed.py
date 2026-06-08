"""
feeds/gex_feed.py — Gamma Exposure (GEX) feed via Deribit public API.

Fetches BTC options book summary, computes a simplified GEX proxy and
identifies gravitational OI levels (support/resistance) without any
paid subscription or API key.

GEX regime logic
────────────────
  Positive GEX  → market makers net short calls (long gamma) → counter-trend
                  hedging → DAMPENING effect → low volatility / range-bound
  Negative GEX  → market makers net short puts (short gamma) → pro-trend
                  hedging → AMPLIFYING effect → high volatility / explosions

Simplified proxy used here (no Black-Scholes required):
  GEX_proxy = Σ call_OI_usd(ATM ± 20%) - Σ put_OI_usd(ATM ± 20%)

The ATM window focuses on near-money options where gamma is most relevant.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("gex_feed")

_DERIBIT_BASE = "https://www.deribit.com/api/v2/public"
_CACHE_TTL = 300          # seconds — re-fetch at most every 5 min
_ATM_WINDOW_PCT = 0.20    # ±20% of spot price considered "near-money"
_TOP_N_STRIKES = 5        # number of top OI strikes to return


@dataclass
class GexSnapshot:
    """Immutable snapshot of GEX state at a given moment."""

    timestamp: int                               # unix epoch when fetched
    spot_price: float                            # BTC spot price from Deribit
    gex_value: float                             # proxy: positive → pos gamma
    regime: str                                  # "positive_gamma" | "negative_gamma" | "neutral"
    pcr: float                                   # put/call OI ratio (> 1 = put-heavy)
    call_oi_usd: float                           # total call OI USD in ATM window
    put_oi_usd: float                            # total put OI USD in ATM window
    max_pain: float                              # strike minimising total OI loss
    resistance_levels: list[float] = field(default_factory=list)   # top call strikes
    support_levels: list[float] = field(default_factory=list)      # top put strikes
    neg_peak_pressure_usd: float = 0.0          # USD pressure per 1% drop at peak neg strike

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "spot_price": self.spot_price,
            "gex_value": self.gex_value,
            "regime": self.regime,
            "pcr": self.pcr,
            "call_oi_usd": self.call_oi_usd,
            "put_oi_usd": self.put_oi_usd,
            "max_pain": self.max_pain,
            "resistance_levels": self.resistance_levels,
            "support_levels": self.support_levels,
            "neg_peak_pressure_usd": self.neg_peak_pressure_usd,
        }


class GexFeed:
    """Fetches and caches BTC GEX data from Deribit public REST API."""

    def __init__(self):
        self._cache: GexSnapshot | None = None
        self._last_fetch: float = 0.0

    async def fetch(self, session) -> GexSnapshot | None:
        """
        Returns a GexSnapshot, using cache if still fresh.
        session: aiohttp.ClientSession
        """
        now = time.time()
        if self._cache and (now - self._last_fetch) < _CACHE_TTL:
            return self._cache

        snapshot = await self._fetch_from_deribit(session)
        if snapshot:
            self._cache = snapshot
            self._last_fetch = now
        return snapshot

    @property
    def cached(self) -> GexSnapshot | None:
        """Returns last cached snapshot without triggering a fetch."""
        return self._cache

    # ── Internal ────────────────────────────────────────────────────────────

    async def _fetch_from_deribit(self, session) -> GexSnapshot | None:
        """Fetches BTC options book summary and builds a GexSnapshot."""
        try:
            url = f"{_DERIBIT_BASE}/get_book_summary_by_currency"
            params = {"currency": "BTC", "kind": "option"}
            async with session.get(url, params=params, timeout=10) as resp:
                data = await resp.json()

            instruments = data.get("result", [])
            if not instruments:
                log.warning("GEX feed: Deribit returned empty result")
                return None

            return self._compute_snapshot(instruments)

        except Exception as exc:
            log.warning("GEX feed: Deribit fetch failed — %s", exc)
            return None

    def _compute_snapshot(self, instruments: list[dict]) -> GexSnapshot | None:
        """Parses raw Deribit instruments into a GexSnapshot."""
        # --- Determine spot price from first available underlying_price
        spot = 0.0
        for instr in instruments:
            up = instr.get("underlying_price") or instr.get("mark_price", 0)
            if up and up > 1000:          # sanity: BTC is always > 1000
                spot = float(up)
                break
        if spot <= 0:
            log.warning("GEX feed: could not determine spot price")
            return None

        atm_low  = spot * (1 - _ATM_WINDOW_PCT)
        atm_high = spot * (1 + _ATM_WINDOW_PCT)

        # Buckets: strike → {call_oi_usd, put_oi_usd}
        strike_data: dict[float, dict[str, float]] = {}

        for instr in instruments:
            name = instr.get("instrument_name", "")
            # Format: BTC-DDMMMYY-STRIKE-TYPE  e.g. BTC-30MAY25-100000-C
            parts = name.split("-")
            if len(parts) < 4:
                continue
            try:
                strike = float(parts[2])
                opt_type = parts[3].upper()   # "C" or "P"
            except (ValueError, IndexError):
                continue

            oi_btc = float(instr.get("open_interest", 0) or 0)
            oi_usd = oi_btc * spot

            if strike not in strike_data:
                strike_data[strike] = {"call_oi_usd": 0.0, "put_oi_usd": 0.0}

            if opt_type == "C":
                strike_data[strike]["call_oi_usd"] += oi_usd
            elif opt_type == "P":
                strike_data[strike]["put_oi_usd"] += oi_usd

        if not strike_data:
            log.warning("GEX feed: no parseable options data")
            return None

        # Totals within ATM window (near-money gamma is most relevant)
        total_call_atm = 0.0
        total_put_atm  = 0.0
        for strike, oi in strike_data.items():
            if atm_low <= strike <= atm_high:
                total_call_atm += oi["call_oi_usd"]
                total_put_atm  += oi["put_oi_usd"]

        gex_proxy = total_call_atm - total_put_atm

        # Regime classification
        gex_abs   = abs(gex_proxy)
        threshold = (total_call_atm + total_put_atm) * 0.10   # >10% imbalance
        if gex_abs < threshold:
            regime = "neutral"
        elif gex_proxy > 0:
            regime = "positive_gamma"
        else:
            regime = "negative_gamma"

        # PCR (put/call ratio) — total across all strikes
        all_calls = sum(v["call_oi_usd"] for v in strike_data.values())
        all_puts  = sum(v["put_oi_usd"]  for v in strike_data.values())
        pcr = (all_puts / all_calls) if all_calls > 0 else 1.0

        # Top call strikes (resistance) and put strikes (support) — above/below spot
        call_strikes_above = {k: v["call_oi_usd"]
                               for k, v in strike_data.items()
                               if k > spot and v["call_oi_usd"] > 0}
        put_strikes_below  = {k: v["put_oi_usd"]
                               for k, v in strike_data.items()
                               if k < spot and v["put_oi_usd"] > 0}

        # Sort by OI descending, take top N, then return sorted by price
        resistance_levels = sorted(
            sorted(call_strikes_above, key=call_strikes_above.get, reverse=True)[:_TOP_N_STRIKES]
        )
        support_levels = sorted(
            sorted(put_strikes_below, key=put_strikes_below.get, reverse=True)[:_TOP_N_STRIKES],
            reverse=True,
        )

        # Max pain — strike minimising total OI loss for option writers
        max_pain = self._compute_max_pain(strike_data, spot)

        # Peak negative pressure: highest put OI below spot (USD per 1% move)
        neg_peak_usd = 0.0
        if put_strikes_below:
            peak_strike = max(put_strikes_below, key=put_strikes_below.get)
            neg_peak_usd = put_strikes_below[peak_strike] * 0.01   # 1% of OI per 1% move

        return GexSnapshot(
            timestamp=int(time.time()),
            spot_price=round(spot, 2),
            gex_value=round(gex_proxy, 0),
            regime=regime,
            pcr=round(pcr, 3),
            call_oi_usd=round(total_call_atm, 0),
            put_oi_usd=round(total_put_atm, 0),
            max_pain=round(max_pain, 0),
            resistance_levels=[round(s, 0) for s in resistance_levels],
            support_levels=[round(s, 0) for s in support_levels],
            neg_peak_pressure_usd=round(neg_peak_usd, 0),
        )

    @staticmethod
    def _compute_max_pain(strike_data: dict[float, dict[str, float]],
                          spot: float) -> float:
        """
        Max pain = the strike that minimises total dollar loss for all
        option writers (i.e., causes maximum loss to option buyers).

        For each candidate expiry strike S:
          loss_calls = Σ max(0, S - K) × call_OI_usd(K)  for K < S
          loss_puts  = Σ max(0, K - S) × put_OI_usd(K)   for K > S
          total_loss = loss_calls + loss_puts

        The strike with minimum total_loss is max pain.
        """
        strikes = sorted(strike_data.keys())
        if not strikes:
            return spot

        min_pain = float("inf")
        max_pain_strike = spot

        for candidate in strikes:
            pain_calls = sum(
                max(0.0, candidate - k) * strike_data[k]["call_oi_usd"]
                for k in strikes if k < candidate
            )
            pain_puts = sum(
                max(0.0, k - candidate) * strike_data[k]["put_oi_usd"]
                for k in strikes if k > candidate
            )
            total_pain = pain_calls + pain_puts
            if total_pain < min_pain:
                min_pain = total_pain
                max_pain_strike = candidate

        return max_pain_strike
