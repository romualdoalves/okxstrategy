"""
strategies/rsi_ma.py — RSI Oversold/Overbought + MA Filter

Lógica:
  LONG  → RSI cruza acima de 30 (saída de sobrevenda) E preço > MA200
  SHORT → RSI cruza abaixo de 70 (saída de sobrecompra) E preço < MA200
"""

from __future__ import annotations
from typing import Optional

import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class RsiMaStrategy(BaseStrategy):

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF002",
            name="TF002 - RSI + Moving Average",
            description = (
                "Entra LONG quando o RSI sobe acima do nível de sobrevenda "
                "e o preço está acima da média móvel de longo prazo. "
                "Entra SHORT quando o RSI cai abaixo do nível de sobrecompra "
                "e o preço está abaixo da média. "
                "Ideal para mercados com reversões claras."
            ),
            tags   = ["momentum", "reversal", "rsi"],
            params = {
                "rsi_period": ParamDef(
                    type="int", default=14, min=5, max=50, step=1,
                    description="RSI (períodos)"),
                "rsi_oversold": ParamDef(
                    type="int", default=30, min=10, max=45, step=1,
                    description="Nível de sobrevenda"),
                "rsi_overbought": ParamDef(
                    type="int", default=70, min=55, max=90, step=1,
                    description="Nível de sobrecompra"),
                "ma_period": ParamDef(
                    type="int", default=200, min=20, max=500, step=10,
                    description="MA de tendência (períodos)"),
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
                {"id": "c1", "label": "RSI na zona", "description": "RSI na zona de sobrevenda/sobrecompra"},
                {"id": "c2", "label": "Filtro MA", "description": "Preço alinhado com a média móvel"},
            ],
        )

    def __init__(self):
        p = self.info().params
        self.rsi_period    = p["rsi_period"].default
        self.rsi_oversold  = p["rsi_oversold"].default
        self.rsi_overbought= p["rsi_overbought"].default
        self.ma_period     = p["ma_period"].default
        self.atr_period    = p["atr_period"].default
        self.atr_mult_sl   = p["atr_mult_sl"].default
        self.tp1_pct       = p["tp1_pct"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        min_candles = max(self.ma_period, self.rsi_period) + 5
        if len(candles) < min_candles:
            return None

        df = pd.DataFrame([
            {"high": c.high, "low": c.low, "close": c.close}
            for c in candles
        ])

        rsi = ta.rsi(df["close"], length=self.rsi_period)
        ma  = ta.sma(df["close"], length=self.ma_period)
        atr = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)

        if rsi is None or ma is None or atr is None:
            return None

        rsi_now  = float(rsi.iloc[-1])
        rsi_prev = float(rsi.iloc[-2])
        ma_now   = float(ma.iloc[-1])
        atr_now  = float(atr.iloc[-1])
        close    = candles[-1].close

        cross_up   = rsi_prev <= self.rsi_oversold  and rsi_now > self.rsi_oversold
        cross_down = rsi_prev >= self.rsi_overbought and rsi_now < self.rsi_overbought
        long_ready = rsi_now <= self.rsi_oversold + 2 and close > ma_now
        short_ready = rsi_now >= self.rsi_overbought - 2 and close < ma_now

        if long_ready:
            signal = Signal.BUY
        elif short_ready:
            signal = Signal.SELL
        else:
            signal = Signal.HOLD

        criteria_met = 2 if signal in (Signal.BUY, Signal.SELL) else 0
        
        return StrategyResult(
            signal     = signal,
            indicators = {
                "rsi":   round(rsi_now, 2),
                "ma":    round(ma_now, 2),
                "atr":   round(atr_now, 2),
                "close": round(close, 2),
            },
            metadata = {
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
            criteria_total=2,
        )
