"""MR008 - Keltner Exhaustion Strategy
Gerado pela Fábrica de Estratégias
Data de criação: 2024-01-01
Versão Corrigida: 2026 (Foco em Candles Fechados)
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class KeltnerExhaustionStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="MR008",
            name="MR008 - Keltner Exhaustion",
            description="Estratégia de reversão baseada em exaustão de momentum com Keltner Channels e MACD. Identifica toques extremos nas bandas de Keltner (2.5 ou 3.0 ATR) seguidos de desaceleração do histograma MACD, indicando perda de força compradora/vendedora. Ideal para timeframes 15m-1h.",
            tags=["mean-reversion", "momentum", "reversal", "volume"],
            recommended_timeframe="15m",
            criteria=[
                {"id": "c1_extreme", "label": "C1 Toque Extremo", "description": "Preço toca ou ultrapassa a banda externa de Keltner (2.5 ou 3.0 ATR)"},
                {"id": "c2_macd_slowdown", "label": "C2 Desaceleração MACD", "description": "Histograma MACD diminui em relação ao candle anterior (perda de momentum)"},
                {"id": "c3_rejection", "label": "C3 Rejeição", "description": "Candle de reversão com pavio na banda externa (opcional, mas aumenta assertividade)"},
            ],
            params={
                "kc_ema_period": ParamDef(type="int", default=20, min=10, max=50, step=1, description="Período da EMA base dos Keltner Channels"),
                "kc_multiplier": ParamDef(type="float", default=2.5, min=2, max=3, step=0.1, description="Multiplicador do ATR para as bandas de Keltner"),
                "macd_fast": ParamDef(type="int", default=12, min=5, max=20, step=1, description="Período rápido do MACD"),
                "macd_slow": ParamDef(type="int", default=26, min=15, max=40, step=1, description="Período lento do MACD"),
                "macd_signal": ParamDef(type="int", default=9, min=5, max=15, step=1, description="Período da linha de sinal do MACD"),
                "atr_period": ParamDef(type="int", default=14, min=7, max=30, step=1, description="Período do ATR para cálculo de volatilidade e gestão de risco"),
                "sl_mult": ParamDef(type="float", default=1.0, min=0.5, max=3.0, step=0.1, description="Multiplicador ATR para Stop Loss (1x ATR)"),
                "tp1_rr": ParamDef(type="float", default=1.5, min=1.0, max=3.0, step=0.1, description="Relação risco-retorno do TP1 (para a média do Keltner)"),
                "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.1, description="Multiplicador ATR para Trailing Stop após TP1"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.kc_ema_period = p["kc_ema_period"].default
        self.kc_multiplier = p["kc_multiplier"].default
        self.macd_fast = p["macd_fast"].default
        self.macd_slow = p["macd_slow"].default
        self.macd_signal = p["macd_signal"].default
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
        min_candles = max(self.kc_ema_period + 5, self.macd_slow + self.macd_signal + 5, 60)
        if len(candles) < min_candles:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_candles} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "kc_upper": 0.0, "kc_lower": 0.0, "kc_mid": 0.0, "macd_histogram": 0.0, "macd_histogram_prev": 0.0, "atr": 0.0},
            )

        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles])

        # Keltner Channels
        ema = ta.ema(df["close"], length=self.kc_ema_period)
        atr_series = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)

        if ema is None or atr_series is None:
            return None

        kc_mid = ema
        kc_upper = ema + self.kc_multiplier * atr_series
        kc_lower = ema - self.kc_multiplier * atr_series

        # MACD
        macd_result = ta.macd(df["close"], fast=self.macd_fast, slow=self.macd_slow, signal=self.macd_signal)
        if macd_result is None:
            return None

        macd_histogram = macd_result.iloc[:, 2]  # MACD histogram (MACD - Signal)

        # ====================================================================
        # AJUSTE DE ESTABILIDADE: Coleta baseada no último candle FECHADO (-2)
        # Ignora o índice [-1] que representa o tick em tempo real imprevisível.
        # ====================================================================
        close = float(candles[-2].close)
        high = float(candles[-2].high)
        low = float(candles[-2].low)
        atr = float(atr_series.iloc[-2])
        kc_upper_val = float(kc_upper.iloc[-2])
        kc_lower_val = float(kc_lower.iloc[-2])
        kc_mid_val = float(kc_mid.iloc[-2])
        
        macd_hist_val = float(macd_histogram.iloc[-2])
        macd_hist_prev = float(macd_histogram.iloc[-3]) if len(macd_histogram) > 2 else 0.0

        # Criteria evaluation (Focado no candle fechado de índice -2)
        criteria_met = 0
        criteria_total = 3

        # C1: Toque Extremo
        c1_buy = low <= kc_lower_val
        c1_sell = high >= kc_upper_val
        if c1_buy or c1_sell:
            criteria_met += 1

        # C2: Desaceleração MACD
        c2_buy = macd_hist_val < 0 and macd_hist_val > macd_hist_prev
        c2_sell = macd_hist_val > 0 and macd_hist_val < macd_hist_prev
        if c2_buy or c2_sell:
            criteria_met += 1

        # C3: Rejeição (pavio na banda externa)
        c3_buy = low <= kc_lower_val and high > kc_lower_val
        c3_sell = high >= kc_upper_val and low < kc_upper_val
        if c3_buy or c3_sell:
            criteria_met += 1

        # Signal determination
        signal = Signal.HOLD
        hold_reason = ""

        if c1_buy and c2_buy:
            signal = Signal.BUY
        elif c1_sell and c2_sell:
            signal = Signal.SELL
        else:
            if not c1_buy and not c1_sell:
                hold_reason = "Preço dentro das bandas de Keltner"
            elif not c2_buy and not c2_sell:
                hold_reason = "MACD sem desaceleração"
            else:
                hold_reason = "Aguardando toque extremo e confirmação"

        # Calculate SL and TP com base na volatilidade real do candle fechado
        if signal == Signal.BUY:
            sl_dist = atr * self.sl_mult
            sl_price = round(min(low - sl_dist, low - atr * self.sl_mult), 4)
            tp1_price = round(kc_mid_val + (kc_mid_val - sl_price) * self.tp1_rr, 4)
        elif signal == Signal.SELL:
            sl_dist = atr * self.sl_mult
            sl_price = round(max(high + sl_dist, high + atr * self.sl_mult), 4)
            tp1_price = round(kc_mid_val - (sl_price - kc_mid_val) * self.tp1_rr, 4)
        else:
            sl_price = 0.0
            tp1_price = 0.0
            sl_dist = 0.0

        return StrategyResult(
            signal=signal,
            hold_reason=hold_reason,
            indicators={
                "kc_upper": round(kc_upper_val, 4),
                "kc_lower": round(kc_lower_val, 4),
                "kc_mid": round(kc_mid_val, 4),
                "macd_histogram": round(macd_hist_val, 6),
                "macd_histogram_prev": round(macd_hist_prev, 6),
                "atr": round(atr, 6),
                "close": round(close, 4),
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(sl_dist / close, 5) if close != 0 else 0.0,
                "tp1_pct": round(abs(tp1_price - close) / close, 5) if close != 0 else 0.0,
                "ts_pct": round(atr * self.ts_mult / close, 5) if close != 0 else 0.0,
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )