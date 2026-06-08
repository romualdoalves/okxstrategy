"""
strategies/cfm.py — Choppiness-Filtered Momentum  (estratégia autoral)

Design philosophy
─────────────────
A maioria das estratégias falha porque tentam operar em QUALQUER condição
de mercado. A CFM resolve isso em três camadas:

  1. REGIME  — Choppiness Index (CHOP) detecta se o mercado está
               genuinamente em tendência ou apenas em ruído lateral.
               Só operamos quando CHOP < 50 (mercado direcional).

  2. DIREÇÃO — EMA 21/55 crossover como sinal de entrada, mas APENAS
               quando o regime já foi aprovado pelo CHOP.

  3. CONFIRMAÇÃO dupla:
       a) Volume atual > 1.2× SMA(volume, 20) → participação real
       b) RSI slope positivo (BUY) ou negativo (SELL) → momentum

  4. RISCO ADAPTATIVO — Stop Loss escalado pelo ATR atual, não fixo.
               Em mercados voláteis, o stop amplia; em calmos, estreita.

Choppiness Index
────────────────
  CHOP = 100 × log10( Σ ATR(1, n) / (Highest_High(n) − Lowest_Low(n)) )
                      ─────────────────────────────────────────────────
                                      log10(n)

  Referências de Fibonacci:
    < 38.2  →  tendência muito forte
    38–50   →  tendência moderada         ← zona de entrada
    50–61.8 →  transição / incerto        ← zona de cuidado
    > 61.8  →  lateral/caótico            ← não operar

RSI Slope
─────────
  Não usamos o nível do RSI sozinho (clássico e lento).
  Usamos a DERIVADA: RSI[0] − RSI[−2].
  Slope > 0 + RSI > 50 = momentum crescente de alta.
  Slope < 0 + RSI < 50 = momentum crescente de baixa.

Resultado
─────────
  BUY  quando: CHOP < chop_max AND EMA crossover alta AND volume OK AND RSI slope up
  SELL quando: CHOP < chop_max AND EMA crossover baixa AND volume OK AND RSI slope down
  HOLD em qualquer outro caso (regime ruim ou confirmações insuficientes)
"""

from __future__ import annotations
import math
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class CfmStrategy(BaseStrategy):

    # ── Metadados ────────────────────────────────────────────────────────────

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF011",
            name="TF011 - Choppiness-Filtered Momentum",
            description = (
                "Estratégia autoral em 4 camadas: "
                "(1) Choppiness Index filtra mercados laterais — só opera "
                "quando há tendência real detectada; "
                "(2) cruzamento EMA 21/55 define a direção; "
                "(3) volume acima da média confirma participação institucional; "
                "(4) slope do RSI valida que o momentum está acelerando. "
                "Risco adaptativo ao ATR atual — stop não é fixo. "
                "Produz menos sinais, mas de maior qualidade."
            ),
            tags   = ["trend", "momentum", "volume", "rsi", "atr", "volatility"],
            criteria=[
                {"id": "c1_regime", "label": "C1 Regime", "description": "Choppiness confirma mercado tendencial."},
                {"id": "c2_ema", "label": "C2 EMA", "description": "EMA rápida e lenta definem direção."},
                {"id": "c3_volume", "label": "C3 Volume", "description": "Volume acima da média confirma participação."},
                {"id": "c4_rsi_slope", "label": "C4 RSI Slope", "description": "Inclinação do RSI confirma momentum."},
            ],
            params = {
                "chop_period": ParamDef(
                    type="int", default=14, min=8, max=30, step=1,
                    description="Período do Choppiness Index"),
                "chop_max": ParamDef(
                    type="float", default=50.0, min=38.2, max=61.8, step=1.0,
                    description="CHOP máximo para aceitar entrada (< = tendência)"),
                "ema_fast": ParamDef(
                    type="int", default=21, min=5, max=50, step=1,
                    description="EMA rápida"),
                "ema_slow": ParamDef(
                    type="int", default=55, min=20, max=200, step=5,
                    description="EMA lenta"),
                "vol_sma": ParamDef(
                    type="int", default=20, min=5, max=50, step=1,
                    description="SMA do volume para comparação"),
                "vol_mult": ParamDef(
                    type="float", default=1.2, min=1.0, max=3.0, step=0.1,
                    description="Volume deve ser ≥ vol_mult × SMA(volume)"),
                "rsi_period": ParamDef(
                    type="int", default=14, min=5, max=30, step=1,
                    description="Período do RSI"),
                "rsi_slope_bars": ParamDef(
                    type="int", default=2, min=1, max=5, step=1,
                    description="Janela (barras) para calcular o slope do RSI"),
                "min_gap_pct": ParamDef(
                    type="float", default=0.05, min=0.01, max=0.5, step=0.01,
                    description="Gap mínimo entre EMA rápida e lenta (% do preço) para confirmar tendência"),
                "atr_period": ParamDef(
                    type="int", default=14, min=5, max=30, step=1,
                    description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(
                    type="float", default=2.5, min=0.5, max=4.0, step=0.1,
                    description="Multiplicador ATR para Stop Loss"),
                "tp1_pct": ParamDef(
                    type="float", default=2.0, min=0.5, max=8.0, step=0.5,
                    description="Take Profit 1 (% a partir da entrada — fecha 50 %)"),
                "ts_mult": ParamDef(
                    type="float", default=2.5, min=1.0, max=5.0, step=0.5,
                    description="Multiplicador ATR para Trailing Stop (após TP1)"),
            },
        )

    # ── Inicialização ─────────────────────────────────────────────────────────

    def __init__(self):
        p = self.info().params
        self.chop_period    = p["chop_period"].default
        self.chop_max       = p["chop_max"].default
        self.ema_fast       = p["ema_fast"].default
        self.ema_slow       = p["ema_slow"].default
        self.vol_sma        = p["vol_sma"].default
        self.vol_mult       = p["vol_mult"].default
        self.rsi_period     = p["rsi_period"].default
        self.rsi_slope_bars = p["rsi_slope_bars"].default
        self.min_gap_pct    = p["min_gap_pct"].default
        self.atr_period     = p["atr_period"].default
        self.sl_mult        = p["sl_mult"].default
        self.tp1_pct        = p["tp1_pct"].default
        self.ts_mult        = p["ts_mult"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    # ── Cálculo ───────────────────────────────────────────────────────────────

    def compute(self, candles: list) -> Optional[StrategyResult]:
        min_len = max(self.ema_slow, self.chop_period, self.rsi_period,
                      self.vol_sma, self.atr_period) + 10
        if len(candles) < min_len:
            return None

        df = pd.DataFrame([
            {"open": c.open, "high": c.high, "low": c.low,
             "close": c.close, "volume": c.volume}
            for c in candles
        ])

        # ── 1. Regime: Choppiness Index ───────────────────────────────────────
        chop = self._choppiness(df["high"], df["low"], df["close"],
                                period=self.chop_period)
        if chop is None:
            return None
        in_trend = chop < self.chop_max   # True = mercado direcional

        # ── 2. Direção: EMA crossover ─────────────────────────────────────────
        ema_f = ta.ema(df["close"], length=self.ema_fast)
        ema_l = ta.ema(df["close"], length=self.ema_slow)
        if ema_f is None or ema_l is None:
            return None

        ef, el     = float(ema_f.iloc[-1]), float(ema_l.iloc[-1])

        # Trend-following: exige gap mínimo como % do preço, não o evento pontual
        # de cruzamento. Em mercados laterais o gap oscila 0.0-0.3 pts sem nunca
        # cruzar de forma limpa — isso elimina 99% dos sinais válidos em vão.
        close_ref = candles[-1].close
        min_gap   = close_ref * self.min_gap_pct / 100
        gap_now   = ef - el

        trend_up   = gap_now >  min_gap   # fast claramente acima da slow
        trend_down = gap_now < -min_gap   # fast claramente abaixo da slow

        # ── 3. Volume: spike acima da média ───────────────────────────────────
        vol_avg  = float(df["volume"].rolling(self.vol_sma).mean().iloc[-1])
        vol_now  = float(df["volume"].iloc[-1])
        vol_ok   = vol_now >= self.vol_mult * vol_avg

        # ── 4. Momentum: slope do RSI ─────────────────────────────────────────
        rsi_series = ta.rsi(df["close"], length=self.rsi_period)
        if rsi_series is None:
            return None
        rsi_now  = float(rsi_series.iloc[-1])
        rsi_old  = float(rsi_series.iloc[-1 - self.rsi_slope_bars])
        rsi_slope = rsi_now - rsi_old   # positivo = acelerando para cima

        rsi_confirm_long  = rsi_slope > 0 and rsi_now > 50
        rsi_confirm_short = rsi_slope < 0 and rsi_now < 50

        # ── ATR para risk ─────────────────────────────────────────────────────
        atr_series = ta.atr(df["high"], df["low"], df["close"],
                            length=self.atr_period)
        if atr_series is None:
            return None
        atr_val = float(atr_series.iloc[-1])
        close   = candles[-1].close

        # ── Decisão final ─────────────────────────────────────────────────────
        hold_reason = ""
        if in_trend and trend_up and vol_ok and rsi_confirm_long:
            signal = Signal.BUY
        elif in_trend and trend_down and vol_ok and rsi_confirm_short:
            signal = Signal.SELL
        else:
            signal = Signal.HOLD
            if not in_trend:
                hold_reason = f"Mercado lateral — CHOP {chop:.1f} (máx {self.chop_max})"
            elif not (trend_up or trend_down):
                hold_reason = f"Tendência EMA fraca — gap {gap_now:+.1f} pts (mín {min_gap:.1f})"
            elif not vol_ok:
                ratio = round(vol_now / vol_avg, 2) if vol_avg > 0 else 0
                hold_reason = f"Volume insuficiente — {ratio}× (mín {self.vol_mult}×)"
            else:
                hold_reason = "RSI não confirma momentum"

        # ── Preços de risco ───────────────────────────────────────────────────
        if signal == Signal.BUY:
            sl_price  = round(close - atr_val * self.sl_mult, 2)
            tp1_price = round(close * (1 + self.tp1_pct / 100), 2)
        else:
            sl_price  = round(close + atr_val * self.sl_mult, 2)
            tp1_price = round(close * (1 - self.tp1_pct / 100), 2)

        return StrategyResult(
            signal      = signal,
            criteria_met= 4 if signal in (Signal.BUY, Signal.SELL) else 0,
            criteria_total= 4,
            hold_reason = hold_reason,
            indicators  = {
                "chop":         round(chop, 2),
                "in_trend":     float(in_trend),
                "ema_fast":     round(ef, 2),
                "ema_slow":     round(el, 2),
                "trend_up":     float(trend_up),
                "trend_down":   float(trend_down),
                "min_gap":      round(min_gap, 2),
                "rsi":          round(rsi_now, 2),
                "rsi_slope":    round(rsi_slope, 2),
                "volume_ratio": round(vol_now / vol_avg, 2) if vol_avg > 0 else 0.0,
                "atr":          round(atr_val, 4),
                "close":        round(close, 2),
            },
            metadata = {
                "sl_price":  sl_price,
                "tp1_price": tp1_price,
                "sl_pct":    round(atr_val * self.sl_mult / close, 5),
                "tp1_pct":   round(self.tp1_pct / 100, 5),
                "ts_pct":    round(atr_val * self.ts_mult / close, 5),
                # diagnóstico — útil para debug no painel
                "diag": {
                    "in_trend":     in_trend,
                    "trend_up":     trend_up,
                    "trend_down":   trend_down,
                    "vol_ok":       vol_ok,
                    "rsi_ok_long":  rsi_confirm_long,
                    "rsi_ok_short": rsi_confirm_short,
                },
            },
        )

    # ── Choppiness Index (implementação própria) ──────────────────────────────

    @staticmethod
    def _choppiness(
        high:  pd.Series,
        low:   pd.Series,
        close: pd.Series,
        period: int,
    ) -> Optional[float]:
        """
        Choppiness Index para a janela mais recente de `period` barras.

        CHOP = 100 × log10( Σ ATR(1,n) / (Highest_High - Lowest_Low) )
                            ─────────────────────────────────────────────
                                           log10(n)

        Retorna float ou None se dados insuficientes.
        """
        if len(close) < period + 1:
            return None

        h = high.values[-period:]
        l = low.values[-period:]
        c = close.values[-period:]
        c_prev = close.values[-(period + 1): -1]

        # True Range para cada barra
        tr_arr = np.maximum(
            h - l,
            np.maximum(
                np.abs(h - c_prev),
                np.abs(l - c_prev),
            )
        )
        sum_atr   = float(tr_arr.sum())
        high_max  = float(h.max())
        low_min   = float(l.min())
        price_range = high_max - low_min

        if price_range == 0 or sum_atr == 0:
            return None

        chop = 100.0 * math.log10(sum_atr / price_range) / math.log10(period)
        return chop
