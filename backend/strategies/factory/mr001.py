"""
strategies/mean_reversion.py — Afastamento Médio (Mean Reversion)

Design
──────
Mede o afastamento percentual do preço em relação a uma EMA longa (padrão 80).
Quando o afastamento atinge uma zona de exaustão (> ±buy_zone_pct %), aguarda
o cruzamento do afastamento sobre sua linha de sinal (EMA-5 do desvio) para
confirmar a reversão em direção à média.

Indicadores
───────────
  deviation  = (close − EMA) / EMA × 100   [% de afastamento]
  signal     = EMA(signal_period) do deviation  [linha de sinal]

Regras de entrada
─────────────────
  BUY  quando: deviation < −buy_zone_pct  AND  deviation cruza signal de baixo para cima
  SELL quando: deviation >  buy_zone_pct  AND  deviation cruza signal de cima para baixo

Gestão de risco
───────────────
  SL   = close ± ATR × sl_mult
  TP1  = SL_dist × tp1_rr  (fecha 50 % da posição)
  TP2  = SL_dist × tp2_rr  (trailing stop restante)
"""

from __future__ import annotations
from typing import Optional

import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class MeanReversionStrategy(BaseStrategy):

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="MR001",
            name="MR001 - Mean Reversion (ByeBot)",
            description = (
                "Estratégia de reversão à média baseada no afastamento percentual "
                "do preço em relação a uma EMA longa (padrão 80 períodos). "
                "Quando o preço se afasta além de uma zona de exaustão (ex.: −7 %), "
                "aguarda o cruzamento do afastamento sobre sua linha de sinal "
                "(EMA-5 do desvio) para entrar na direção da reversão. "
                "Risco adaptado pelo ATR atual."
            ),
            tags   = ["mean_reversion", "ema", "oscillator", "atr"],
            criteria=[
                {
                    "id": "c1_exhaustion_zone",
                    "label": "C1 Zona de exaustão",
                    "description": "Desvio percentual do preço em relação à EMA longa entra em zona de compra/venda.",
                },
                {
                    "id": "c2_signal_cross",
                    "label": "C2 Cruzamento",
                    "description": "Desvio cruza a linha de sinal na direção da reversão.",
                },
            ],
            params = {
                "ema_period": ParamDef(
                    type="int", default=80, min=20, max=200, step=5,
                    description="Período da EMA de referência (base do afastamento)"),
                "signal_period": ParamDef(
                    type="int", default=5, min=2, max=20, step=1,
                    description="Período da EMA da linha de sinal do afastamento"),
                "buy_zone_pct": ParamDef(
                    type="float", default=7.0, min=1.0, max=20.0, step=0.5,
                    description="Afastamento mínimo (%) para entrar na zona de reversão"),
                "atr_period": ParamDef(
                    type="int", default=14, min=5, max=30, step=1,
                    description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(
                    type="float", default=2.5, min=0.5, max=4.0, step=0.1,
                    description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(
                    type="float", default=2.0, min=1.0, max=5.0, step=0.5,
                    description="Risk:Reward do TP1 (fecha 50 % da posição)"),
                "tp2_rr": ParamDef(
                    type="float", default=3.0, min=1.5, max=8.0, step=0.5,
                    description="Risk:Reward do TP2 (trailing stop após TP1)"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.ema_period    = p["ema_period"].default
        self.signal_period = p["signal_period"].default
        self.buy_zone_pct  = p["buy_zone_pct"].default
        self.atr_period    = p["atr_period"].default
        self.sl_mult       = p["sl_mult"].default
        self.tp1_rr        = p["tp1_rr"].default
        self.tp2_rr        = p["tp2_rr"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        min_len = max(self.ema_period, self.atr_period) + self.signal_period + 5
        if len(candles) < min_len:
            return None

        df = pd.DataFrame([
            {"high": c.high, "low": c.low, "close": c.close}
            for c in candles
        ])

        # ── 1. EMA longa ──────────────────────────────────────────────────────
        ema_series = ta.ema(df["close"], length=self.ema_period)
        if ema_series is None or ema_series.isna().all():
            return None

        # ── 2. Afastamento percentual ─────────────────────────────────────────
        dev_series = (df["close"] - ema_series) / ema_series * 100

        # ── 3. Linha de sinal (EMA do afastamento) ────────────────────────────
        sig_series = ta.ema(dev_series, length=self.signal_period)
        if sig_series is None or sig_series.isna().all():
            return None

        dev_now  = float(dev_series.iloc[-1])
        dev_prev = float(dev_series.iloc[-2])
        sig_now  = float(sig_series.iloc[-1])
        sig_prev = float(sig_series.iloc[-2])

        # ── 4. ATR ────────────────────────────────────────────────────────────
        atr_series = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        if atr_series is None or atr_series.isna().all():
            return None
        atr_val = float(atr_series.iloc[-1])
        close   = candles[-1].close
        ema_val = float(ema_series.iloc[-1])

        # ── 5. Cruzamento do desvio sobre a linha de sinal ────────────────────
        cross_up   = dev_prev < sig_prev and dev_now >= sig_now
        cross_down = dev_prev > sig_prev and dev_now <= sig_now

        in_buy_zone  = dev_now < -self.buy_zone_pct
        in_sell_zone = dev_now >  self.buy_zone_pct
        in_zone = in_buy_zone or in_sell_zone
        cross_aligned = (in_buy_zone and cross_up) or (in_sell_zone and cross_down)
        cross_conflict = (in_buy_zone and cross_down) or (in_sell_zone and cross_up)
        criteria_total = 2
        criteria_met = int(in_zone) + int(cross_aligned)

        # ── 6. Sinal ──────────────────────────────────────────────────────────
        hold_reason = ""
        if in_buy_zone and cross_up:
            signal = Signal.BUY
        elif in_sell_zone and cross_down:
            signal = Signal.SELL
        else:
            signal = Signal.HOLD
            if not (in_buy_zone or in_sell_zone):
                hold_reason = f"Afastamento neutro: {dev_now:.2f}% (zona ≥ {self.buy_zone_pct}%)"
            elif in_buy_zone:
                hold_reason = f"Na zona compra ({dev_now:.2f}%) — aguardando cruzamento"
            else:
                hold_reason = f"Na zona venda ({dev_now:.2f}%) — aguardando cruzamento"

        # ── 7. Preços de risco ────────────────────────────────────────────────
        sl_dist = atr_val * self.sl_mult
        if signal == Signal.BUY:
            sl_price  = round(close - sl_dist, 4)
            tp1_price = round(close + sl_dist * self.tp1_rr, 4)
        else:
            sl_price  = round(close + sl_dist, 4)
            tp1_price = round(close - sl_dist * self.tp1_rr, 4)

        return StrategyResult(
            signal      = signal,
            criteria_met= criteria_met,
            criteria_total= criteria_total,
            hold_reason = hold_reason,
            indicators  = {
                "deviation":    round(dev_now, 4),
                "signal_line":  round(sig_now, 4),
                "ema":          round(ema_val, 4),
                "atr":          round(atr_val, 4),
                "close":        round(close, 4),
                "in_buy_zone":  float(in_buy_zone),
                "in_sell_zone": float(in_sell_zone),
                "cross_up":     float(cross_up),
                "cross_down":   float(cross_down),
                "cross_aligned": float(cross_aligned),
                "cross_conflict": float(cross_conflict),
            },
            metadata = {
                "sl_price":   sl_price,
                "tp1_price":  tp1_price,
                "sl_pct":     round(sl_dist / close, 5),
                "tp1_pct":    round(sl_dist * self.tp1_rr / close, 5),
                "ts_pct":     round(sl_dist * self.tp2_rr / close, 5),
                "diag": {
                    "in_buy_zone":  in_buy_zone,
                    "in_sell_zone": in_sell_zone,
                    "cross_up":     cross_up,
                    "cross_down":   cross_down,
                    "dev_now":      round(dev_now, 4),
                    "sig_now":      round(sig_now, 4),
                },
            },
        )
