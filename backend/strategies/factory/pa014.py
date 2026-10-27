"""PA014 - Rejeição de Suporte/Resistência
Gerado pela Fábrica de Estratégias
Data de criação: 2024-01-01
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class RejeiçãoDeSuporteresistênciaStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="PA014",
            name="PA014 - Rejeição de Suporte/Resistência",
            description="Estratégia de reversão baseada em rejeição de preço em suportes e resistências, identificada por longos pavios nos candles. Para short, o preço testa resistência e deixa pavio superior longo. Para long, o preço testa suporte e deixa pavio inferior longo. Entrada na confirmação da rejeição.",
            tags=["reversal", "pattern", "swing"],
            recommended_timeframe="15m",
            criteria=[
                {"id": "c1_zone", "label": "C1 Zona", "description": "Preço está próximo de suporte (long) ou resistência (short)"},
                {"id": "c2_wick", "label": "C2 Pavio", "description": "Candle atual tem pavio longo (superior para short, inferior para long) com wick/body >= wick_ratio"},
                {"id": "c3_rejection", "label": "C3 Rejeição", "description": "Fechamento do candle na direção oposta ao pavio (ex: pavio superior, fechamento abaixo da máxima)"},
            ],
            params={
                "atr_period": ParamDef(type="int", default=14, min=5, max=50, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Trailing Stop após TP1"),
                "wick_ratio": ParamDef(type="float", default=2.0, min=1.5, max=5.0, step=0.5, description="Relação mínima entre pavio e corpo do candle para considerar rejeição"),
                "lookback": ParamDef(type="int", default=20, min=10, max=50, step=1, description="Período de lookback para identificar suporte/resistência (mínimo/máximo)"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.atr_period = p["atr_period"].default
        self.sl_mult = p["sl_mult"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default
        self.wick_ratio = p["wick_ratio"].default
        self.lookback = p["lookback"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles, context=None):
        min_c = max(self.lookback + 5, self.atr_period + 5)
        if len(candles) < min_c:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_c} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "atr": 0.0, "high": 0.0, "low": 0.0, "open": 0.0, "wick_upper": 0.0, "wick_lower": 0.0, "body": 0.0, "support": 0.0, "resistance": 0.0},
            )

        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close, "open": c.open, "volume": c.volume} for c in candles])
        atr_s = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        if atr_s is None:
            return None

        atr = float(atr_s.iloc[-1])
        close = float(candles[-1].close)
        high = float(candles[-1].high)
        low = float(candles[-1].low)
        open_price = float(candles[-1].open)

        # Calcular suporte e resistência via lookback
        support = float(df["low"].iloc[-self.lookback:].min())
        resistance = float(df["high"].iloc[-self.lookback:].max())

        # Calcular pavios e corpo
        body = abs(close - open_price)
        wick_upper = high - max(open_price, close)
        wick_lower = min(open_price, close) - low

        criteria_met = 0
        criteria_total=3

        # C1: Preço próximo de suporte (long) ou resistência (short)
        zone_long = abs(close - support) <= atr
        zone_short = abs(close - resistance) <= atr

        # C2: Pavio longo
        wick_long_upper = body > 0 and wick_upper / body >= self.wick_ratio
        wick_long_lower = body > 0 and wick_lower / body >= self.wick_ratio

        # C3: Rejeição confirmada
        rejection_long = close > low + body / 2
        rejection_short = close < high - body / 2

        signal = Signal.HOLD
        hold_reason = ""

        # Verificar entrada long
        if zone_long and wick_long_lower and rejection_long:
            criteria_met = 3
            signal = Signal.BUY
        # Verificar entrada short
        elif zone_short and wick_long_upper and rejection_short:
            criteria_met = 3
            signal = Signal.SELL
        else:
            # Contar critérios individuais para long
            if zone_long:
                criteria_met += 1
            if wick_long_lower:
                criteria_met += 1
            if rejection_long:
                criteria_met += 1

            # Se não for long, verificar short
            if criteria_met < 3:
                criteria_met = 0
                if zone_short:
                    criteria_met += 1
                if wick_long_upper:
                    criteria_met += 1
                if rejection_short:
                    criteria_met += 1

            if criteria_met == 0:
                hold_reason = "Preço não está em zona de suporte/resistência"
            elif criteria_met == 1:
                hold_reason = "Pavio não é suficientemente longo"
            elif criteria_met == 2:
                hold_reason = "Fechamento não confirma rejeição"

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
                "close": round(close, 4),
                "atr": round(atr, 6),
                "high": round(high, 4),
                "low": round(low, 4),
                "open": round(open_price, 4),
                "wick_upper": round(wick_upper, 4),
                "wick_lower": round(wick_lower, 4),
                "body": round(body, 4),
                "support": round(support, 4),
                "resistance": round(resistance, 4),
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