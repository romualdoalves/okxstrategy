"""SC004 - Gold Scalp Structure Strategy
Gerado pela Fábrica de Estratégias
Scalping strategy for gold using RSI momentum filter and swing structure bands.
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class GoldScalpStructureStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="SC004",
            name="SC004 - Gold Scalp Structure",
            description="Scalping strategy for gold using RSI momentum filter and swing structure bands. Only trades when RSI-based MA is outside the 44-54 neutral zone and price has broken a swing high/low with structure confirmation. Uses 1:1.5 risk-reward.",
            tags=["scalp", "breakout", "momentum", "pattern"],
            recommended_timeframe="5m",
            criteria=[
                {"id": "c1_structure", "label": "C1 Estrutura", "description": "Preço está acima (buy) ou abaixo (sell) das bandas de estrutura completas"},
                {"id": "c2_breakout", "label": "C2 Breakout", "description": "Preço rompeu o último swing high (buy) ou swing low (sell) anterior"},
                {"id": "c3_momentum", "label": "C3 Momentum", "description": "RSI MA está acima do limite superior (buy) ou abaixo do limite inferior (sell)"},
            ],
            params={
                "rsi_length": ParamDef(type="int", default=14, min=5, max=30, step=1, description="Período do RSI para o filtro de momentum"),
                "rsi_upper": ParamDef(type="float", default=54, min=50, max=70, step=1, description="Limite superior da zona de momentum bullish"),
                "rsi_lower": ParamDef(type="float", default=44, min=30, max=50, step=1, description="Limite inferior da zona de momentum bearish"),
                "swing_period": ParamDef(type="int", default=10, min=5, max=30, step=1, description="Período para identificação de swing highs/lows locais"),
                "atr_period": ParamDef(type="int", default=14, min=5, max=50, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2, min=1, max=5, step=0.5, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=1.5, min=1, max=5, step=0.5, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=3, min=1, max=5, step=0.5, description="Multiplicador ATR para Trailing Stop após TP1"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.rsi_length = p["rsi_length"].default
        self.rsi_upper = p["rsi_upper"].default
        self.rsi_lower = p["rsi_lower"].default
        self.swing_period = p["swing_period"].default
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
        # Janela de aquecimento ajustada para comportar o rolling empilhado das bandas (swing_period * 3)
        min_c = max(self.swing_period * 3 + 5, self.atr_period + 5, 50)
        if len(candles) < min_c:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_c} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "rsi_ma": 50.0, "swing_high": 0.0, "swing_low": 0.0, "structure_band_upper": 0.0, "structure_band_lower": 0.0, "atr": 0.0},
            )

        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles])

        # RSI e sua Média Móvel Simples (RSI-based MA)
        rsi_series = ta.rsi(df["close"], length=self.rsi_length)
        if rsi_series is None:
            return None
        rsi_ma_series = ta.sma(rsi_series, length=self.rsi_length)
        if rsi_ma_series is None:
            return None

        # ATR para volatilidade e stop dinâmico
        atr_series = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        if atr_series is None:
            return None

        # Identificação de canais/swings locais
        high_series = df["high"]
        low_series = df["low"]
        swing_highs = high_series.rolling(window=self.swing_period).max()
        swing_lows = low_series.rolling(window=self.swing_period).min()

        # Bandas de Estrutura: Janela macro baseada no dobro do período de swing para evitar sobreposição estrita
        structure_band_upper = swing_highs.rolling(window=self.swing_period * 2).max()
        structure_band_lower = swing_lows.rolling(window=self.swing_period * 2).min()

        # Extração de valores atuais para os osciladores/preço
        rsi_ma = float(rsi_ma_series.iloc[-1])
        atr = float(atr_series.iloc[-1])
        close = float(candles[-1].close)
        
        # APLICAÇÃO DE SHIFT(1): Compara o close atual com as barreiras consolidadas até a vela passada
        swing_high = float(swing_highs.shift(1).iloc[-1])
        swing_low = float(swing_lows.shift(1).iloc[-1])
        band_upper = float(structure_band_upper.shift(1).iloc[-1])
        band_lower = float(structure_band_lower.shift(1).iloc[-1])

        # Verificação preventiva de NaNs originados pelas janelas rolling expandidas
        if any(math.isnan(v) for v in [rsi_ma, atr, swing_high, swing_low, band_upper, band_lower]):
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Aquecendo — indicadores estruturais ainda não disponíveis",
                indicators={"close": close, "rsi_ma": rsi_ma, "swing_high": swing_high, "swing_low": swing_low, "structure_band_upper": band_upper, "structure_band_lower": band_lower, "atr": atr},
            )

        # Avaliação de critérios pontuais para rastreamento no painel
        criteria_met = 0
        criteria_total = 3

        # C1: Estrutura Macrocêntrica
        c1_buy = close > band_upper
        c1_sell = close < band_lower
        if c1_buy or c1_sell:
            criteria_met += 1

        # C2: Rompimento do Swing Local
        c2_buy = close > swing_high
        c2_sell = close < swing_low
        if c2_buy or c2_sell:
            criteria_met += 1

        # C3: Filtro Opcional de Momentum Ativo via RSI MA
        c3_buy = rsi_ma > self.rsi_upper
        c3_sell = rsi_ma < self.rsi_lower
        if c3_buy or c3_sell:
            criteria_met += 1

        # Validação de gatilhos unificados (Execution Rules)
        buy_signal = c1_buy and c2_buy and c3_buy
        sell_signal = c1_sell and c2_sell and c3_sell

        if buy_signal:
            signal = Signal.BUY
            hold_reason = ""
        elif sell_signal:
            signal = Signal.SELL
            hold_reason = ""
        else:
            signal = Signal.HOLD
            if not (c1_buy or c1_sell):
                hold_reason = "Preço em consolidação dentro das bandas macro de estrutura"
            elif not (c2_buy or c2_sell):
                hold_reason = "Estrutura favorável, aguardando gatilho de rompimento de swing"
            else:
                hold_reason = "Estrutura e rompimento confirmados, mas RSI MA retido na zona neutra (44-54)"

        # Dimensionamento de alvos (Risk Management) baseado em ATR
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
                "rsi_ma": round(rsi_ma, 2),
                "swing_high": round(swing_high, 4),
                "swing_low": round(swing_low, 4),
                "structure_band_upper": round(band_upper, 4),
                "structure_band_lower": round(band_lower, 4),
                "close": round(close, 4),
                "atr": round(atr, 6),
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