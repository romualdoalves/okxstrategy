"""Estratégia PA007 - Supply Demand Crypto
Gerado pela Fábrica de Estratégias
Data de criação: 2024-01-01
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class SupplyDemandCryptoStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="PA007",
            name="PA007 - Supply Demand Crypto",
            description="Estratégia de oferta e demanda adaptada para criptomoedas. Seleciona altcoins com volume e tendência forte, mapeia zonas em 4H, executa em 15M/5M com confirmação de quebra de estrutura e volume. Gestão de risco com stop abaixo do pavio e alvo na próxima zona.",
            tags=["trend", "breakout", "volume", "swing"],
            recommended_timeframe="15m",
            criteria=[
                {"id": "c1_bias", "label": "C1 Viés", "description": "EMA rápida e lenta definem direção."},
                {"id": "c2_volume", "label": "C2 Volume", "description": "Volume acima da média."},
                {"id": "c3_zone", "label": "C3 Zona", "description": "Preço toca zona de oferta/demanda."},
                {"id": "c4_choch", "label": "C4 CHoCH", "description": "Quebra de estrutura confirma entrada."},
            ],
            params={
                "atr_period": ParamDef(type="int", default=14, min=5, max=50, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Trailing Stop após TP1"),
                "ema_fast": ParamDef(type="int", default=20, min=5, max=50, step=1, description="Período da EMA rápida para tendência"),
                "ema_slow": ParamDef(type="int", default=50, min=20, max=200, step=1, description="Período da EMA lenta para tendência"),
                "volume_ma_period": ParamDef(type="int", default=20, min=5, max=50, step=1, description="Período da média móvel de volume"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.atr_period = p["atr_period"].default
        self.sl_mult = p["sl_mult"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default
        self.ema_fast = p["ema_fast"].default
        self.ema_slow = p["ema_slow"].default
        self.volume_ma_period = p["volume_ma_period"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles: list, context=None) -> Optional[StrategyResult]:
        min_candles = max(self.ema_slow + 5, self.volume_ma_period + 5, 100)
        if len(candles) < min_candles:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_candles} velas ({len(candles)} recebidas)",
                indicators={
                    "close": float(candles[-1].close) if candles else 0.0,
                    "atr": 0.0,
                    "ema_fast": 0.0,
                    "ema_slow": 0.0,
                    "volume_ma": 0.0,
                    "bias": 0.0,
                },
            )

        df = pd.DataFrame([
            {"high": c.high, "low": c.low, "close": c.close, "volume": c.volume}
            for c in candles
        ])

        close = float(df["close"].iloc[-1])
        volume = float(df["volume"].iloc[-1])

        # Calcular EMAs
        ema_fast_series = ta.ema(df["close"], length=self.ema_fast)
        ema_slow_series = ta.ema(df["close"], length=self.ema_slow)

        # Calcular ATR
        atr_series = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)

        # Calcular média móvel de volume
        volume_ma_series = ta.sma(df["volume"], length=self.volume_ma_period)

        if ema_fast_series is None or ema_slow_series is None or atr_series is None or volume_ma_series is None:
            return None

        ema_fast_val = float(ema_fast_series.iloc[-1])
        ema_slow_val = float(ema_slow_series.iloc[-1])
        atr = float(atr_series.iloc[-1])
        volume_ma = float(volume_ma_series.iloc[-1])

        # Determinar viés (bias)
        if ema_fast_val > ema_slow_val:
            bias = 1.0
        elif ema_fast_val < ema_slow_val:
            bias = -1.0
        else:
            bias = 0.0

        # Critérios
        criteria_met = 0
        criteria_total = 4

        # C1: Viés
        c1_met = bias != 0.0
        if c1_met:
            criteria_met += 1

        # C2: Volume
        c2_met = volume > volume_ma
        if c2_met:
            criteria_met += 1

        # C3: Toque na Zona - identificar swing recente
        # Para compra: preço tocou zona de demanda (low de um swing low recente)
        # Para venda: preço tocou zona de oferta (high de um swing high recente)
        swing_low = float(df["low"].iloc[-20:].min()) if len(df) >= 20 else float(df["low"].min())
        swing_high = float(df["high"].iloc[-20:].max()) if len(df) >= 20 else float(df["high"].max())

        if bias == 1.0:
            # Zona de demanda: próximo ao swing low
            zone_touch = close <= swing_low * 1.02  # 2% acima do swing low
        elif bias == -1.0:
            # Zona de oferta: próximo ao swing high
            zone_touch = close >= swing_high * 0.98  # 2% abaixo do swing high
        else:
            zone_touch = False

        c3_met = zone_touch
        if c3_met:
            criteria_met += 1

        # C4: Quebra de Estrutura (CHoCH)
        # Para compra: close > previous swing high (últimos 5 candles)
        # Para venda: close < previous swing low (últimos 5 candles)
        if len(df) >= 10:
            recent_high = float(df["high"].iloc[-6:-1].max())  # últimos 5 candles antes do atual
            recent_low = float(df["low"].iloc[-6:-1].min())
        else:
            recent_high = float(df["high"].max())
            recent_low = float(df["low"].min())

        if bias == 1.0:
            choch = close > recent_high
        elif bias == -1.0:
            choch = close < recent_low
        else:
            choch = False

        c4_met = choch
        if c4_met:
            criteria_met += 1

        # Decisão de sinal
        signal = Signal.HOLD
        hold_reason = ""

        if bias == 1.0 and c2_met and c3_met and c4_met:
            signal = Signal.BUY
        elif bias == -1.0 and c2_met and c3_met and c4_met:
            signal = Signal.SELL
        else:
            if not c1_met:
                hold_reason = "Mercado sem direção clara (EMA fast próxima da slow)"
            elif not c2_met:
                hold_reason = "Volume insuficiente"
            elif not c3_met:
                hold_reason = "Preço não tocou zona relevante"
            elif not c4_met:
                hold_reason = "Aguardando confirmação de quebra de estrutura"
            else:
                hold_reason = "Condições não atendidas para entrada"

        # Calcular SL e TP
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
                "ema_fast": round(ema_fast_val, 4),
                "ema_slow": round(ema_slow_val, 4),
                "volume_ma": round(volume_ma, 4),
                "bias": round(bias, 1),
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
