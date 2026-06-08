"""
strategies/bollinger.py — Bollinger Bands Breakout

Lógica:
  LONG  → vela fecha acima da banda superior (breakout de alta confirmado)
           E a vela anterior estava abaixo da banda superior (evita re-entradas)
  SHORT → vela fecha abaixo da banda inferior
           E a vela anterior estava acima da banda inferior
  Filtro de volume: volume da vela de sinal > média × volume_mult
"""

from __future__ import annotations
from typing import Optional

import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class BollingerStrategy(BaseStrategy):

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF013",
            name="TF013 - Bollinger Bands Breakout",
            description = (
                "Detecta rompimentos das Bandas de Bollinger com confirmação de volume. "
                "LONG quando o preço fecha acima da banda superior com volume elevado. "
                "SHORT quando fecha abaixo da banda inferior com volume elevado. "
                "Funciona bem em mercados com tendências fortes e volatilidade."
            ),
            tags   = ["breakout", "volatility", "bollinger", "volume"],
            criteria=[
                {"id": "c1_bb_position", "label": "C1 Posição BB", "description": "Preço rompe banda superior/inferior."},
                {"id": "c2_bb_width", "label": "C2 Largura BB", "description": "Bandas indicam volatilidade suficiente."},
                {"id": "c3_volume", "label": "C3 Volume", "description": "Volume confirma rompimento."},
            ],
            params = {
                "bb_period": ParamDef(
                    type="int", default=20, min=5, max=100, step=1,
                    description="Bollinger Bands (períodos)"),
                "bb_std": ParamDef(
                    type="float", default=2.0, min=1.0, max=4.0, step=0.1,
                    description="Desvios padrão das bandas"),
                "vol_period": ParamDef(
                    type="int", default=20, min=5, max=100, step=1,
                    description="Média de volume (períodos)"),
                "vol_mult": ParamDef(
                    type="float", default=1.5, min=1.0, max=5.0, step=0.1,
                    description="Volume mínimo (× média) para confirmar sinal"),
                "atr_period": ParamDef(
                    type="int", default=14, min=5, max=50, step=1,
                    description="ATR para cálculo de risco"),
                "atr_mult_sl": ParamDef(
                    type="float", default=1.5, min=0.5, max=5.0, step=0.1,
                    description="Multiplicador ATR para Stop Loss"),
                "tp1_pct": ParamDef(
                    type="float", default=2.0, min=0.5, max=10.0, step=0.1,
                    description="Take Profit 1 (%)"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.bb_period   = p["bb_period"].default
        self.bb_std      = p["bb_std"].default
        self.vol_period  = p["vol_period"].default
        self.vol_mult    = p["vol_mult"].default
        self.atr_period  = p["atr_period"].default
        self.atr_mult_sl = p["atr_mult_sl"].default
        self.tp1_pct     = p["tp1_pct"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        min_candles = max(self.bb_period, self.vol_period, self.atr_period) + 5
        if len(candles) < min_candles:
            return None

        df = pd.DataFrame([
            {"high": c.high, "low": c.low, "close": c.close, "volume": c.volume}
            for c in candles
        ])

        bb  = ta.bbands(df["close"], length=self.bb_period, std=self.bb_std)
        atr = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        vol_ma = df["volume"].rolling(self.vol_period).mean()

        if bb is None or atr is None:
            return None

        upper_s = self._bb_col(bb, "BBU")
        lower_s = self._bb_col(bb, "BBL")
        mid_s   = self._bb_col(bb, "BBM")
        if upper_s is None or lower_s is None or mid_s is None:
            return None

        upper = float(upper_s.iloc[-1])
        lower = float(lower_s.iloc[-1])
        mid   = float(mid_s.iloc[-1])
        upper_p = float(upper_s.iloc[-2])
        lower_p = float(lower_s.iloc[-2])

        atr_now = float(atr.iloc[-1])
        vol_avg = float(vol_ma.iloc[-1])
        vol_now = candles[-1].volume
        close   = candles[-1].close
        close_p = candles[-2].close

        vol_ok = vol_now >= vol_avg * self.vol_mult

        long_ready = close > upper and vol_ok
        short_ready = close < lower and vol_ok
        trigger_breakout_up = close > upper and close_p <= upper_p
        trigger_breakout_down = close < lower and close_p >= lower_p

        if long_ready:
            signal = Signal.BUY
        elif short_ready:
            signal = Signal.SELL
        else:
            signal = Signal.HOLD

        return StrategyResult(
            signal     = signal,
            criteria_met= 3 if signal in (Signal.BUY, Signal.SELL) else 0,
            criteria_total= 3,
            indicators = {
                "bb_upper": round(upper, 2),
                "bb_lower": round(lower, 2),
                "bb_mid":   round(mid, 2),
                "atr":      round(atr_now, 2),
                "close":    round(close, 2),
                "volume":   round(vol_now, 4),
                "vol_avg":  round(vol_avg, 4),
            },
            metadata = {
                "entry_mode": "criteria_ready",
                "trigger_breakout": (
                    trigger_breakout_up if signal == Signal.BUY
                    else trigger_breakout_down
                ),
                "sl_price":  round(close - atr_now * self.atr_mult_sl, 2)
                             if signal == Signal.BUY
                             else round(close + atr_now * self.atr_mult_sl, 2),
                "tp1_price": round(close * (1 + self.tp1_pct / 100), 2)
                             if signal == Signal.BUY
                             else round(close * (1 - self.tp1_pct / 100), 2),
            },
        )

    def _bb_col(self, bb, prefix: str):
        """pandas-ta can format std as 2 or 2.0 depending on version."""
        exact = f"{prefix}_{self.bb_period}_{self.bb_std}"
        if exact in bb.columns:
            return bb[exact]
        candidates = [c for c in bb.columns if c.startswith(f"{prefix}_{self.bb_period}_")]
        return bb[candidates[0]] if candidates else None
