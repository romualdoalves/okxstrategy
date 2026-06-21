"""Estratégia TF014 - Marty Schwartz Red Light Green Light
Gerado pela Fábrica de Estratégias
Data de criação: 2024-01-01
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class MartySchwartzRedLightGreenLightStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF014",
            name="TF014 - Marty Schwartz Red Light Green Light",
            description="Estratégia de tendência baseada no sistema Red Light Green Light de Marty Schwartz. Utiliza EMA10 em timeframe superior para definir viés (Green Light = compra, Red Light = venda) e EMA50 ou rompimento com volume em timeframe inferior como gatilho de entrada. Gestão de risco com stop curto no candle gatilho e alvo de 2x risco ou trailing stop via ATR.",
            tags=["trend", "multi-tf", "breakout", "volume"],
            recommended_timeframe="15m",
            criteria=[
                {"id": "c1", "label": "Viés EMA Macro", "description": "EMA macro define Green Light ou Red Light"},
                {"id": "c2", "label": "Gatilho de Entrada", "description": "Pullback na EMA micro ou rompimento com volume"},
            ],
            params={
                "ema_macro_period": ParamDef(type="int", default=10, min=5, max=50, step=1, description="Período da EMA no timeframe superior para definir viés"),
                "ema_micro_period": ParamDef(type="int", default=50, min=20, max=200, step=1, description="Período da EMA no timeframe inferior para pullback"),
                "volume_sma_period": ParamDef(type="int", default=20, min=10, max=50, step=1, description="Período da SMA de volume para confirmação de rompimento"),
                "atr_period": ParamDef(type="int", default=14, min=5, max=50, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Trailing Stop após TP1"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.ema_macro_period = p["ema_macro_period"].default
        self.ema_micro_period = p["ema_micro_period"].default
        self.volume_sma_period = p["volume_sma_period"].default
        self.atr_period = p["atr_period"].default
        self.sl_mult = p["sl_mult"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles, context=None) -> Optional[StrategyResult]:
        min_candles = max(self.ema_macro_period + 5, self.ema_micro_period + 5, self.volume_sma_period + 5, 60)
        if len(candles) < min_candles:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_candles} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "bias": 0.0, "ema_macro": 0.0, "ema_micro": 0.0, "volume_sma": 0.0, "atr": 0.0},
            )

        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles])

        # Calcular EMA macro (timeframe superior simulado com EMA no mesmo timeframe)
        ema_macro_s = ta.ema(df["close"], length=self.ema_macro_period)
        ema_micro_s = ta.ema(df["close"], length=self.ema_micro_period)
        atr_s = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        volume_sma_s = ta.sma(df["volume"], length=self.volume_sma_period)

        if ema_macro_s is None or ema_micro_s is None or atr_s is None or volume_sma_s is None:
            return None

        close = float(candles[-1].close)
        ema_macro = float(ema_macro_s.iloc[-1])
        ema_micro = float(ema_micro_s.iloc[-1])
        atr = float(atr_s.iloc[-1])
        volume_sma = float(volume_sma_s.iloc[-1])
        volume = float(candles[-1].volume)

        # Calcular inclinação da EMA macro (diferença entre EMA atual e EMA de 3 períodos atrás)
        if len(ema_macro_s) >= 4:
            ema_macro_3_ago = float(ema_macro_s.iloc[-4])
            ema_slope = ema_macro - ema_macro_3_ago
        else:
            ema_slope = 0.0

        # Definir bias: 1 = alta (preço acima EMA macro com inclinação positiva), -1 = baixa (preço abaixo EMA macro com inclinação negativa), 0 = neutro
        if close > ema_macro and ema_slope > 0:
            bias = 1
        elif close < ema_macro and ema_slope < 0:
            bias = -1
        else:
            bias = 0

        # Calcular suporte/resistência (mínima/máxima dos últimos 20 candles)
        lookback = min(20, len(candles) - 1)
        support = float(df["low"].iloc[-lookback:].min())
        resistance = float(df["high"].iloc[-lookback:].max())

        # Critérios
        criteria_met = 0
        criteria_total = 2

        # C1: Viés
        if bias != 0:
            criteria_met += 1

        # C2: Gatilho de entrada
        entry_trigger = False
        if bias == 1:
            # Pullback na EMA micro com reversão OU rompimento com volume
            pullback = (candles[-1].low <= ema_micro and close > ema_micro and close > candles[-1].open)
            breakout = (close > resistance and volume > volume_sma)
            if pullback or breakout:
                entry_trigger = True
        elif bias == -1:
            pullback = (candles[-1].high >= ema_micro and close < ema_micro and close < candles[-1].open)
            breakout = (close < support and volume > volume_sma)
            if pullback or breakout:
                entry_trigger = True

        if entry_trigger:
            criteria_met += 1

        # Decisão de sinal
        signal = Signal.HOLD
        hold_reason = ""

        if bias == 0:
            hold_reason = "EMA macro sem inclinação clara (lateralização)"
        elif not entry_trigger:
            hold_reason = "Nenhum gatilho de entrada ativado — aguardando pullback ou rompimento com volume"
        elif bias == 1 and entry_trigger:
            signal = Signal.BUY
        elif bias == -1 and entry_trigger:
            signal = Signal.SELL

        # Calcular stop loss e take profit
        sl_dist = atr * self.sl_mult
        if signal == Signal.BUY:
            sl_price = round(close - sl_dist, 4)
            tp1_price = round(close + sl_dist * self.tp1_rr, 4)
        elif signal == Signal.SELL:
            sl_price = round(close + sl_dist, 4)
            tp1_price = round(close - sl_dist * self.tp1_rr, 4)
        else:
            sl_price = 0.0
            tp1_price = 0.0

        return StrategyResult(
            signal=signal,
            hold_reason=hold_reason,
            indicators={
                "bias": float(bias),
                "close": round(close, 4),
                "atr": round(atr, 6),
                "ema_macro": round(ema_macro, 4),
                "ema_micro": round(ema_micro, 4),
                "volume_sma": round(volume_sma, 2),
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(sl_dist / close, 5) if close != 0 else 0.0,
                "tp1_pct": round(sl_dist * self.tp1_rr / close, 5) if close != 0 else 0.0,
                "ts_pct": round(atr * self.ts_mult / close, 5) if close != 0 else 0.0,
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )
