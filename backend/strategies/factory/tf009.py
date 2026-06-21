"""
strategies/ma_crossover.py — Moving Average Crossover Strategy

Lógica:
   LONG  → MA curta cruza acima da MA longa
   SHORT → MA curta cruza abaixo da MA longa
"""

from __future__ import annotations
from typing import Optional

import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class MaCrossoverStrategy(BaseStrategy):

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF009",
            name="TF009 - MA Crossover",
            description = (
                "Entra LONG quando a média móvel curta cruza acima da longa. "
                "Entra SHORT quando a média móvel curta cruza abaixo da longa. "
                "Funciona bem em tendências fortes."
            ),
            tags   = ["trend", "moving_average", "crossover"],
            criteria=[
                {"id": "c1", "label": "Cruzamento MA", "description": "MA rápida cruza a MA lenta"},
            ],
            params = {
                "fast_ma_period": ParamDef(
                    type="int", default=20, min=5, max=100, step=1,
                    description="Período da MA rápida"),
                "slow_ma_period": ParamDef(
                    type="int", default=50, min=10, max=200, step=1,
                    description="Período da MA lenta"),
                "ma_type": ParamDef(
                    type="str", default="sma", 
                    description="Tipo de MA (sma, ema)"),
                "atr_period": ParamDef(
                    type="int", default=14, min=5, max=50, step=1,
                    description="ATR para cálculo de risco"),
                "atr_mult_sl": ParamDef(
                    type="float", default=2.0, min=0.5, max=10.0, step=0.1,
                    description="Multiplicador ATR para Stop Loss"),
                "tp_rr": ParamDef(
                    type="float", default=2.0, min=0.5, max=5.0, step=0.1,
                    description="Relação risco-recompensa para Take Profit"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.fast_ma_period = p["fast_ma_period"].default
        self.slow_ma_period = p["slow_ma_period"].default
        self.ma_type = p["ma_type"].default
        self.atr_period = p["atr_period"].default
        self.atr_mult_sl = p["atr_mult_sl"].default
        self.tp_rr = p["tp_rr"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        min_candles = max(self.fast_ma_period, self.slow_ma_period) + 10
        if len(candles) < min_candles:
            return None

        df = pd.DataFrame([
            {"high": c.high, "low": c.low, "close": c.close}
            for c in candles
        ])

        # Calculate moving averages
        if self.ma_type.lower() == "ema":
            fast_ma = ta.ema(df["close"], length=self.fast_ma_period)
            slow_ma = ta.ema(df["close"], length=self.slow_ma_period)
        else:  # Default to SMA
            fast_ma = ta.sma(df["close"], length=self.fast_ma_period)
            slow_ma = ta.sma(df["close"], length=self.slow_ma_period)

        # Calculate ATR for risk management
        atr = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)

        if fast_ma is None or slow_ma is None or atr is None:
            return None

        fast_ma_now  = float(fast_ma.iloc[-1])
        fast_ma_prev = float(fast_ma.iloc[-2])
        slow_ma_now  = float(slow_ma.iloc[-1])
        slow_ma_prev = float(slow_ma.iloc[-2])
        atr_now      = float(atr.iloc[-1])
        close        = candles[-1].close

        # Detect crossovers
        fast_above_slow_now = fast_ma_now > slow_ma_now
        fast_above_slow_prev = fast_ma_prev > slow_ma_prev
        
        bullish_crossover = fast_above_slow_now and not fast_above_slow_prev  # Crossed up
        bearish_crossover = not fast_above_slow_now and fast_above_slow_prev  # Crossed down

        if bullish_crossover:
            signal = Signal.BUY
        elif bearish_crossover:
            signal = Signal.SELL
        else:
            signal = Signal.HOLD

        # Calculate stop loss and take profit based on ATR
        if signal == Signal.BUY:
            sl_price = close - (atr_now * self.atr_mult_sl)
            tp_price = close + (atr_now * self.atr_mult_sl * self.tp_rr)
        elif signal == Signal.SELL:
            sl_price = close + (atr_now * self.atr_mult_sl)
            tp_price = close - (atr_now * self.atr_mult_sl * self.tp_rr)
        else:
            sl_price = tp_price = 0.0

        return StrategyResult(
            signal     = signal,
            criteria_met= 1 if signal in (Signal.BUY, Signal.SELL) else 0,
            criteria_total= 1,
            indicators = {
                "fast_ma": round(fast_ma_now, 4),
                "slow_ma": round(slow_ma_now, 4),
                "atr":     round(atr_now, 4),
                "close":   round(close, 4),
            },
            metadata = {
                "bullish_crossover": bullish_crossover,
                "bearish_crossover": bearish_crossover,
                "sl_price":  round(sl_price, 4),
                "tp1_price": round(tp_price, 4),
                "ma_type":   self.ma_type.upper(),
            },
        )
