"""
strategies/bollinger_triple_threat.py — S007 (The Secret Mindset)
"""

from __future__ import annotations
from typing import Optional, Dict, Any
import pandas as pd
import pandas_ta as ta
import numpy as np

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class BollingerTripleThreatStrategy(BaseStrategy):

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="MR005",
            name="MR005 - Bollinger Triple Threat",
            description = (
                "Sistema baseado em 'The Secret Mindset'. Combina 3 setups: "
                "1. Slingshot (Squeeze + Pullback), "
                "2. Rubber Band (Exaustão Total + Divergência RSI), "
                "3. Staircase (Tendência na Média Móvel). "
                "Usa a inclinação da SMA 20 como filtro de tendência principal."
            ),
            tags        = ["bollinger", "rsi_divergence", "trend_following", "breakout"],
            recommended_timeframe = "15m",
            criteria=[
                {"id": "c1_rsi_exhaustion", "label": "C1 RSI Exaustão", "description": "RSI em zona de sobrecompra/sobrevenda para Rubber Band."},
                {"id": "c2_bollinger_position", "label": "C2 Posição Bollinger", "description": "Preço toca/rompe bandas para Rubber Band ou Slingshot."},
                {"id": "c3_sma20_bias", "label": "C3 SMA 20", "description": "Inclinação e posição na média para Staircase."},
            ],
            params = {
                "rsi_length":    ParamDef(type="int",   default=14,  min=7,   max=21, description="Período do RSI"),
                "bb_length":     ParamDef(type="int",   default=20,  min=10,  max=50, description="Período das Bandas"),
                "bb_std":        ParamDef(type="float", default=2.0, min=1.5, max=3.0, description="Desvio Padrão"),
                "squeeze_perc":  ParamDef(type="int",   default=20,  min=10,  max=40, description="Percentil para detectar Squeeze (Slingshot)"),
                "rr_ratio":      ParamDef(type="float", default=2.5, min=1.5, max=5.0, description="Relação Risco:Recompensa"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.rsi_length   = p["rsi_length"].default
        self.bb_length    = p["bb_length"].default
        self.bb_std       = p["bb_std"].default
        self.squeeze_perc = p["squeeze_perc"].default
        self.rr_ratio     = p["rr_ratio"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        criteria_total = 3
        lookback = 60 # Necessário para detectar divergências
        if len(candles) < lookback:
            return None

        df = pd.DataFrame([{
            "open": c.open, "high": c.high, "low": c.low, "close": c.close
        } for c in candles])

        # ── Indicadores ──────────────────────────────────────────────────────
        # Bollinger Bands
        bb = ta.bbands(df["close"], length=self.bb_length, std=self.bb_std)
        df['bb_u'] = bb.iloc[:, 2]
        df['bb_m'] = bb.iloc[:, 1]
        df['bb_l'] = bb.iloc[:, 0]
        df['bw']   = (df['bb_u'] - df['bb_l']) / df['bb_m'] # Bandwidth

        # RSI e ATR
        df['rsi'] = ta.rsi(df['close'], length=self.rsi_length)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)

        # Inclinação da Média (SMA 20)
        df['sma_slope'] = df['bb_m'].diff(3) # Diferença simples de 3 períodos

        # Squeeze dinâmico (lowest percentile)
        df['is_squeeze'] = df['bw'] < df['bw'].rolling(40).quantile(self.squeeze_perc/100)

        # Dados atuais e anteriores
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        criteria_met = self._criteria_met(curr)
        
        # ── Detecção de Divergência (Detector de Mentiras) ────────────────────
        bull_div = self._check_bull_div(df)
        bear_div = self._check_bear_div(df)

        # ── SETUP 1: Rubber Band (Reversão com Exaustão Total) ───────────────
        # Long: Vela anterior fechou COMPLETAMENTE abaixo da banda (High < Lower)
        if df['high'].iloc[-2] < df['bb_l'].iloc[-2]:
            if curr['close'] > df['bb_l'].iloc[-1] and bull_div:
                return self._signal(Signal.BUY, "Rubber Band (Long + Div RSI)", curr, df)

        # Short: Vela anterior fechou COMPLETAMENTE acima da banda (Low > Upper)
        if df['low'].iloc[-2] > df['bb_u'].iloc[-2]:
            if curr['close'] < df['bb_u'].iloc[-1] and bear_div:
                return self._signal(Signal.SELL, "Rubber Band (Short + Div RSI)", curr, df)

        # ── SETUP 2: Staircase (Tendência na Média) ──────────────────────────
        # Long: Média inclinada para cima, toque na banda inferior ou média + rejeição (pavio)
        if curr['sma_slope'] > 0:
            touched = curr['low'] <= df['bb_l'].iloc[-1] or curr['low'] <= df['bb_m'].iloc[-1]
            rejection = (curr['close'] > curr['open']) and (curr['low'] < min(curr['open'], curr['close']))
            if touched and rejection:
                return self._signal(Signal.BUY, "Staircase (Long Trend)", curr, df)

        # Short: Média inclinada para baixo
        if curr['sma_slope'] < 0:
            touched = curr['high'] >= df['bb_u'].iloc[-1] or curr['high'] >= df['bb_m'].iloc[-1]
            rejection = (curr['close'] < curr['open']) and (curr['high'] > max(curr['open'], curr['close']))
            if touched and rejection:
                return self._signal(Signal.SELL, "Staircase (Short Trend)", curr, df)

        # ── SETUP 3: Slingshot (Squeeze + Rompimento) ────────────────────────
        # Squeeze anterior + Rompimento atual
        if df['is_squeeze'].iloc[-5:-1].any(): # Squeeze nos últimos 5 candles
            # Long
            if curr['close'] > df['bb_u'].iloc[-1] and prev['close'] <= df['bb_u'].iloc[-2]:
                 return self._signal(Signal.BUY, "Slingshot (Breakout Up)", curr, df)
            # Short
            if curr['close'] < df['bb_l'].iloc[-1] and prev['close'] >= df['bb_l'].iloc[-2]:
                 return self._signal(Signal.SELL, "Slingshot (Breakout Down)", curr, df)

        return StrategyResult(
            signal=Signal.HOLD,
            criteria_met=criteria_met,
            criteria_total=criteria_total,
            hold_reason=f"Aguardando sinal Triple Threat (Slope: {curr['sma_slope']:.4f})",
            indicators=self._inds(curr)
        )

    def _check_bull_div(self, df):
        """Preço faz mínima mais baixa, RSI faz mínima mais alta."""
        win = df.iloc[-30:]
        p_min_idx = win['low'].idxmin()
        r_min_idx = win['rsi'].idxmin()
        # Se o índice da mínima do RSI ocorreu ANTES da mínima do preço e o RSI está subindo
        return p_min_idx > r_min_idx and win['rsi'].iloc[-1] > win['rsi'].loc[r_min_idx]

    def _check_bear_div(self, df):
        """Preço faz máxima mais alta, RSI faz máxima mais baixa."""
        win = df.iloc[-30:]
        p_max_idx = win['high'].idxmax()
        r_max_idx = win['rsi'].idxmax()
        return p_max_idx > r_max_idx and win['rsi'].iloc[-1] < win['rsi'].loc[r_max_idx]

    def _signal(self, sig, reason, curr, df):
        atr = curr['atr'] if not pd.isna(curr['atr']) else curr['close'] * 0.01
        sl_dist = atr * 2.0
        entry = curr['close']
        sl = entry - sl_dist if sig == Signal.BUY else entry + sl_dist
        tp = entry + (sl_dist * self.rr_ratio) if sig == Signal.BUY else entry - (sl_dist * self.rr_ratio)
        
        return StrategyResult(
            signal=sig,
            hold_reason=reason,
            indicators=self._inds(curr),
            metadata={"sl_price": round(sl, 2), "tp1_price": round(tp, 2)},
            criteria_met=3,
            criteria_total=3,
        )

    def _criteria_met(self, curr) -> int:
        rsi = curr.get("rsi")
        close = curr.get("close")
        bb_u = curr.get("bb_u")
        bb_l = curr.get("bb_l")
        bb_m = curr.get("bb_m")
        score = 0
        if not pd.isna(rsi) and (rsi < 30 or rsi > 70):
            score += 1
        if close > bb_u or close < bb_l:
            score += 1
        if (close > bb_m and curr.get("sma_slope", 0) > 0) or (close < bb_m and curr.get("sma_slope", 0) < 0):
            score += 1
        return score

    def _inds(self, curr) -> dict:
        return {
            "close": round(curr["close"], 2),
            "rsi": round(curr["rsi"], 2) if not pd.isna(curr["rsi"]) else 0,
            "bb_u": round(curr["bb_u"], 2),
            "bb_m": round(curr["bb_m"], 2),
            "bb_l": round(curr["bb_l"], 2),
            "atr": round(curr["atr"], 6) if not pd.isna(curr["atr"]) else 0.0,
            "sma_slope": round(curr["sma_slope"], 6) if not pd.isna(curr["sma_slope"]) else 0.0,
        }
