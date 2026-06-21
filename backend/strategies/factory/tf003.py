"""
strategies/macd.py — MACD Crossover + Signal Line + Zero Cross

Lógica:
  LONG  → linha MACD cruza acima da linha Signal E histograma positivo
  SHORT → linha MACD cruza abaixo da linha Signal E histograma negativo
  Filtro opcional de tendência: EMA de longo prazo
"""

from __future__ import annotations
from typing import Optional

import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class MacdStrategy(BaseStrategy):

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF003",
            name="TF003 - MACD Crossover",
            description = (
                "Usa o cruzamento da linha MACD com a linha Signal "
                "para identificar mudanças de momentum. "
                "LONG quando MACD cruza acima do Signal com histograma positivo. "
                "SHORT no cruzamento inverso. "
                "Filtro de tendência via EMA longa para evitar trades contra a tendência."
            ),
            tags   = ["momentum", "trend", "macd"],
            params = {
                "macd_fast": ParamDef(
                    type="int", default=12, min=3, max=50, step=1,
                    description="MACD EMA rápida"),
                "macd_slow": ParamDef(
                    type="int", default=26, min=10, max=100, step=1,
                    description="MACD EMA lenta"),
                "macd_signal": ParamDef(
                    type="int", default=9, min=3, max=30, step=1,
                    description="MACD linha signal"),
                "trend_ma": ParamDef(
                    type="int", default=200, min=50, max=500, step=10,
                    description="MA de tendência (0 = desativado)"),
                "atr_period": ParamDef(
                    type="int", default=14, min=5, max=50, step=1,
                    description="ATR para cálculo de risco"),
                "atr_mult_sl": ParamDef(
                    type="float", default=1.5, min=0.5, max=5.0, step=0.1,
                    description="Multiplicador ATR para Stop Loss"),
                "tp1_pct": ParamDef(
                    type="float", default=2.0, min=0.5, max=10.0, step=0.1,
                    description="Take Profit 1 (%)"),
            },
            criteria=[
                {"id": "c1", "label": "Cruzamento MACD", "description": "MACD cruza a linha Signal"},
                {"id": "c2", "label": "Histograma", "description": "Histograma positivo/negativo"},
                {"id": "c3", "label": "Filtro Tendência", "description": "Preço alinhado com a tendência"},
            ],
        )

    def __init__(self):
        p = self.info().params
        self.macd_fast   = p["macd_fast"].default
        self.macd_slow   = p["macd_slow"].default
        self.macd_signal = p["macd_signal"].default
        self.trend_ma    = p["trend_ma"].default
        self.atr_period  = p["atr_period"].default
        self.atr_mult_sl = p["atr_mult_sl"].default
        self.tp1_pct     = p["tp1_pct"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        min_candles = max(self.macd_slow + self.macd_signal,
                         self.trend_ma if self.trend_ma > 0 else 0,
                         self.atr_period) + 5
        if len(candles) < min_candles:
            return None

        df = pd.DataFrame([
            {"high": c.high, "low": c.low, "close": c.close}
            for c in candles
        ])

        macd_df = ta.macd(df["close"],
                          fast=self.macd_fast,
                          slow=self.macd_slow,
                          signal=self.macd_signal)
        atr = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)

        if macd_df is None or atr is None:
            return None

        macd_col   = f"MACD_{self.macd_fast}_{self.macd_slow}_{self.macd_signal}"
        signal_col = f"MACDs_{self.macd_fast}_{self.macd_slow}_{self.macd_signal}"
        hist_col   = f"MACDh_{self.macd_fast}_{self.macd_slow}_{self.macd_signal}"

        macd_now  = float(macd_df[macd_col].iloc[-1])
        macd_prev = float(macd_df[macd_col].iloc[-2])
        sig_now   = float(macd_df[signal_col].iloc[-1])
        sig_prev  = float(macd_df[signal_col].iloc[-2])
        hist      = float(macd_df[hist_col].iloc[-1])
        atr_now   = float(atr.iloc[-1])
        close     = candles[-1].close

        # Filtro de tendência
        trend_ok_long = trend_ok_short = True
        ma_val = None
        if self.trend_ma > 0:
            ma = ta.ema(df["close"], length=self.trend_ma)
            if ma is not None:
                ma_val = float(ma.iloc[-1])
                trend_ok_long  = close > ma_val
                trend_ok_short = close < ma_val

        cross_up   = macd_prev <= sig_prev and macd_now > sig_now and hist > 0
        cross_down = macd_prev >= sig_prev and macd_now < sig_now and hist < 0
        long_ready = macd_now > sig_now and hist > 0 and trend_ok_long
        short_ready = macd_now < sig_now and hist < 0 and trend_ok_short

        if long_ready:
            signal = Signal.BUY
        elif short_ready:
            signal = Signal.SELL
        else:
            signal = Signal.HOLD

        indicators = {
            "macd":   round(macd_now, 4),
            "signal": round(sig_now, 4),
            "hist":   round(hist, 4),
            "atr":    round(atr_now, 2),
            "close":  round(close, 2),
        }
        if ma_val is not None:
            indicators["trend_ma"] = round(ma_val, 2)

        criteria_met = 3 if signal in (Signal.BUY, Signal.SELL) else 0
        
        return StrategyResult(
            signal     = signal,
            indicators = indicators,
            metadata   = {
                "entry_mode": "criteria_ready",
                "trigger_cross": cross_up if signal == Signal.BUY else cross_down,
                "sl_price":  round(close - atr_now * self.atr_mult_sl, 2)
                             if signal == Signal.BUY
                             else round(close + atr_now * self.atr_mult_sl, 2),
                "tp1_price": round(close * (1 + self.tp1_pct / 100), 2)
                             if signal == Signal.BUY
                             else round(close * (1 - self.tp1_pct / 100), 2),
            },
            criteria_met=criteria_met,
            criteria_total=3,
        )
