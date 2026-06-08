"""
strategies/supertrend.py — SuperTrend + RSI Confirmation

SuperTrend é o indicador mais popular do TradingView (> 10M de uses).
Baseia-se em bandas dinâmicas ATR que servem de suporte/resistência e
invertem de direção quando o preço as cruza — sinal claro de BUY/SELL.

Lógica:
  - Banda central = (high + low) / 2
  - Upper Band    = centro + multiplier × ATR
  - Lower Band    = centro - multiplier × ATR
  - Direção vira para +1 (alta) quando close > Upper Band anterior
  - Direção vira para -1 (baixa) quando close < Lower Band anterior
  - Sinal BUY  → flip de -1 para +1  (com RSI > rsi_min  como filtro)
  - Sinal SELL → flip de +1 para -1  (com RSI < rsi_max  como filtro)

Risk:
  - Stop Loss    = ATR × sl_mult abaixo/acima da entrada
  - Take Profit 1 = tp1_pct % de distância da entrada (fecha 50 %)
  - Trailing Stop = ATR × ts_mult (ativado após TP1)
"""

from __future__ import annotations
from typing import Optional

import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class SuperTrendStrategy(BaseStrategy):

    # ── Metadados ────────────────────────────────────────────────────────────

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF004",
            name="TF004 - SuperTrend Follower",
            description = (
                "O indicador SuperTrend é o mais popular do TradingView, "
                "com milhões de aplicações em mercados de tendência. "
                "Utiliza bandas dinâmicas baseadas em ATR para identificar "
                "reversões de tendência com sinais BUY/SELL precisos. "
                "Um filtro de RSI elimina entradas contra a força do momentum, "
                "reduzindo falsos positivos em mercados laterais. "
                "Ideal para BTC, ETH e outros ativos voláteis no timeframe "
                "de 15 min a 4 h."
            ),
            tags   = ["trend", "atr", "momentum", "rsi", "breakout"],
            params = {
                "atr_period": ParamDef(
                    type="int", default=10, min=5, max=50, step=1,
                    description="Período do ATR (padrão TradingView: 10)"),
                "multiplier": ParamDef(
                    type="float", default=3.0, min=1.0, max=6.0, step=0.5,
                    description="Multiplicador ATR para largura das bandas (padrão: 3.0)"),
                "rsi_period": ParamDef(
                    type="int", default=14, min=5, max=30, step=1,
                    description="Período do RSI para filtro de momentum"),
                "rsi_min": ParamDef(
                    type="int", default=50, min=30, max=60, step=1,
                    description="RSI mínimo para confirmar sinal BUY (> rsi_min)"),
                "rsi_max": ParamDef(
                    type="int", default=50, min=40, max=70, step=1,
                    description="RSI máximo para confirmar sinal SELL (< rsi_max)"),
                "sl_mult": ParamDef(
                    type="float", default=2.5, min=0.5, max=4.0, step=0.1,
                    description="Multiplicador ATR para Stop Loss"),
                "tp1_pct": ParamDef(
                    type="float", default=2.5, min=0.5, max=10.0, step=0.5,
                    description="Take Profit 1 (% a partir da entrada, fecha 50 %)"),
                "ts_mult": ParamDef(
                    type="float", default=2.0, min=0.5, max=5.0, step=0.5,
                    description="Multiplicador ATR para Trailing Stop (ativado após TP1)"),
            },
            criteria=[
                {"id": "c1", "label": "Flip SuperTrend", "description": "SuperTrend inverte direção"},
                {"id": "c2", "label": "Filtro RSI", "description": "RSI confirma momentum"},
            ],
        )

    # ── Inicialização ─────────────────────────────────────────────────────────

    def __init__(self):
        p = self.info().params
        self.atr_period = p["atr_period"].default
        self.multiplier = p["multiplier"].default
        self.rsi_period = p["rsi_period"].default
        self.rsi_min    = p["rsi_min"].default
        self.rsi_max    = p["rsi_max"].default
        self.sl_mult    = p["sl_mult"].default
        self.tp1_pct    = p["tp1_pct"].default
        self.ts_mult    = p["ts_mult"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    # ── Cálculo principal ─────────────────────────────────────────────────────

    def compute(self, candles: list) -> Optional[StrategyResult]:
        # Precisamos de pelo menos atr_period + rsi_period + look-back
        min_len = max(self.atr_period, self.rsi_period) * 3 + 10
        if len(candles) < min_len:
            return None

        df = pd.DataFrame([
            {
                "high":   c.high,
                "low":    c.low,
                "close":  c.close,
                "open":   c.open,
                "volume": c.volume,
            }
            for c in candles
        ])

        # ── SuperTrend manual (compatível com o cálculo original do Pine Script) ──
        direction, st_line, final_atr = self._supertrend(
            df["high"], df["low"], df["close"],
            self.atr_period, self.multiplier,
        )

        if direction is None:
            return None

        # ── RSI ───────────────────────────────────────────────────────────────
        rsi_series = ta.rsi(df["close"], length=self.rsi_period)
        if rsi_series is None or rsi_series.iloc[-1] is None:
            return None

        rsi_now  = float(rsi_series.iloc[-1])
        rsi_prev = float(rsi_series.iloc[-2]) if len(rsi_series) > 1 else rsi_now

        # ── Identificar flip de direção ───────────────────────────────────────
        dir_now  = direction[-1]
        dir_prev = direction[-2] if len(direction) > 1 else dir_now

        close   = candles[-1].close
        atr_val = final_atr

        flip_long  = (dir_prev == -1 and dir_now == 1)   # flip → alta
        flip_short = (dir_prev ==  1 and dir_now == -1)  # flip → baixa

        # ── Sinal final com filtro RSI ─────────────────────────────────────────
        if flip_long and rsi_now > self.rsi_min:
            signal = Signal.BUY
        elif flip_short and rsi_now < self.rsi_max:
            signal = Signal.SELL
        else:
            signal = Signal.HOLD

        # ── Risk prices ───────────────────────────────────────────────────────
        sl_price  = (
            round(close - atr_val * self.sl_mult, 2) if signal == Signal.BUY
            else round(close + atr_val * self.sl_mult, 2)
        )
        tp1_price = (
            round(close * (1 + self.tp1_pct / 100), 2) if signal == Signal.BUY
            else round(close * (1 - self.tp1_pct / 100), 2)
        )

        criteria_met = 3 if signal in (Signal.BUY, Signal.SELL) else 0
        
        return StrategyResult(
            signal     = signal,
            indicators = {
                "supertrend":  round(st_line[-1], 2),
                "direction":   dir_now,
                "rsi":         round(rsi_now, 2),
                "rsi_prev":    round(rsi_prev, 2),
                "atr":         round(atr_val, 4),
                "close":       round(close, 2),
            },
            metadata = {
                "sl_price":  sl_price,
                "tp1_price": tp1_price,
                "sl_pct":    round(atr_val * self.sl_mult / close, 5),
                "tp1_pct":   round(self.tp1_pct / 100, 5),
                "ts_pct":    round(atr_val * self.ts_mult / close, 5),
            },
            criteria_met=criteria_met,
            criteria_total=3,
        )

    # ── SuperTrend puro (algoritmo Pine Script v3/v5 equivalente) ─────────────

    @staticmethod
    def _supertrend(
        high: pd.Series,
        low:  pd.Series,
        close: pd.Series,
        period: int,
        multiplier: float,
    ) -> tuple[list[int] | None, list[float], float]:
        """
        Calcula SuperTrend do zero, idêntico ao Pine Script padrão:
          - upper_band e lower_band ajustam-se continuamente
          - direction é +1 (alta) ou -1 (baixa)

        Returns:
            (direction_list, st_line_list, latest_atr)
        """
        # ATR via pandas_ta (Wilder RMA)
        atr_series = ta.atr(high, low, close, length=period)
        if atr_series is None or atr_series.isna().all():
            return None, [], 0.0

        n = len(close)
        # Arrays
        upper  = [float("nan")] * n
        lower  = [float("nan")] * n
        st     = [float("nan")] * n
        direc  = [1] * n  # começa como alta

        hl2 = [(high.iloc[i] + low.iloc[i]) / 2.0 for i in range(n)]

        for i in range(n):
            atr_i = atr_series.iloc[i]
            if pd.isna(atr_i):
                continue

            # Bandas básicas
            basic_upper = hl2[i] + multiplier * atr_i
            basic_lower = hl2[i] - multiplier * atr_i

            # Ajuste do upper band
            if i == 0 or pd.isna(upper[i - 1]):
                upper[i] = basic_upper
            else:
                # Upper band só sobe se estávamos em tendência de baixa
                upper[i] = (
                    basic_upper
                    if basic_upper < upper[i - 1] or close.iloc[i - 1] > upper[i - 1]
                    else upper[i - 1]
                )

            # Ajuste do lower band
            if i == 0 or pd.isna(lower[i - 1]):
                lower[i] = basic_lower
            else:
                # Lower band só desce se estávamos em tendência de alta
                lower[i] = (
                    basic_lower
                    if basic_lower > lower[i - 1] or close.iloc[i - 1] < lower[i - 1]
                    else lower[i - 1]
                )

            # Direção
            if i == 0:
                direc[i] = 1
            elif st[i - 1] == upper[i - 1]:
                # Estávamos usando upper como stop
                direc[i] = -1 if close.iloc[i] <= upper[i] else 1
            else:
                # Estávamos usando lower como stop
                direc[i] =  1 if close.iloc[i] >= lower[i] else -1

            # Linha SuperTrend
            st[i] = lower[i] if direc[i] == 1 else upper[i]

        # Última direção e ATR válidos
        valid_dirs = [d for d, s in zip(direc, st) if not (isinstance(s, float) and s != s)]
        if not valid_dirs:
            return None, st, 0.0

        last_valid_atr = float(atr_series.dropna().iloc[-1])
        return direc, st, last_valid_atr
