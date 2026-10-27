"""TF018 - Sistema Multicamadas Híbrido e Ortogonal - MR e Momentum

Estratégia multicamadas que combina reversão à média (RSI+Keltner) em consolidação e momentum baseado em força relativa, com detecção de regime via ADX, filtro de volume e dimensionamento por ATR.
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.indicators import session_vwap
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class SistemaHibridoMrEMomentumStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF018",
            name="TF018 - Sistema Multicamadas Híbrido e Ortogonal - MR e Momentum",
            description="Estratégia multicamadas que combina reversão à média (RSI+Keltner) em consolidação e momentum baseado em força relativa, com detecção de regime via ADX, filtro de volume e dimensionamento por ATR.",
            tags=["trend", "momentum", "mean-reversion", "multi-tf"],
            recommended_timeframe="1d",
            criteria=[
                {"id": "c1_regime", "label": "C1 Regime", "description": "Regime identificado via ADX (Tendência vs Consolidação)"},
                {"id": "c2_signal", "label": "C2 Sinal", "description": "Gatilho técnico do motor ativo atendido (MR ou Momentum)"},
                {"id": "c3_volume", "label": "C3 Volume", "description": "Confirmação de fluxo/volume acima da média ou proximidade do VWAP"},
            ],
            params={
                "rsi_period": ParamDef(type="int", default=2, min=2, max=14, step=1, description="Período do RSI para reversão à média"),
                "rsi_oversold": ParamDef(type="int", default=10, min=5, max=30, step=1, description="Limiar de sobrevenda do RSI"),
                "keltner_period": ParamDef(type="int", default=20, min=10, max=50, step=1, description="Período das Bandas de Keltner"),
                "keltner_mult": ParamDef(type="float", default=2.5, min=1.0, max=4.0, step=0.1, description="Multiplicador das Bandas de Keltner"),
                "roc_period": ParamDef(type="int", default=252, min=20, max=500, step=1, description="Período da Taxa de Variação para Momentum"),
                "regime_lookback": ParamDef(type="int", default=50, min=20, max=200, step=1, description="Lookback do ADX para detecção de regime"),
                "regime_threshold": ParamDef(type="float", default=25.0, min=15.0, max=40.0, step=1.0, description="Limiar ADX para classificar tendência"),
                "atr_period": ParamDef(type="int", default=14, min=7, max=30, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Trailing Stop"),
            },
        )

    def __init__(self):
        super().__init__()
        p = self.info().params
        self.rsi_period = p["rsi_period"].default
        self.rsi_oversold = p["rsi_oversold"].default
        self.keltner_period = p["keltner_period"].default
        self.keltner_mult = p["keltner_mult"].default
        self.roc_period = p["roc_period"].default
        self.regime_lookback = p["regime_lookback"].default
        self.regime_threshold = p["regime_threshold"].default
        self.atr_period = p["atr_period"].default
        self.sl_mult = p["sl_mult"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default

    def set_params(self, params) -> None:
        if isinstance(params, dict):
            for k, v in params.items():
                if hasattr(self, k):
                    setattr(self, k, v)

    def compute(self, candles) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles, context=None) -> Optional[StrategyResult]:
        # Criamos o DataFrame imediatamente para alimentar o cálculo de segurança do ATR desde o início
        df = pd.DataFrame([{
            "high": float(c.high), 
            "low": float(c.low), 
            "close": float(c.close), 
            "volume": float(c.volume)
        } for c in candles])

        # Calcule: atr_s = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        atr_s = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        
        # Garante um valor numérico dinâmico válido mesmo se a série falhar por falta de histórico
        current_atr = 0.0
        if atr_s is not None and not atr_s.empty:
            current_atr = float(atr_s.iloc[-1]) if not pd.isna(atr_s.iloc[-1]) else 0.0

        min_candles = max(self.keltner_period, self.regime_lookback, self.atr_period, self.roc_period) + 25
        if len(candles) < min_candles:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_candles} velas ({len(candles)} recebidas)",
                # CORREÇÃO CRÍTICA: indicators["atr"] recebe a variável dinâmica real mesmo em estado de aquecimento
                indicators={"close": float(candles[-1].close), "rsi": 50.0, "keltner_lower": 0.0, "keltner_mid": 0.0, "adx": 0.0, "atr": current_atr, "volume": float(candles[-1].volume), "vwap": float(candles[-1].close)},
                metadata={"sl_price": 0.0, "tp1_price": 0.0, "sl_pct": 0.0, "tp1_pct": 0.0, "ts_pct": 0.0},
                criteria_met=0,
                criteria_total=3,
            )

        rsi_series = ta.rsi(df["close"], length=self.rsi_period)
        kc = ta.kc(df["high"], df["low"], df["close"], length=self.keltner_period, scalar=self.keltner_mult)
        adx_series = ta.adx(df["high"], df["low"], df["close"], length=self.regime_lookback)
        vwap_series = session_vwap(candles)   # ta.vwap exige DatetimeIndex e devolvia None (a estratégia nunca operava)

        if rsi_series is None or kc is None or adx_series is None or vwap_series is None:
            return None

        rsi = float(rsi_series.iloc[-1])
        keltner_lower = float(kc.iloc[-1, 0])
        keltner_mid = float(kc.iloc[-1, 1])
        keltner_upper = float(kc.iloc[-1, 2])
        adx = float(adx_series.iloc[-1, 0])
        vwap = float(vwap_series.iloc[-1])

        close = float(candles[-1].close)
        volume = float(candles[-1].volume)
        avg_volume = float(df["volume"].rolling(window=20).mean().iloc[-1])

        regime_trend = adx >= self.regime_threshold
        regime_consolidation = adx < self.regime_threshold
        c1_met = 1  

        c2_met = 0
        signal = Signal.HOLD
        hold_reason = ""

        if regime_consolidation:
            if close < keltner_lower and rsi < self.rsi_oversold:
                c2_met = 1
                signal = Signal.BUY
            elif close > keltner_upper and rsi > (100 - self.rsi_oversold):
                c2_met = 1
                signal = Signal.SELL
        elif regime_trend:
            roc_series = ta.roc(df["close"], length=self.roc_period)
            if roc_series is not None:
                roc = float(roc_series.iloc[-1])
                if roc > 0 and close > keltner_mid:
                    c2_met = 1
                    signal = Signal.BUY
                elif roc < 0 and close < keltner_mid:
                    c2_met = 1
                    signal = Signal.SELL

        c3_met = 0
        if regime_consolidation:
            if volume > (avg_volume * 1.2) or (abs(close - vwap) / vwap < 0.01):
                c3_met = 1
        elif regime_trend:
            if volume > avg_volume:
                c3_met = 1

        criteria_met = c1_met + c2_met + c3_met
        criteria_total = 3

        if criteria_met < criteria_total:
            signal = Signal.HOLD
            if c2_met == 0:
                hold_reason = "Sinal técnico (MR/Momentum) não disparado nos limites atuais"
            elif c3_met == 0:
                hold_reason = "Sinal técnico rejeitado pelo filtro de Volume/VWAP"

        sl_dist = current_atr * self.sl_mult
        if signal == Signal.BUY:
            sl_price = round(close - sl_dist, 4)
            tp1_price = round(close + (sl_dist * self.tp1_rr), 4)
        elif signal == Signal.SELL:
            sl_price = round(close + sl_dist, 4)
            tp1_price = round(close - (sl_dist * self.tp1_rr), 4)
        else:
            sl_price = 0.0
            tp1_price = 0.0

        return StrategyResult(
            signal=signal,
            hold_reason=hold_reason,
            indicators={
                "rsi": round(rsi, 2),
                "keltner_lower": round(keltner_lower, 4),
                "keltner_mid": round(keltner_mid, 4),
                "keltner_upper": round(keltner_upper, 4),
                "adx": round(adx, 2),
                "atr": round(current_atr, 6),  
                "close": round(close, 4),
                "volume": float(volume),
                "vwap": round(vwap, 4),
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(sl_dist / close, 5) if close != 0 else 0.0,
                "tp1_pct": round((sl_dist * self.tp1_rr) / close, 5) if close != 0 else 0.0,
                "ts_pct": round((current_atr * self.ts_mult) / close, 5) if close != 0 else 0.0,
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )