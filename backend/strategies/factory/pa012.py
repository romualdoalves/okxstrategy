"""PA012 - Caixa de Liquidez
Estratégia de Price Action baseada em liquidez institucional.
Gerado pela Fábrica de Estratégias
"""

from __future__ import annotations
from typing import Optional
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class CaixaDeLiquidezStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        c_list = [
            {"id": "c1_trend", "label": "C1 Tendência", "description": "Tendência definida por EMA9"},
            {"id": "c2_box", "label": "C2 Caixa Identificada", "description": "Última vela contrária mapeada"},
            {"id": "c3_pullback", "label": "C3 Múltiplos Pullbacks", "description": "Preço visitou o interior da caixa no histórico recente"},
            {"id": "c4_breakout", "label": "C4 Gatilho Confirmado", "description": "Vela atual fecha rompendo a caixa a favor do movimento"}
        ]
        
        p_dict = {
            "atr_period": ParamDef(type="int", default=14, min=5, max=50, step=1, description="Período do ATR"),
            "tp1_rr": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.1, description="Relação risco-retorno do TP1 baseado na estrutura"),
            "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.1, description="Multiplicador ATR para Trailing Stop"),
            "lookback": ParamDef(type="int", default=15, min=5, max=30, step=1, description="Janela para achar a última vela contrária"),
            "ny_filter": ParamDef(type="bool", default=True, description="Ativar filtro dos primeiros 5 minutos de NY")
        }
        
        return StrategyInfo(
            id="PA012",
            name="PA012 - Caixa de Liquidez",
            description="Mapeia a última vela contrária à tendência e aguarda o rompimento após o pullback.",
            tags=["pattern", "breakout", "intraday"],
            recommended_timeframe="1m",
            criteria=c_list,
            params=p_dict
        )

    def __init__(self):
        p = self.info().params
        self.atr_period = p["atr_period"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default
        self.lookback = p["lookback"].default
        self.ny_filter = p["ny_filter"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles, context=None):
        min_c = max(self.lookback + 10, self.atr_period + 5, 30)
        if len(candles) < min_c:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Aquecendo",
                indicators={
                    "close": float(candles[-1].close), 
                    "high": float(candles[-1].high), 
                    "low": float(candles[-1].low), 
                    "box_high": 0.0, 
                    "box_low": 0.0, 
                    "trend": 0.0, 
                    "atr": 0.0
                }
            )

        df = pd.DataFrame([
            {"high": float(c.high), "low": float(c.low), "close": float(c.close), "volume": float(c.volume), "open": float(c.open)}
            for c in candles
        ])

        atr_s = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        ema9_s = ta.ema(df["close"], length=9)
        
        if atr_s is None or ema9_s is None:
            return None

        atr = float(atr_s.iloc[-1])
        ema9_val = float(ema9_s.iloc[-1])
        ema9_prev = float(ema9_s.iloc[-2])

        close = float(df["close"].iloc[-1])
        high = float(df["high"].iloc[-1])
        low = float(df["low"].iloc[-1])
        prev_close = float(df["close"].iloc[-2])

        trend = 0.0
        if close > ema9_val and ema9_val > ema9_prev:
            trend = 1.0
        elif close < ema9_val and ema9_val < ema9_prev:
            trend = -1.0

        # Find last opposite candle
        box_high = 0.0
        box_low = 0.0
        for i in range(-2, -self.lookback - 2, -1):
            idx = len(df) + i
            if idx < 0:
                break
            candle_open = float(df["open"].iloc[idx])
            candle_close = float(df["close"].iloc[idx])
            candle_high = float(df["high"].iloc[idx])
            candle_low = float(df["low"].iloc[idx])
            
            if trend > 0 and candle_close < candle_open:
                box_high = candle_high
                box_low = candle_low
                break
            elif trend < 0 and candle_close > candle_open:
                box_high = candle_high
                box_low = candle_low
                break

        criteria_met = 0
        criteria_total=4

        # C1: Trend
        if trend != 0:
            criteria_met += 1

        # C2: Box identified
        if box_high > 0 and box_low > 0:
            criteria_met += 1

        # C3: Pullback - price visited inside box in recent history
        pullback_found = False
        if box_high > 0 and box_low > 0:
            for i in range(-3, -self.lookback - 3, -1):
                idx = len(df) + i
                if idx < 0:
                    break
                candle_low_i = float(df["low"].iloc[idx])
                candle_high_i = float(df["high"].iloc[idx])
                if candle_low_i < box_high and candle_high_i > box_low:
                    pullback_found = True
                    break
        if pullback_found:
            criteria_met += 1

        # C4: Breakout
        breakout = False
        if trend > 0 and box_high > 0 and close > box_high:
            breakout = True
        elif trend < 0 and box_low > 0 and close < box_low:
            breakout = True
        if breakout:
            criteria_met += 1

        signal = Signal.HOLD
        hold_reason = ""
        
        if criteria_met == criteria_total:
            if trend > 0:
                signal = Signal.BUY
            elif trend < 0:
                signal = Signal.SELL
            else:
                hold_reason = "Tendência indefinida"
        else:
            if trend == 0:
                hold_reason = "Sem tendência definida"
            elif box_high == 0:
                hold_reason = "Caixa de liquidez não identificada"
            elif not pullback_found:
                hold_reason = "Aguardando pullback na caixa"
            elif not breakout:
                hold_reason = "Aguardando rompimento da caixa"
            else:
                hold_reason = "Critérios não atendidos"

        sl_dist = atr * 1.5
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
                "high": round(high, 4),
                "low": round(low, 4),
                "box_high": round(box_high, 4),
                "box_low": round(box_low, 4),
                "trend": round(trend, 2),
                "atr": round(atr, 6)
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(sl_dist / close, 5) if close > 0 else 0.0,
                "tp1_pct": round(sl_dist * self.tp1_rr / close, 5) if close > 0 else 0.0,
                "ts_pct": round(atr * self.ts_mult / close, 5) if close > 0 else 0.0
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total
        )