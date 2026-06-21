"""
ABCD Inertia Strategy ("N" pattern) for crypto day trade.

Long-only setup:
- Market keeps bullish inertia above a rising EMA21.
- Price forms a complex correction: A high -> B low -> C lower high -> D false
  break below B.
- AB and CD legs have similar amplitude.
- A strong bullish candle reclaims the prior low, suggesting a failed breakdown.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class AbcdInertiaStrategy(BaseStrategy):

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="PA002",
            name="PA002 - ABCD Inertia",
            description=(
                "Day trade long-only para 15m: opera a favor da inercia de alta "
                "quando uma correcao ABCD projeta CD proximo de 100% de AB, "
                "rompe falsamente o fundo B e fecha com candle forte de alta. "
                "Stop abaixo do candle de reversao e alvo no topo A."
            ),
            tags=["daytrade", "abcd", "inertia", "false-break", "15m"],
            criteria=[
                {"id": "c1_abcd", "label": "C1 AB≈CD", "description": "Correção ABCD com simetria válida."},
                {"id": "c2_ema21", "label": "C2 EMA21", "description": "Inércia de alta confirmada pela EMA21."},
            ],
            params={
                "ema_period": ParamDef(
                    type="int", default=21, min=8, max=80, step=1,
                    description="EMA usada para confirmar inercia de alta"),
                "ema_slope_lookback": ParamDef(
                    type="int", default=8, min=3, max=30, step=1,
                    description="Barras usadas para medir inclinacao da EMA"),
                "min_ema_slope_pct": ParamDef(
                    type="float", default=0.08, min=0.0, max=2.0, step=0.01,
                    description="Inclinacao minima da EMA (%)"),
                "correction_lookback": ParamDef(
                    type="int", default=80, min=30, max=180, step=5,
                    description="Janela para localizar A, B e C"),
                "abcd_tolerance": ParamDef(
                    type="float", default=0.25, min=0.05, max=0.60, step=0.05,
                    description="Tolerancia de simetria AB~CD (0.25 = +/-25%)"),
                "min_fake_break_pct": ParamDef(
                    type="float", default=0.03, min=0.0, max=1.0, step=0.01,
                    description="Rompimento minimo abaixo do fundo B (%)"),
                "min_body_ratio": ParamDef(
                    type="float", default=0.55, min=0.2, max=0.9, step=0.05,
                    description="Corpo minimo do candle de reversao vs range"),
                "close_near_high": ParamDef(
                    type="float", default=0.65, min=0.4, max=0.95, step=0.05,
                    description="Fechamento minimo dentro do range do candle"),
                "min_rr": ParamDef(
                    type="float", default=1.2, min=0.5, max=5.0, step=0.1,
                    description="Risco/retorno minimo ate o topo A"),
                "sl_buffer_pct": ParamDef(
                    type="float", default=0.05, min=0.0, max=1.0, step=0.01,
                    description="Buffer do stop abaixo da minima do candle (%)"),
                "use_liquidity_windows": ParamDef(
                    type="int", default=1, min=0, max=1, step=1,
                    description="1 para filtrar janelas de liquidez 10:00/21:00 BRT"),
                "liquidity_window_minutes": ParamDef(
                    type="int", default=90, min=15, max=240, step=15,
                    description="Minutos antes/depois das janelas 10:00 e 21:00 BRT"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.ema_period = p["ema_period"].default
        self.ema_slope_lookback = p["ema_slope_lookback"].default
        self.min_ema_slope_pct = p["min_ema_slope_pct"].default
        self.correction_lookback = p["correction_lookback"].default
        self.abcd_tolerance = p["abcd_tolerance"].default
        self.min_fake_break_pct = p["min_fake_break_pct"].default
        self.min_body_ratio = p["min_body_ratio"].default
        self.close_near_high = p["close_near_high"].default
        self.min_rr = p["min_rr"].default
        self.sl_buffer_pct = p["sl_buffer_pct"].default
        self.use_liquidity_windows = p["use_liquidity_windows"].default
        self.liquidity_window_minutes = p["liquidity_window_minutes"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k) and v is not None:
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        min_candles = max(
            int(self.ema_period) + int(self.ema_slope_lookback) + 2,
            int(self.correction_lookback) // 2,
            35,
        )
        if len(candles) < min_candles:
            return None

        df = pd.DataFrame([
            {"open": c.open, "high": c.high, "low": c.low,
             "close": c.close, "volume": c.volume}
            for c in candles
        ])

        ema = ta.ema(df["close"], length=int(self.ema_period))
        atr = ta.atr(df["high"], df["low"], df["close"], length=14)
        if ema is None or atr is None:
            return None

        signal_candle = candles[-1]
        close = signal_candle.close
        ema_now = float(ema.iloc[-1])
        ema_prev = float(ema.iloc[-int(self.ema_slope_lookback)])
        ema_slope_pct = (ema_now - ema_prev) / ema_prev * 100 if ema_prev else 0.0
        trend_ok = close > ema_now and ema_slope_pct >= float(self.min_ema_slope_pct)
        liquidity_ok = self._in_liquidity_window(signal_candle.epoch)

        pattern = self._find_pattern(candles)
        candle = self._signal_candle_quality(signal_candle)

        signal = Signal.HOLD
        hold_reason = ""
        if not liquidity_ok:
            hold_reason = "Fora das janelas de liquidez BRT"
        elif not trend_ok:
            hold_reason = "EMA21 sem inercia de alta suficiente"
        elif not pattern["valid_shape"]:
            hold_reason = pattern["reason"]
        elif not candle["valid"]:
            hold_reason = candle["reason"]
        elif pattern["rr"] < float(self.min_rr):
            hold_reason = "Risco/retorno insuficiente ate o topo A"
        else:
            signal = Signal.BUY

        atr_now = float(atr.iloc[-1])
        sl_price = pattern["sl_price"] or close - atr_now
        tp1_price = pattern["target_price"] or close + atr_now * float(self.min_rr)

        return StrategyResult(
            signal=signal,
            criteria_met=2 if signal in (Signal.BUY, Signal.SELL) else 0,
            criteria_total=2,
            indicators={
                "ema21": round(ema_now, 2),
                "ema_slope_pct": round(ema_slope_pct, 4),
                "trend_ok": 1 if trend_ok else 0,
                "liquidity_window": 1 if liquidity_ok else 0,
                "ab_cd_ratio": round(pattern["ab_cd_ratio"], 3),
                "fake_break_pct": round(pattern["fake_break_pct"], 4),
                "body_ratio": round(candle["body_ratio"], 3),
                "close_location": round(candle["close_location"], 3),
                "rr": round(pattern["rr"], 2),
                "a_price": pattern["a_price"] or 0,
                "b_price": pattern["b_price"] or 0,
                "c_price": pattern["c_price"] or 0,
                "d_price": pattern["d_price"] or 0,
                "close": round(close, 2),
                "atr": round(atr_now, 2),
            },
            metadata={
                "pattern": {
                    "a_price": pattern["a_price"],
                    "b_price": pattern["b_price"],
                    "c_price": pattern["c_price"],
                    "d_price": pattern["d_price"],
                    "entry_trigger": round(signal_candle.high, 2),
                },
                "sl_price": round(sl_price, 2),
                "tp1_price": round(tp1_price, 2),
                "sl_pct": round(abs(close - sl_price) / close, 5) if close else 0.0,
                "tp1_pct": round(abs(tp1_price - close) / close, 5) if close else 0.0,
                "ts_pct": round(abs(tp1_price - close) / close, 5) if close else 0.0,
            },
            hold_reason=hold_reason,
        )

    def _in_liquidity_window(self, epoch_ms: int) -> bool:
        if not int(self.use_liquidity_windows):
            return True
        dt = datetime.fromtimestamp(epoch_ms / 1000, tz=ZoneInfo("America/Sao_Paulo"))
        current = dt.hour * 60 + dt.minute
        window = int(self.liquidity_window_minutes)
        anchors = [10 * 60, 21 * 60]
        return any(abs(current - anchor) <= window for anchor in anchors)

    def _find_pattern(self, candles: list) -> dict:
        n = len(candles)
        sig_idx = n - 1
        start = max(0, sig_idx - int(self.correction_lookback))
        max_a_end = sig_idx - 3
        if max_a_end <= start + 2:
            return self._invalid_pattern("Aguardando correcao ABCD suficiente")

        # A: recent high that starts the complex correction.
        a_idx = max(range(start, max_a_end + 1), key=lambda i: candles[i].high)
        if a_idx >= sig_idx - 3:
            return self._invalid_pattern("Topo A muito proximo do candle de sinal")

        # B: first meaningful low after A, before the bounce C.
        b_idx = min(range(a_idx + 1, sig_idx - 2), key=lambda i: candles[i].low)
        if b_idx <= a_idx or b_idx >= sig_idx - 2:
            return self._invalid_pattern("Fundo B mal posicionado")

        c_idx = max(range(b_idx + 1, sig_idx), key=lambda i: candles[i].high)

        a_price = candles[a_idx].high
        b_price = candles[b_idx].low
        c_price = candles[c_idx].high
        d_price = candles[sig_idx].low
        close = candles[sig_idx].close

        if not (a_price > c_price > b_price):
            return self._invalid_pattern("Estrutura A-B-C nao e correcao de alta")

        fake_break_pct = (b_price - d_price) / b_price * 100 if b_price else 0.0
        if d_price >= b_price or fake_break_pct < float(self.min_fake_break_pct):
            return self._invalid_pattern("Ainda sem rompimento falso do fundo B")
        if close <= b_price:
            return self._invalid_pattern("Preco ainda nao recuperou o fundo B")

        ab = a_price - b_price
        cd = c_price - d_price
        ab_cd_ratio = cd / ab if ab > 0 else 0.0
        lo = 1.0 - float(self.abcd_tolerance)
        hi = 1.0 + float(self.abcd_tolerance)
        if not (lo <= ab_cd_ratio <= hi):
            return self._invalid_pattern("Projecao CD fora da simetria AB~CD",
                                         ab_cd_ratio=ab_cd_ratio,
                                         fake_break_pct=fake_break_pct)

        sl_price = d_price * (1 - float(self.sl_buffer_pct) / 100)
        target_price = a_price
        risk = close - sl_price
        reward = target_price - close
        rr = reward / risk if risk > 0 else 0.0

        return {
            "valid_shape": True,
            "reason": "",
            "a_price": round(a_price, 2),
            "b_price": round(b_price, 2),
            "c_price": round(c_price, 2),
            "d_price": round(d_price, 2),
            "ab_cd_ratio": ab_cd_ratio,
            "fake_break_pct": fake_break_pct,
            "sl_price": sl_price,
            "target_price": target_price,
            "rr": rr,
        }

    def _invalid_pattern(self, reason: str, ab_cd_ratio=0.0, fake_break_pct=0.0) -> dict:
        return {
            "valid_shape": False,
            "reason": reason,
            "a_price": None,
            "b_price": None,
            "c_price": None,
            "d_price": None,
            "ab_cd_ratio": ab_cd_ratio,
            "fake_break_pct": fake_break_pct,
            "sl_price": None,
            "target_price": None,
            "rr": 0.0,
        }

    def _signal_candle_quality(self, candle) -> dict:
        rng = candle.high - candle.low
        if rng <= 0:
            return {"valid": False, "reason": "Candle sem range", "body_ratio": 0.0, "close_location": 0.0}

        body = candle.close - candle.open
        body_ratio = abs(body) / rng
        close_location = (candle.close - candle.low) / rng

        if body <= 0:
            return {"valid": False, "reason": "Candle de reversao nao e alta", "body_ratio": body_ratio, "close_location": close_location}
        if body_ratio < float(self.min_body_ratio):
            return {"valid": False, "reason": "Candle de alta fraco", "body_ratio": body_ratio, "close_location": close_location}
        if close_location < float(self.close_near_high):
            return {"valid": False, "reason": "Fechamento distante da maxima", "body_ratio": body_ratio, "close_location": close_location}

        return {"valid": True, "reason": "", "body_ratio": body_ratio, "close_location": close_location}
