"""
strategies/ema_vwap.py — EMA Crossover + VWAP Filter + ATR Risk

Estratégia EMA+VWAP consolidada como classe reutilizável.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class EmaVwapStrategy(BaseStrategy):

    # ── Metadados ────────────────────────────────────────────────────────────

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF001",
            name="TF001 - EMA + VWAP Trend",
            description = (
                "Detecta cruzamentos da EMA rápida sobre a EMA lenta "
                "com confirmação do VWAP diário. "
                "Entra LONG quando EMA21 cruza EMA55 para cima e o preço "
                "está acima do VWAP; SHORT no cruzamento inverso abaixo do VWAP. "
                "Stop-Loss e Trailing Stop baseados em ATR."
            ),
            tags   = ["trend", "crossover", "vwap", "atr"],
            params = {
                "ema_fast": ParamDef(
                    type="int", default=21, min=3, max=100, step=1,
                    description="EMA rápida (períodos)"),
                "ema_slow": ParamDef(
                    type="int", default=55, min=5, max=200, step=1,
                    description="EMA lenta (períodos)"),
                "atr_period": ParamDef(
                    type="int", default=14, min=5, max=50, step=1,
                    description="ATR (períodos)"),
                "atr_mult_sl": ParamDef(
                    type="float", default=1.5, min=0.5, max=5.0, step=0.1,
                    description="Multiplicador ATR para Stop Loss"),
                "atr_mult_ts": ParamDef(
                    type="float", default=2.0, min=0.5, max=5.0, step=0.1,
                    description="Multiplicador ATR para Trailing Stop"),
                "tp1_pct": ParamDef(
                    type="float", default=2.0, min=0.5, max=10.0, step=0.1,
                    description="Take Profit 1 (% de ganho para fechar 50%)"),
            },
            criteria=[
                {"id": "c1", "label": "Tendência EMA", "description": "EMA rápida alinhada com EMA lenta"},
                {"id": "c2", "label": "Filtro VWAP", "description": "Preço acima/abaixo do VWAP"},
            ],
        )

    # ── Inicialização ─────────────────────────────────────────────────────────

    def __init__(self):
        p = self.info().params
        self.ema_fast    = p["ema_fast"].default
        self.ema_slow    = p["ema_slow"].default
        self.atr_period  = p["atr_period"].default
        self.atr_mult_sl = p["atr_mult_sl"].default
        self.atr_mult_ts = p["atr_mult_ts"].default
        self.tp1_pct     = p["tp1_pct"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    # ── Cálculo ───────────────────────────────────────────────────────────────

    def compute(self, candles: list) -> Optional[StrategyResult]:
        min_candles = max(self.ema_slow, self.atr_period) + 5
        if len(candles) < min_candles:
            return None

        df = pd.DataFrame([
            {"open": c.open, "high": c.high, "low": c.low,
             "close": c.close, "volume": c.volume}
            for c in candles
        ])

        ema_f = ta.ema(df["close"], length=self.ema_fast)
        ema_l = ta.ema(df["close"], length=self.ema_slow)
        atr   = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)

        if ema_f is None or ema_l is None or atr is None:
            return None

        ef, el     = float(ema_f.iloc[-1]), float(ema_l.iloc[-1])
        ef_p, el_p = float(ema_f.iloc[-2]), float(ema_l.iloc[-2])
        curr_atr   = float(atr.iloc[-1])
        close      = candles[-1].close
        vwap       = self._vwap(candles)

        crossover_up   = ef_p <= el_p and ef > el
        crossover_down = ef_p >= el_p and ef < el
        trend_alta     = ef > el and close > vwap
        trend_baixa    = ef < el and close < vwap

        if trend_alta:
            signal = Signal.BUY
        elif trend_baixa:
            signal = Signal.SELL
        else:
            signal = Signal.HOLD

        criteria_met = 3 if signal in (Signal.BUY, Signal.SELL) else 0
        
        return StrategyResult(
            signal     = signal,
            indicators = {
                "ema_fast": round(ef, 2),
                "ema_slow": round(el, 2),
                "vwap":     round(vwap, 2),
                "atr":      round(curr_atr, 2),
                "close":    round(close, 2),
            },
            metadata = {
                "entry_mode": "criteria_ready",
                "trigger_cross": crossover_up if signal == Signal.BUY else crossover_down,
                "sl_pct":  self.atr_mult_sl * curr_atr / close,
                "tp1_pct": self.tp1_pct / 100,
                "ts_pct":  self.atr_mult_ts * curr_atr / close,
                "sl_price":  round(close - curr_atr * self.atr_mult_sl, 2)
                             if signal == Signal.BUY
                             else round(close + curr_atr * self.atr_mult_sl, 2),
                "tp1_price": round(close * (1 + self.tp1_pct / 100), 2)
                             if signal == Signal.BUY
                             else round(close * (1 - self.tp1_pct / 100), 2),
            },
            criteria_met=criteria_met,
            criteria_total=3,
        )

    # ── VWAP diário ───────────────────────────────────────────────────────────

    def _vwap(self, candles: list) -> float:
        today_utc   = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0)
        midnight_ms = int(today_utc.timestamp()) * 1000
        today = [c for c in candles if c.epoch >= midnight_ms] or candles[-10:]
        total_vol = sum(c.volume for c in today)
        if total_vol == 0:
            return candles[-1].close
        return sum(c.hlc3 * c.volume for c in today) / total_vol
