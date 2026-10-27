"""Estratégia TF017 - Adaptive Supertrend (Versão Final Corrigida)
Gerado pela Fábrica de Estratégias
Data de criação: 2024-01-01
Última modificação: 2026-06-26
"""

from __future__ import annotations
from typing import Optional
import numpy as np
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class AdaptiveSupertrendStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF017",
            name="TF017 - Adaptive Supertrend",
            description="Estratégia baseada no Modern Adaptive Supertrend, que adiciona filtro de commit (fechamento além de 0.5 ATR), distância adaptativa (expande em tendência/range, contrai em transições) e velocidade adaptativa opcional. Gera sinais de compra/venda quando o Supertrend flipa com confirmação de fechamento, reduzindo falsos flips em mercados laterais.",
            tags=["trend", "momentum", "breakout"],
            recommended_timeframe="1h",
            criteria=[
                {"id": "c1_supertrend_flip", "label": "C1 Flip Supertrend", "description": "Supertrend mudou de direção em relação ao candle anterior"},
                {"id": "c2_commit_filter", "label": "C2 Filtro Commit", "description": "Preço de fechamento ultrapassou a banda por pelo menos commit_buffer * ATR"},
            ],
            params={
                "atr_period": ParamDef(type="int", default=14, min=5, max=50, step=1, description="Período do ATR para cálculo do Supertrend e gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Trailing Stop após TP1"),
                "supertrend_factor": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para as bandas do Supertrend"),
                "commit_buffer": ParamDef(type="float", default=0.5, min=0.1, max=1.0, step=0.1, description="Fração do ATR que o preço deve fechar além da banda para confirmar flip (filtro de commit)"),
                "adaptive_distance": ParamDef(type="bool", default=True, description="Ativar distância adaptativa (expande em tendência/range, contrai em transições)"),
                "adaptive_speed": ParamDef(type="bool", default=False, description="Ativar velocidade adaptativa (opcional, altera lookback conforme ritmo)"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.atr_period = p["atr_period"].default
        self.sl_mult = p["sl_mult"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default
        self.supertrend_factor = p["supertrend_factor"].default
        self.commit_buffer = p["commit_buffer"].default
        self.adaptive_distance = p["adaptive_distance"].default
        self.adaptive_speed = p["adaptive_speed"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles, context=None):
        min_candles = max(self.atr_period * 3, 50)
        if len(candles) < min_candles:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_candles} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "atr": 0.0, "supertrend_direction": 0.0, "supertrend_band": 0.0, "commit_confirmed": 0.0},
            )

        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close} for c in candles])
        
        # 1. Velocidade Adaptativa Opcional (Lookback dinâmico baseado na volatilidade de retornos)
        current_atr_period = self.atr_period
        if self.adaptive_speed and len(df) > self.atr_period * 2:
            returns_std = df["close"].pct_change().iloc[-self.atr_period:].std()
            if returns_std > 0:
                # Maior volatilidade resulta em períodos menores (indicador mais responsivo)
                current_atr_period = max(7, min(21, int(self.atr_period * (0.01 / returns_std))))

        # Calcular ATR Real
        atr_series = ta.atr(df["high"], df["low"], df["close"], length=current_atr_period)
        if atr_series is None or atr_series.isna().all():
            return None

        # 2. Geração dos Fatores Dinâmicos (Série Temporal)
        factors = pd.Series(self.supertrend_factor, index=df.index)
        if self.adaptive_distance:
            # Compara volatilidade atual com a média histórica recente para inferir o regime do mercado
            # Vetorizado (equivalente ao laço por candle): média do ATR dos 2*período candles ANTERIORES a i.
            recent_mean = atr_series.shift(1).rolling(self.atr_period * 2, min_periods=1).mean()
            vol_ratio = atr_series / recent_mean
            eligible = (recent_mean > 0) & (pd.Series(df.index, index=df.index) >= self.atr_period)
            # vol_ratio < 0.8: compressão/lateral -> alarga a banda; > 1.2: explosão -> contrai a banda
            factors = factors.mask(eligible & (vol_ratio < 0.8), self.supertrend_factor * 1.3)
            factors = factors.mask(eligible & (vol_ratio >= 0.8) & (vol_ratio > 1.2), self.supertrend_factor * 0.8)

        # 3. Cálculo das Bandas Clássicas Matemáticas Baseadas no ATR
        hl2 = (df["high"] + df["low"]) / 2
        basic_upper = hl2 + (factors * atr_series)
        basic_lower = hl2 - (factors * atr_series)

        # 4. Cálculo Histórico Iterativo com Integração do Commit Filter
        st_dir = np.ones(len(df))   # 1.0 = Alta, -1.0 = Baixa
        st_band = np.zeros(len(df))
        
        # Inicialização do primeiro ponto de dados válido
        start_idx = current_atr_period
        st_band[start_idx] = basic_upper.iloc[start_idx]
        st_dir[start_idx] = 1.0

        close_np = df["close"].to_numpy(dtype=float)
        atr_np = atr_series.to_numpy(dtype=float)
        upper_np = basic_upper.to_numpy(dtype=float)
        lower_np = basic_lower.to_numpy(dtype=float)
        for i in range(start_idx + 1, len(df)):
            prev_dir = st_dir[i - 1]
            prev_band = st_band[i - 1]
            curr_close = close_np[i]
            curr_atr = atr_np[i]
            buffer = self.commit_buffer * curr_atr

            if prev_dir == 1.0:
                # Vira para Baixa apenas se o Fechamento quebrar a banda inferior com a margem do commit buffer
                if curr_close < prev_band - buffer:
                    st_dir[i] = -1.0
                    st_band[i] = upper_np[i]
                else:
                    st_dir[i] = 1.0
                    st_band[i] = max(lower_np[i], prev_band)
            else:
                # Vira para Alta apenas se o Fechamento quebrar a banda superior com a margem do commit buffer
                if curr_close > prev_band + buffer:
                    st_dir[i] = 1.0
                    st_band[i] = lower_np[i]
                else:
                    st_dir[i] = -1.0
                    st_band[i] = min(upper_np[i], prev_band)

        # Captura de variáveis finais do último candle
        close = float(df["close"].iloc[-1])
        atr = float(atr_series.iloc[-1])
        current_direction = st_dir[-1]
        current_band = st_band[-1]
        prev_direction = st_dir[-2]

        # 5. Avaliação Granular dos Critérios Definidos
        criteria_met = 0
        criteria_total = 2

        # C1: Mudança estrutural do Supertrend
        flip_ocorreu = current_direction != prev_direction
        if flip_ocorreu:
            criteria_met += 1

        # C2: Validação da margem de segurança (Commit Filter)
        buffer = self.commit_buffer * atr
        commit_satisfeito = False
        if current_direction == 1.0:
            if close > current_band + buffer:
                commit_satisfeito = True
        else:
            if close < current_band - buffer:
                commit_satisfeito = True

        if commit_satisfeito:
            criteria_met += 1

        # 6. Geração do Sinal Operacional
        signal = Signal.HOLD
        hold_reason = "Supertrend estável"

        if flip_ocorreu and commit_satisfeito:
            if current_direction == 1.0:
                signal = Signal.BUY
                hold_reason = ""
            elif current_direction == -1.0:
                signal = Signal.SELL
                hold_reason = ""
        else:
            if not flip_ocorreu:
                hold_reason = "Supertrend não flipou"
            elif not commit_satisfeito:
                hold_reason = "Filtro de commit não satisfeito"

        # 7. Gestão de Risco (Stop Loss e Take Profit)
        sl_dist = atr * self.sl_mult
        if signal == Signal.BUY:
            sl_price = round(close - sl_dist, 4)
            tp1_price = round(close + sl_dist * self.tp1_rr, 4)
        elif signal == Signal.SELL:
            sl_price = round(close + sl_dist, 4)
            tp1_price = round(close - sl_dist * self.tp1_rr, 4)
        else:
            sl_price = round(close - sl_dist, 4) if current_direction == 1.0 else round(close + sl_dist, 4)
            tp1_price = round(close + sl_dist * self.tp1_rr, 4) if current_direction == 1.0 else round(close - sl_dist * self.tp1_rr, 4)

        return StrategyResult(
            signal=signal,
            hold_reason=hold_reason,
            indicators={
                "close": round(close, 4),
                "atr": round(atr, 6),
                "supertrend_direction": current_direction,
                "supertrend_band": round(current_band, 4),
                "commit_confirmed": 1.0 if commit_satisfeito else 0.0
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(sl_dist / close, 5),
                "tp1_pct": round(sl_dist * self.tp1_rr / close, 5),
                "ts_pct": round(atr * self.ts_mult / close, 5),
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )