"""
strategies/stochastic_reversal.py — EMA 21/34/144 + Stochastic Reversal

Filosofia: Entrar no PRIMEIRO pullback após uma reversão de tendência.

Regras de Long:
1. Reversão: Preço > EMA 144, e EMA 21 > EMA 34 > EMA 144.
2. Pullback: Stochastic %K cai abaixo de 20.
3. Entrada: Apenas no PRIMEIRO pullback após a reversão. O sinal é ativado 
   quando %K cruza acima de %D e fecha acima de 20.
4. Stop Loss: Abaixo da EMA 144 ou swing low recente.
5. Take Profit: 2:1 RR.

Regras de Short: Inverso.
"""

from __future__ import annotations
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult

class StochasticReversalStrategy(BaseStrategy):

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF010",
            name="TF010 - Stochastic Reversal",
            description = (
                "Identifica o início de uma nova tendência usando o cruzamento "
                "das EMAs 21, 34 e 144. Entra exclusivamente no primeiro pullback "
                "após a reversão, utilizando o Oscilador Estocástico (7,3,3) em zonas "
                "extremas (20/80) para o gatilho de precisão. Alvo Risco/Retorno 2:1."
            ),
            tags   = ["trend_reversal", "stochastic", "ema", "pullback"],
            criteria=[
                {"id": "c1_stochastic", "label": "C1 Stoch K/D", "description": "Estocástico em zona extrema com cruzamento de gatilho."},
            ],
            params = {
                "ema_fast": ParamDef(
                    type="int", default=21, min=5, max=50, step=1,
                    description="EMA Curta (Tendência imediata)"),
                "ema_med": ParamDef(
                    type="int", default=34, min=10, max=100, step=1,
                    description="EMA Intermediária"),
                "ema_slow": ParamDef(
                    type="int", default=144, min=50, max=300, step=1,
                    description="EMA Longa (Tendência Principal)"),
                "stoch_k": ParamDef(
                    type="int", default=7, min=3, max=21, step=1,
                    description="Stochastic %K Length"),
                "stoch_d": ParamDef(
                    type="int", default=3, min=2, max=10, step=1,
                    description="Stochastic %D Smoothing"),
                "stoch_smooth": ParamDef(
                    type="int", default=3, min=2, max=10, step=1,
                    description="Stochastic %K Smoothing"),
                "stoch_upper": ParamDef(
                    type="int", default=80, min=70, max=90, step=5,
                    description="Zona de Sobrecompra (Short)"),
                "stoch_lower": ParamDef(
                    type="int", default=20, min=10, max=30, step=5,
                    description="Zona de Sobrevenda (Long)"),
                "tp_rr": ParamDef(
                    type="float", default=2.0, min=1.0, max=5.0, step=0.1,
                    description="Relação Risk:Reward para o alvo (ex: 2.0 = 2:1)"),
                "sl_buffer": ParamDef(
                    type="float", default=0.001, min=0.0, max=0.05, step=0.001,
                    description="Buffer percentual para o Stop Loss (além da EMA/Pivot)"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.ema_fast = p["ema_fast"].default
        self.ema_med = p["ema_med"].default
        self.ema_slow = p["ema_slow"].default
        self.stoch_k = p["stoch_k"].default
        self.stoch_d = p["stoch_d"].default
        self.stoch_smooth = p["stoch_smooth"].default
        self.stoch_upper = p["stoch_upper"].default
        self.stoch_lower = p["stoch_lower"].default
        self.tp_rr = p["tp_rr"].default
        self.sl_buffer = p["sl_buffer"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        min_len = self.ema_slow + 20
        if len(candles) < min_len:
            return None

        df = pd.DataFrame([
            {"open": c.open, "high": c.high, "low": c.low, "close": c.close}
            for c in candles
        ])

        # EMAs
        ema21 = ta.ema(df["close"], length=self.ema_fast)
        ema34 = ta.ema(df["close"], length=self.ema_med)
        ema144 = ta.ema(df["close"], length=self.ema_slow)

        if ema144 is None or ema144.isna().iloc[-1]:
            return None

        df["ema21"] = ema21
        df["ema34"] = ema34
        df["ema144"] = ema144

        # Estocástico
        stoch = ta.stoch(df["high"], df["low"], df["close"],
                         k=self.stoch_k, d=self.stoch_d, smooth_k=self.stoch_smooth)
        
        if stoch is None or stoch.empty:
            return None
            
        k_col = [c for c in stoch.columns if 'STOCHk' in c][0]
        d_col = [c for c in stoch.columns if 'STOCHd' in c][0]
        
        df["stoch_k"] = stoch[k_col]
        df["stoch_d"] = stoch[d_col]

        curr_close = df["close"].iloc[-1]
        curr_k = df["stoch_k"].iloc[-1]
        curr_d = df["stoch_d"].iloc[-1]
        prev_k = df["stoch_k"].iloc[-2]
        
        # ── Detecção de Alinhamento ──────────────────────────────────────────
        aligned_long = (df["ema21"] > df["ema34"]) & (df["ema34"] > df["ema144"]) & (df["close"] > df["ema144"])
        aligned_short = (df["ema21"] < df["ema34"]) & (df["ema34"] < df["ema144"]) & (df["close"] < df["ema144"])
        
        is_aligned_long = bool(aligned_long.iloc[-1])
        is_aligned_short = bool(aligned_short.iloc[-1])

        if not is_aligned_long and not is_aligned_short:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="EMAs não alinhadas (Aguarda 21 > 34 > 144)",
                indicators=self._ind(df)
            )

        # ── Rastreio do Primeiro Pullback (Lookback State Machine) ───────────
        if is_aligned_long:
            not_aligned_idx = np.where(~aligned_long)[0]
        else:
            not_aligned_idx = np.where(~aligned_short)[0]

        if len(not_aligned_idx) == 0:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Histórico insuficiente para detectar a reversão (Sempre alinhado)",
                indicators=self._ind(df)
            )

        t_rev = not_aligned_idx[-1]
        
        stoch_k_arr = df["stoch_k"].values
        
        previous_completed_pullback = False
        in_zone = False
        
        for i in range(t_rev + 1, len(df) - 1):
            val = stoch_k_arr[i]
            if pd.isna(val): continue
            
            if is_aligned_long:
                if val < self.stoch_lower:
                    in_zone = True
                elif in_zone and val >= self.stoch_lower:
                    previous_completed_pullback = True
                    break
            else:
                if val > self.stoch_upper:
                    in_zone = True
                elif in_zone and val <= self.stoch_upper:
                    previous_completed_pullback = True
                    break

        if previous_completed_pullback:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Primeiro pullback já ocorreu. Estratégia inativa para esta tendência.",
                indicators=self._ind(df)
            )

        # ── Gatilho de Entrada ────────────────────────────────────────────────
        signal = Signal.HOLD
        hold_reason = "Aguardando gatilho do Estocástico"
        
        if is_aligned_long:
            recently_in_zone = any(stoch_k_arr[i] < self.stoch_lower for i in range(max(t_rev+1, len(df)-5), len(df)))
            
            if recently_in_zone and curr_k > curr_d and curr_k > self.stoch_lower and prev_k <= self.stoch_lower:
                signal = Signal.BUY
                hold_reason = ""
                
        elif is_aligned_short:
            recently_in_zone = any(stoch_k_arr[i] > self.stoch_upper for i in range(max(t_rev+1, len(df)-5), len(df)))
            
            if recently_in_zone and curr_k < curr_d and curr_k < self.stoch_upper and prev_k >= self.stoch_upper:
                signal = Signal.SELL
                hold_reason = ""

        if signal == Signal.HOLD:
            return StrategyResult(
                signal=signal,
                hold_reason=hold_reason,
                indicators=self._ind(df)
            )

        # ── Risco (Stop Loss e Take Profit) ───────────────────────────────────
        lookback_pullback = min(10, len(df) - t_rev)
        recent_low = df["low"].iloc[-lookback_pullback:].min()
        recent_high = df["high"].iloc[-lookback_pullback:].max()
        ema144_val = df["ema144"].iloc[-1]

        if signal == Signal.BUY:
            sl_price = min(recent_low, ema144_val) * (1 - self.sl_buffer)
            dist = curr_close - sl_price
            if dist <= 0:
                return StrategyResult(
                    signal=Signal.HOLD,
                    indicators=self._ind(df),
                    hold_reason="Risco inválido",
                    criteria_met=0,
                    criteria_total=1,
                )
            tp_price = curr_close + (dist * self.tp_rr)
            
        else: # SELL
            sl_price = max(recent_high, ema144_val) * (1 + self.sl_buffer)
            dist = sl_price - curr_close
            if dist <= 0:
                return StrategyResult(
                    signal=Signal.HOLD,
                    indicators=self._ind(df),
                    hold_reason="Risco inválido",
                    criteria_met=0,
                    criteria_total=1,
                )
            tp_price = curr_close - (dist * self.tp_rr)

        return StrategyResult(
            signal=signal,
            hold_reason="",
            indicators=self._ind(df),
            metadata={
                "sl_price": round(sl_price, 4),
                "tp1_price": round(tp_price, 4),
                "rr": self.tp_rr,
                "regime_state": "stochastic_first_pullback"
            },
            criteria_met=1,
            criteria_total=1,
        )

    def _ind(self, df) -> dict:
        return {
            "close": round(float(df["close"].iloc[-1]), 4),
            "ema21": round(float(df["ema21"].iloc[-1]), 4) if not pd.isna(df["ema21"].iloc[-1]) else 0,
            "ema34": round(float(df["ema34"].iloc[-1]), 4) if not pd.isna(df["ema34"].iloc[-1]) else 0,
            "ema144": round(float(df["ema144"].iloc[-1]), 4) if not pd.isna(df["ema144"].iloc[-1]) else 0,
            "stoch_k": round(float(df["stoch_k"].iloc[-1]), 2) if not pd.isna(df["stoch_k"].iloc[-1]) else 0,
            "stoch_d": round(float(df["stoch_d"].iloc[-1]), 2) if not pd.isna(df["stoch_d"].iloc[-1]) else 0,
        }
