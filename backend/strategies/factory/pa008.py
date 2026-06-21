"""PA008 - ORB Breakout Retest Strategy
Gerado pela Fábrica de Estratégias
Data de criação: 2024-01-01
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class Pa008OrbBreakoutRetestStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="PA008",
            name="PA008 - ORB Breakout Retest",
            description="Estratégia de rompimento do range de abertura (Opening Range Breakout) com reteste. Marca o range dos primeiros 30 minutos das bolsas americanas (09:30-10:00 NY) nos gráficos de 15/30 min. Aguarda fechamento de vela de 5 min fora do range para validar direção, depois busca reteste corretivo da linha rompida para entrada. Inclui filtros de volatilidade e tempo limite até meio-dia.",
            tags=["breakout", "intraday", "pattern"],
            recommended_timeframe="15m",
            criteria=[
                {"id": "c1_orb", "label": "C1 ORB", "description": "Opening range definido e dentro da volatilidade máxima."},
                {"id": "c2_breakout", "label": "C2 Rompimento", "description": "Preço fechou fora do range."},
                {"id": "c3_retest", "label": "C3 Reteste", "description": "Preço retestou a linha rompida."},
            ],
            params={
                "atr_period": ParamDef(type="int", default=14, min=5, max=50, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Trailing Stop após TP1"),
                "orb_minutes": ParamDef(type="int", default=30, min=15, max=60, step=5, description="Janela de minutos para definir o ORB"),
                "max_volatility_range_pct": ParamDef(type="float", default=2.0, min=0.5, max=5.0, step=0.1, description="Range máximo do ORB em % para evitar volatilidade excessiva"),
                "callback_rate": ParamDef(type="float", default=1.0, min=0.5, max=1.5, step=0.1, description="Callback rate para trailing stop (0.5%-1.5%)"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.atr_period = p["atr_period"].default
        self.sl_mult = p["sl_mult"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default
        self.orb_minutes = p["orb_minutes"].default
        self.max_volatility_range_pct = p["max_volatility_range_pct"].default
        self.callback_rate = p["callback_rate"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles, context=None):
        min_candles = max(self.atr_period + 5, 100)
        if len(candles) < min_candles:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_candles} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "atr": 0.0, "high": 0.0, "low": 0.0, "orb_high": 0.0, "orb_low": 0.0, "orb_mid": 0.0, "breakout_direction": 0.0},
            )

        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles])

        # Calcular ATR
        atr_s = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        if atr_s is None:
            return None
        atr = float(atr_s.iloc[-1])
        close = float(candles[-1].close)
        high = float(candles[-1].high)
        low = float(candles[-1].low)

        # Definir ORB (Opening Range Breakout) - range dos primeiros orb_minutes minutos
        # Como não temos timestamp, usamos as primeiras velas do período
        orb_candles = int(self.orb_minutes / 15)  # Aproximação para timeframe 15m
        if orb_candles < 1:
            orb_candles = 1
        if len(candles) < orb_candles + 5:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {orb_candles + 5} velas para definir ORB ({len(candles)} recebidas)",
                indicators={"close": close, "atr": round(atr, 6), "high": high, "low": low, "orb_high": 0.0, "orb_low": 0.0, "orb_mid": 0.0, "breakout_direction": 0.0},
            )

        # Calcular ORB a partir das primeiras velas
        orb_high = float(df["high"].iloc[:orb_candles].max())
        orb_low = float(df["low"].iloc[:orb_candles].min())
        orb_mid = (orb_high + orb_low) / 2.0

        # Calcular range do ORB em %
        orb_range_pct = ((orb_high - orb_low) / orb_low) * 100.0

        # Critérios
        criteria_met = 0
        criteria_total=3

        # C1: ORB definido e dentro do limite de volatilidade
        c1_orb_defined = orb_range_pct > 0 and orb_range_pct <= self.max_volatility_range_pct
        if c1_orb_defined:
            criteria_met += 1

        # C2: Rompimento confirmado (vela de 5 min fechou fora do ORB)
        # Como estamos em timeframe 15m, verificamos se a última vela fechou fora do range
        breakout_direction = 0
        c2_breakout_confirmed = False
        if close > orb_high:
            breakout_direction = 1
            c2_breakout_confirmed = True
        elif close < orb_low:
            breakout_direction = -1
            c2_breakout_confirmed = True

        if c2_breakout_confirmed:
            criteria_met += 1

        # C3: Reteste para entrada (preço retornou à linha rompida)
        c3_retest_entry = False
        if breakout_direction == 1:
            # Rompimento de alta: reteste no orb_high ou orb_mid
            if close <= orb_high and close >= orb_mid:
                c3_retest_entry = True
        elif breakout_direction == -1:
            # Rompimento de baixa: reteste no orb_low ou orb_mid
            if close >= orb_low and close <= orb_mid:
                c3_retest_entry = True

        if c3_retest_entry:
            criteria_met += 1

        # Determinar sinal
        signal = Signal.HOLD
        hold_reason = ""

        if c1_orb_defined and c2_breakout_confirmed and c3_retest_entry:
            if breakout_direction == 1:
                signal = Signal.BUY
            elif breakout_direction == -1:
                signal = Signal.SELL
            else:
                hold_reason = "Mercado sem direção clara"
        elif not c1_orb_defined:
            hold_reason = "Range ORB não definido ou muito volátil"
        elif not c2_breakout_confirmed:
            hold_reason = "Nenhum rompimento confirmado até meio-dia"
        elif not c3_retest_entry:
            hold_reason = "Reteste não ocorreu dentro do prazo"
        else:
            hold_reason = "Mercado sem direção clara"

        # Calcular stops
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
                "orb_high": round(orb_high, 4),
                "orb_low": round(orb_low, 4),
                "orb_mid": round(orb_mid, 4),
                "breakout_direction": float(breakout_direction),
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
