"""
Signal-only strategy for studying whale flow context.

This strategy does not place trades. It records large free-source on-chain
events alongside simple market confirmation metrics so we can measure whether
the idea has an edge before enabling execution logic.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class OnchainWhaleContextStrategy(BaseStrategy):
    needs_onchain_context = True

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="IF001",
            name="IF001 - On-chain Whale Context",
            description=(
                "Signal-only research strategy using free on-chain sources. "
                "It classifies large BTC/EVM flows, scores market confirmation, "
                "and logs outcomes without opening positions."
            ),
            tags=["onchain", "whale", "research", "signal-only"],
            criteria=[
                {"id": "c1_whale_score", "label": "C1 Whale score", "description": "Pontuação de contexto on-chain por movimentação de baleias."},
            ],
            params={
                "min_value_usd": ParamDef(
                    type="float", default=1_000_000, min=100_000,
                    max=100_000_000, step=100_000,
                    description="Minimum on-chain event value in USD"),
                "poll_seconds": ParamDef(
                    type="int", default=60, min=15, max=900, step=15,
                    description="Minimum seconds between on-chain API polls"),
                "min_score": ParamDef(
                    type="int", default=70, min=0, max=100, step=5,
                    description="Score threshold for high-conviction research signals"),
                "ema_fast": ParamDef(
                    type="int", default=21, min=3, max=100, step=1,
                    description="Fast EMA for market confirmation"),
                "ema_slow": ParamDef(
                    type="int", default=55, min=5, max=200, step=1,
                    description="Slow EMA for market confirmation"),
                "volume_spike_ratio": ParamDef(
                    type="float", default=1.4, min=1.0, max=5.0, step=0.1,
                    description="Volume/current average ratio required for confirmation"),
                "monitor_evm": ParamDef(
                    type="int", default=0, min=0, max=1, step=1,
                    description="Set 1 to enable Etherscan-based EVM monitoring"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.min_value_usd = p["min_value_usd"].default
        self.poll_seconds = p["poll_seconds"].default
        self.min_score = p["min_score"].default
        self.ema_fast = p["ema_fast"].default
        self.ema_slow = p["ema_slow"].default
        self.volume_spike_ratio = p["volume_spike_ratio"].default
        self.monitor_evm = p["monitor_evm"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k) and v is not None:
                setattr(self, k, v)

    def onchain_config(self) -> dict:
        return {
            "min_value_usd": float(self.min_value_usd),
            "cooldown_seconds": int(self.poll_seconds),
            "monitor_btc": True,
            "monitor_evm": bool(int(self.monitor_evm)),
        }

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, {})

    def compute_with_context(
        self,
        candles: list,
        context: dict | None = None,
    ) -> Optional[StrategyResult]:
        min_len = max(int(self.ema_slow), 30) + 5
        if len(candles) < min_len:
            return None

        context = context or {}
        events = context.get("onchain_events") or []
        market = self._market_context(candles)
        best_event = self._select_best_event(events)
        score, classification = self._score(best_event, market)

        indicators = {
            "whale_score": score,
            "event_count": len(events),
            "largest_usd": round(float(best_event.get("amount_usd", 0)) if best_event else 0, 2),
            "exchange_inflow": 1 if best_event and best_event.get("direction") == "exchange_inflow" else 0,
            "market_confirm": 1 if market["trend_confirmed"] and market["volume_confirmed"] else 0,
            "ema_fast": market["ema_fast"],
            "ema_slow": market["ema_slow"],
            "volume_ratio": market["volume_ratio"],
            "close": round(candles[-1].close, 2),
        }

        metadata = {
            "mode": "signal_only",
            "classification": classification,
            "event": best_event,
            "market": market,
            "would_trade": score >= int(self.min_score),
        }

        if not best_event:
            reason = "No qualifying free on-chain event"
        elif score >= int(self.min_score):
            reason = "High-context whale signal logged; execution disabled"
        else:
            reason = "Whale context logged; score below threshold"

        return StrategyResult(
            signal=Signal.HOLD,
            criteria_met=1 if score >= int(self.min_score) else 0,
            criteria_total=1,
            indicators=indicators,
            metadata=metadata,
            hold_reason=reason,
        )

    def _market_context(self, candles: list) -> dict:
        df = pd.DataFrame([
            {"close": c.close, "volume": c.volume}
            for c in candles
        ])
        ema_f = ta.ema(df["close"], length=int(self.ema_fast))
        ema_l = ta.ema(df["close"], length=int(self.ema_slow))

        ef = float(ema_f.iloc[-1]) if ema_f is not None else candles[-1].close
        el = float(ema_l.iloc[-1]) if ema_l is not None else candles[-1].close
        vol_avg = float(df["volume"].tail(20).mean() or 0)
        volume_ratio = float(candles[-1].volume / vol_avg) if vol_avg else 0.0

        return {
            "ema_fast": round(ef, 2),
            "ema_slow": round(el, 2),
            "trend": "bullish" if ef > el else "bearish" if ef < el else "neutral",
            "trend_confirmed": ef != el,
            "volume_ratio": round(volume_ratio, 3),
            "volume_confirmed": volume_ratio >= float(self.volume_spike_ratio),
        }

    def _select_best_event(self, events: list[dict]) -> dict:
        if not events:
            return {}
        return max(events, key=lambda e: float(e.get("amount_usd") or 0))

    def _score(self, event: dict, market: dict) -> tuple[int, str]:
        if not event:
            return 0, "none"

        score = 0
        amount_usd = float(event.get("amount_usd") or 0)
        direction = event.get("direction")
        confidence = event.get("confidence")

        if amount_usd >= float(self.min_value_usd):
            score += 20
        if amount_usd >= float(self.min_value_usd) * 5:
            score += 15
        if event.get("symbol") in {"BTC", "ETH", "USDT", "USDC"}:
            score += 10
        if direction == "exchange_inflow":
            score += 25
        elif direction == "large_transfer":
            score += 10
        if confidence == "high":
            score += 15
        if market["volume_confirmed"]:
            score += 10
        if market["trend_confirmed"]:
            score += 5

        if direction == "exchange_inflow":
            classification = "possible_sell_pressure"
        elif direction == "large_transfer":
            classification = "large_transfer_watch"
        else:
            classification = "unknown_flow"

        return min(score, 100), classification
