"""PA016 - Reversão 3 Impulsos
Gerado pela Fábrica de Estratégias (Corrigido)
Estratégia de scalping que identifica esgotamento de tendência de alta após três impulsos consecutivos, entrando vendido em zona de resistência.
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class Reversao3ImpulsosStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="PA016",
            name="PA016 - Reversão 3 Impulsos",
            description="Estratégia de scalping que identifica esgotamento de tendência de alta após três impulsos consecutivos, entrando vendido em zona de resistência. Utiliza contagem de ondas e bull traps como filtros, com gerenciamento de contratos e stop no break-even.",
            tags=["reversal", "scalp", "pattern", "intraday"],
            recommended_timeframe="5m",
            extra_timeframes=["15m"],
            criteria=[
                {"id": "c1_impulse_exhaustion", "label": "C1 Esgotamento de Impulsos", "description": "Três ou mais impulsos de alta (topos ascendentes) detectados no lookback"},
                {"id": "c2_bull_trap", "label": "C2 Bull Trap", "description": "Rompimento falso de máxima anterior seguido de reversão"},
                {"id": "c3_resistance_zone", "label": "C3 Zona de Resistência", "description": "Preço atual está próximo da máxima do último impulso (zona de resistência)"},
            ],
            params={
                "atr_period": ParamDef(type="int", default=14, min=7, max=30, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Trailing Stop após TP1"),
                "impulse_count": ParamDef(type="int", default=3, min=2, max=5, step=1, description="Número de impulsos de alta para esgotamento"),
                "lookback_impulse": ParamDef(type="int", default=50, min=20, max=100, step=5, description="Período de lookback para identificar impulsos"),
                "bull_trap_threshold": ParamDef(type="float", default=0.1, min=0.01, max=2.0, step=0.05, description="Percentual mínimo de rompimento falso para bull trap"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.atr_period = p["atr_period"].default
        self.sl_mult = p["sl_mult"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default
        self.impulse_count = p["impulse_count"].default
        self.lookback_impulse = p["lookback_impulse"].default
        self.bull_trap_threshold = p["bull_trap_threshold"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles, context=None):
        min_c = max(self.lookback_impulse + 5, 100)
        if len(candles) < min_c:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_c} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "atr": 0.0, "high": 0.0, "low": 0.0, "impulse_count": 0, "bull_trap": 0, "resistance_zone": 0.0},
            )

        df = pd.DataFrame([{"high": float(c.high), "low": float(c.low), "close": float(c.close), "volume": float(c.volume)} for c in candles])
        close = float(df["close"].iloc[-1])
        high = float(df["high"].iloc[-1])
        low = float(df["low"].iloc[-1])

        atr_s = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        if atr_s is None:
            return None
        atr = float(atr_s.iloc[-1])

        # Recortar o lookback para análise histórica estrutural
        lookback_df = df.tail(self.lookback_impulse).reset_index(drop=True)
        highs = lookback_df["high"].values
        
        # 1. Detecção Robusta de Topos (Swing Highs)
        # Identifica velas que são maiores que as 2 anteriores e as 2 posteriores
        peaks = []
        for i in range(2, len(highs) - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                peaks.append((i, highs[i]))
        
        # Contar quantos topos consecutivamente ascendentes (impulsos) nós temos
        impulse_count_val = 0
        if len(peaks) >= 2:
            current_streak = 1
            max_streak = 1
            for p in range(1, len(peaks)):
                if peaks[p][1] > peaks[p-1][1]:
                    current_streak += 1
                    max_streak = max(max_streak, current_streak)
                else:
                    current_streak = 1
            impulse_count_val = max_streak

        # 2. Identificar Bull Trap Legítimo
        bull_trap = 0
        resistance_zone = 0.0
        
        if len(peaks) >= 2:
            # Pegamos o topo significativo anterior (penúltimo pico detectado)
            previous_significant_high = peaks[-2][1]
            # A vela atual (ou a última concluída) rompeu essa máxima histórica recente e falhou?
            highest_recent = max(highs[-3:]) # Máxima das últimas 3 velas
            
            if highest_recent > previous_significant_high * (1 + self.bull_trap_threshold / 100):
                # Se rompeu mas o fechamento atual voltou para baixo da máxima anterior, é um Bull Trap
                if close < previous_significant_high:
                    bull_trap = 1
                    resistance_zone = highest_recent

        if resistance_zone == 0.0:
            resistance_zone = float(max(highs[-10:])) if len(highs) >= 10 else float(max(highs))

        # 3. Validação dos Critérios
        criteria_met = 0
        criteria_total = 3

        # C1: Esgotamento de impulsos
        if impulse_count_val >= self.impulse_count:
            criteria_met += 1

        # C2: Bull trap
        if bull_trap == 1:
            criteria_met += 1

        # C3: Zona de resistência (0.5% de tolerância da região de topo)
        tolerance = resistance_zone * 0.005
        if abs(close - resistance_zone) <= tolerance:
            criteria_met += 1

        # Decisão de sinal
        signal = Signal.HOLD
        hold_reason = ""
        
        if criteria_met == criteria_total:
            signal = Signal.SELL
        else:
            if impulse_count_val < self.impulse_count:
                hold_reason = f"Número insuficiente de impulsos de alta ({impulse_count_val}/{self.impulse_count})"
            elif bull_trap == 0:
                hold_reason = "Sem bull trap detectado no rompimento"
            else:
                hold_reason = "Preço fora da zona de resistência especificada"

        # Cálculo preciso de SL e TP (Alvo curto de Scalping baseado no ATR)
        sl_dist = atr * self.sl_mult
        sl_price = round(close + sl_dist, 4)
        tp1_price = round(close - (sl_dist * self.tp1_rr), 4)

        return StrategyResult(
            signal=signal,
            hold_reason=hold_reason,
            indicators={
                "close": round(close, 4),
                "atr": round(atr, 6),
                "high": round(high, 4),
                "low": round(low, 4),
                "impulse_count": float(impulse_count_val),
                "bull_trap": float(bull_trap),
                "resistance_zone": round(resistance_zone, 4),
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(sl_dist / close, 5),
                "tp1_pct": round((sl_dist * self.tp1_rr) / close, 5),
                "ts_pct": round(atr * self.ts_mult / close, 5),
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )