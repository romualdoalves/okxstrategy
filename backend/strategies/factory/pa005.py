# strategies/price_action_liquidity_sweep.py — Liquidity Sweep Strategy (Spot Market)

"""Implementation of a non‑leveraged price‑action strategy that exploits liquidity‑sweep moves.

The algorithm follows the plan described in the implementation plan:
1. Identify support zones where retail stop‑losses accumulate.
2. Detect a rapid sweep candle that breaks the support and leaves a long lower wick.
3. Confirm a Change of Character (CHoCH) by breaking the most recent high of the down‑channel.
4. Generate entry, stop‑loss, and tiered take‑profit signals.

Only the cleaned OHLCV data from `pos_imputation.csv` is required; the strategy does not write any CSV files.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from typing import Optional

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class LiquiditySweepStrategy(BaseStrategy):
    """Liquidity‑Sweep (price‑action) strategy for spot markets.

    The strategy works on a DataFrame of candles (open, high, low, close) and
    returns a :class:`StrategyResult` containing the appropriate signal.
    """

    @classmethod
    def info(cls) -> StrategyInfo:
        """Metadata describing the strategy and its configurable parameters."""
        return StrategyInfo(
            id="PA005",
            name="PA005 - Liquidity Sweep – Price Action (Spot)",
            description=(
                "Explora varreduras de liquidez no lado de venda e confirma a "
                "reversão via Change of Character (CHoCH). Operação sem alavancagem."
            ),
            tags=["price_action", "liquidity", "sweep", "spot", "non_leveraged"],
            recommended_timeframe="1h",
            criteria=[
                {"id": "c1_support", "label": "C1 Suporte", "description": "Zona de suporte por lows repetidos."},
                {"id": "c2_sweep", "label": "C2 Sweep", "description": "Varredura de liquidez com pavio dominante."},
                {"id": "c3_choch", "label": "C3 CHoCH", "description": "Quebra de estrutura confirma reversão."},
            ],
            params={
                "support_window": ParamDef(type="int", default=4, min=2, max=10,
                                            description="Janela para cálculo do low rolling (suporte)."),
                "support_repeat": ParamDef(type="int", default=3, min=2, max=6,
                                            description="Número mínimo de lows repetidos para criar zona de suporte."),
                "sweep_lookback": ParamDef(type="int", default=20, min=10, max=40,
                                            description="Quantas velas observar ao identificar o menor low da sweep."),
                "wick_ratio": ParamDef(type="float", default=0.5, min=0.2, max=0.8,
                                         description="Proporção mínima do wick em relação ao corpo para considerar sweep."),
                "atr_period": ParamDef(type="int", default=14, min=7, max=28,
                                         description="Período do ATR usado como buffer para stop."),
                "atr_multiplier": ParamDef(type="float", default=0.5, min=0.1, max=2.0,
                                             description="Multiplicador do ATR no cálculo do stop."),
                "rr_ratio": ParamDef(type="float", default=3.0, min=1.5, max=5.0,
                                        description="Risco:Recompensa alvo para TP1."),
                "tp2_factor": ParamDef(type="float", default=1.5, min=1.0, max=2.5,
                                         description="Fator para cálculo do TP2 (pre‑sweep top)."),
            },
        )

    def __init__(self):
        p = self.info().params
        self.support_window = p["support_window"].default
        self.support_repeat = p["support_repeat"].default
        self.sweep_lookback = p["sweep_lookback"].default
        self.wick_ratio = p["wick_ratio"].default
        self.atr_period = p["atr_period"].default
        self.atr_multiplier = p["atr_multiplier"].default
        self.rr_ratio = p["rr_ratio"].default
        self.tp2_factor = p["tp2_factor"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def _atr(self, df: pd.DataFrame) -> pd.Series:
        """Simple Average True Range implementation."""
        high_low = df["high"] - df["low"]
        high_close = np.abs(df["high"] - df["close"].shift())
        low_close = np.abs(df["low"] - df["close"].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(self.atr_period, min_periods=1).mean()
        return atr

    def compute(self, candles: list) -> Optional[StrategyResult]:
        """Generate a signal based on the most recent candle.

        Parameters
        ----------
        candles : list
            List of candle objects with attributes ``open, high, low, close``.
        """
        # Ensure a minimal amount of data; otherwise return HOLD.
        min_required = max(self.support_window, self.sweep_lookback) + 2
        if len(candles) < min_required:
            dummy = {
                "open": candles[-1].open if candles else 0,
                "high": candles[-1].high if candles else 0,
                "low": candles[-1].low if candles else 0,
                "close": candles[-1].close if candles else 0,
            }
            df_dummy = pd.DataFrame([dummy])
            return StrategyResult(
                signal=Signal.HOLD,
                criteria_met=0,
                criteria_total=3,
                hold_reason="Número insuficiente de candles para cálculo.",
                indicators=self._inds(df_dummy.iloc[-1], support_ok=0)
            )

        # Build DataFrame from candle list
        df = pd.DataFrame([
            {"open": c.open, "high": c.high, "low": c.low, "close": c.close}
            for c in candles
        ])

        # ------------------------------------------------------------
        # 1️⃣ Identify support zones – rolling low with repetition.
        # ------------------------------------------------------------
        low_rolling = df["low"].rolling(window=self.support_window, min_periods=1).min()
        support_mask = (low_rolling.diff().abs() < 1e-8)
        for _ in range(self.support_repeat - 1):
            support_mask = support_mask & low_rolling.shift(-1).eq(low_rolling)
        support_price = low_rolling.where(support_mask)
        valid_supports = support_price.dropna()
        if valid_supports.empty:
            # Fallback: lowest low in recent sweep_lookback candles.
            support_level = df["low"].iloc[-(self.sweep_lookback+1):-1].min()
        else:
            support_level = valid_supports.iloc[-1]

        # ------------------------------------------------------------
        # 2️⃣ Detect Sweep candle – price break below support with long wick.
        # ------------------------------------------------------------
        recent_low_window = df["low"].rolling(self.sweep_lookback, min_periods=1).min()
        cond_break = df["close"] < support_level
        cond_lowest = df["low"] == recent_low_window
        lower_body = df[["open", "close"]].min(axis=1)
        wick_len = lower_body - df["low"]
        body = (df["open"] - df["close"]).abs()
        cond_wick = wick_len > self.wick_ratio * body
        sweep_mask = cond_break & cond_lowest & cond_wick

        if not sweep_mask.any():
            return StrategyResult(
                signal=Signal.HOLD,
                criteria_met=0,
                criteria_total=3,
                hold_reason="Nenhuma varredura de liquidez detectada.",
                indicators=self._inds(df.iloc[-1], support_ok=1, sweep_ok=0, support_level=support_level)
            )

        # Index of most recent sweep candle
        sweep_idx = sweep_mask[::-1].idxmax()
        sweep_low = df.at[sweep_idx, "low"]

        # ------------------------------------------------------------
        # 3️⃣ CHoCH confirmation – break of recent channel high.
        # ------------------------------------------------------------
        channel_start = max(0, sweep_idx - 8)
        channel_high = df["high"].iloc[channel_start:sweep_idx].max()
        if sweep_idx + 1 >= len(df):
            return StrategyResult(
                signal=Signal.HOLD,
                criteria_met=0,
                criteria_total=3,
                hold_reason="Sweep identificado, mas ainda não há candle de confirmação.",
                indicators=self._inds(df.iloc[-1], support_ok=1, sweep_ok=1, choch_ok=0, support_level=support_level, channel_high=channel_high)
            )
        conf_close = df.at[sweep_idx + 1, "close"]
        choch = conf_close > channel_high
        if not choch:
            return StrategyResult(
                signal=Signal.HOLD,
                criteria_met=0,
                criteria_total=3,
                hold_reason="Sweep detectada, mas sem quebra de CHoCH.",
                indicators=self._inds(df.iloc[-1], support_ok=1, sweep_ok=1, choch_ok=0, support_level=support_level, channel_high=channel_high)
            )

        # ------------------------------------------------------------
        # 4️⃣ Build entry, stop, TP1 & TP2.
        # ------------------------------------------------------------
        entry_price = conf_close
        atr_series = self._atr(df)
        atr_value = atr_series.iloc[sweep_idx + 1]
        stop_price = sweep_low - self.atr_multiplier * atr_value
        risk = entry_price - stop_price
        if risk <= 0:
            return StrategyResult(
                signal=Signal.HOLD,
                criteria_met=0,
                criteria_total=3,
                hold_reason="Risco calculado não positivo.",
                indicators=self._inds(df.iloc[-1])
            )
        tp1_price = entry_price + self.rr_ratio * risk
        pre_sweep_top = df["high"].iloc[:sweep_idx].max()
        tp2_price = entry_price + self.tp2_factor * (pre_sweep_top - entry_price)

        metadata = {
            "entry": round(entry_price, 2),
            "sl_price": round(stop_price, 2),
            "tp1_price": round(tp1_price, 2),
            "tp2": round(tp2_price, 2),
            "sl_pct": round(risk / entry_price, 5),
            "tp1_pct": round(self.rr_ratio * risk / entry_price, 5),
            "ts_pct": round(self.rr_ratio * risk / entry_price, 5),
            "support_zone": round(support_level, 2),
            "sweep_low": round(sweep_low, 2),
            "channel_high": round(channel_high, 2),
        }

        return StrategyResult(
            signal=Signal.BUY,
            criteria_met=3,
            criteria_total=3,
            hold_reason="Liquidity Sweep confirmado – entrada LONG.",
            indicators=self._inds(df.iloc[-1], support_ok=1, sweep_ok=1, choch_ok=1, support_level=support_level, channel_high=channel_high),
            metadata=metadata,
        )

    # -----------------------------------------------------------------
    # Helper to expose a minimal set of indicator values for UI / logging.
    # -----------------------------------------------------------------
    def _inds(self, candle, support_ok=0, sweep_ok=0, choch_ok=0, support_level=0, channel_high=0) -> dict:
        return {
            "close":         round(candle["close"], 2),
            "low":           round(candle["low"], 2),
            "high":          round(candle["high"], 2),
            "support_ok":    support_ok,
            "sweep_ok":      sweep_ok,
            "choch_ok":      choch_ok,
            "support_level": round(support_level, 2) if support_level else 0,
            "channel_high":  round(channel_high, 2) if channel_high else 0,
        }
