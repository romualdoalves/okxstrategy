"""PA010 - ORB Breakout 5min Strategy
Gerado pela Fábrica de Estratégias
Data de criação: 2024-01-01
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class OrbBreakout5minStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="PA010",
            name="PA010 - ORB Breakout 5min",
            description="Estratégia de rompimento do range de abertura (ORB) nos primeiros 20 minutos do mercado americano. Opera o rompimento com candle de força, usando stop na abertura do candle sinal e alvo 2x o risco.",
            tags=["breakout", "intraday", "pattern"],
            recommended_timeframe="5m",
            criteria=[
                {"id": "c1_orb_formed", "label": "C1 Range formado", "description": "Range de abertura definido com os primeiros N candles"},
                {"id": "c2_breakout", "label": "C2 Rompimento", "description": "Preço fecha fora do range"},
                {"id": "c3_strong_candle", "label": "C3 Candle de força", "description": "Corpo > avg_body * body_ratio e corpo > sombra * wick_ratio"},
            ],
            params={
                "orb_candles": ParamDef(type="int", default=4, min=2, max=10, step=1, description="Número de candles para formar o range de abertura"),
                "body_ratio": ParamDef(type="float", default=1.5, min=1, max=3, step=0.1, description="Relação mínima entre corpo do candle de força e média dos corpos anteriores"),
                "wick_ratio": ParamDef(type="float", default=0.5, min=0.1, max=1, step=0.1, description="Relação máxima entre sombra e corpo do candle de força"),
                "atr_period": ParamDef(type="int", default=14, min=5, max=50, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2, min=1, max=5, step=0.1, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=2, min=1, max=5, step=0.1, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=3, min=1, max=5, step=0.1, description="Multiplicador ATR para Trailing Stop após TP1"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.orb_candles = p["orb_candles"].default
        self.body_ratio = p["body_ratio"].default
        self.wick_ratio = p["wick_ratio"].default
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

    def compute_with_context(self, candles, context=None):
        min_c = self.orb_candles + self.atr_period + 5
        if len(candles) < min_c:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_c} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "orb_high": 0.0, "orb_low": 0.0, "avg_body": 0.0, "atr": 0.0},
            )

        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close, "open": c.open, "volume": c.volume} for c in candles])
        
        # Calcular ATR
        atr_s = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        if atr_s is None:
            return None
        atr = float(atr_s.iloc[-1])
        
        # Calcular range de abertura (primeiros orb_candles candles)
        orb_high = float(df["high"].iloc[:self.orb_candles].max())
        orb_low = float(df["low"].iloc[:self.orb_candles].min())
        
        # Calcular média dos corpos dos últimos candles (excluindo o atual)
        body_sizes = []
        for i in range(1, min(self.orb_candles + 5, len(df))):
            body = abs(float(df["close"].iloc[-i]) - float(df["open"].iloc[-i]))
            body_sizes.append(body)
        avg_body = float(pd.Series(body_sizes).mean()) if body_sizes else 0.0
        
        # Dados do candle atual
        close = float(candles[-1].close)
        open_price = float(candles[-1].open)
        high = float(candles[-1].high)
        low = float(candles[-1].low)
        
        # Calcular corpo e sombra do candle atual
        body = abs(close - open_price)
        upper_wick = high - max(close, open_price)
        lower_wick = min(close, open_price) - low
        total_wick = upper_wick + lower_wick
        
        # Critérios
        criteria_met = 0
        criteria_total=3
        
        # C1: Range formado
        c1_orb_formed = orb_high > orb_low and orb_high > 0 and orb_low > 0
        if c1_orb_formed:
            criteria_met += 1
        
        # C2: Rompimento
        c2_breakout_buy = close > orb_high
        c2_breakout_sell = close < orb_low
        c2_breakout = c2_breakout_buy or c2_breakout_sell
        if c2_breakout:
            criteria_met += 1
        
        # C3: Candle de força
        c3_strong_candle = False
        if body > 0 and avg_body > 0:
            c3_strong_candle = (body > avg_body * self.body_ratio) and (total_wick <= body * self.wick_ratio)
        if c3_strong_candle:
            criteria_met += 1
        
        # Determinar sinal
        signal = Signal.HOLD
        hold_reason = ""
        
        if not c1_orb_formed:
            hold_reason = "Range de abertura não formado"
        elif not c2_breakout:
            hold_reason = "Aguardando rompimento"
        elif not c3_strong_candle:
            hold_reason = "Candle sem força suficiente"
        else:
            if c2_breakout_buy and c3_strong_candle:
                signal = Signal.BUY
            elif c2_breakout_sell and c3_strong_candle:
                signal = Signal.SELL
        
        # Calcular stops e targets
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
                "orb_high": round(orb_high, 4),
                "orb_low": round(orb_low, 4),
                "avg_body": round(avg_body, 6),
                "atr": round(atr, 6),
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(sl_dist / close, 5) if close > 0 else 0.0,
                "tp1_pct": round(sl_dist * self.tp1_rr / close, 5) if close > 0 else 0.0,
                "ts_pct": round(atr * self.ts_mult / close, 5) if close > 0 else 0.0,
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )