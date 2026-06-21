"""
OTR Value Zones strategy.

O - Onde operar: trade only near the recent range edges.
T - Tendencia/Timing: simplified wave 1/2 plus upbar/downbar trigger.
R - Risco: require minimum reward/risk, default 2:1.

The strategy avoids the middle of the range by design.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class OtrValueZonesStrategy(BaseStrategy):

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="PA003",
            name="PA003 - OTR Value Zones",
            description=(
                "Estrategia de topo e fundo baseada em regioes de valor. "
                "Compra apenas no fundo do range, vende apenas no topo, evita "
                "o meio do caminho e exige gatilho por upbar/downbar com "
                "risco/retorno minimo."
            ),
            tags=["range", "support-resistance", "price-action", "risk"],
            criteria=[
                {"id": "c1_zone", "label": "C1 Zona", "description": "Preço está em borda operável do range."},
                {"id": "c2_range_atr", "label": "C2 Range/ATR", "description": "Range possui amplitude suficiente versus ATR."},
                {"id": "c3_wave", "label": "C3 Onda", "description": "Onda 1/2 confirma topo ou fundo."},
                {"id": "c4_trigger", "label": "C4 Gatilho", "description": "Upbar/downbar dispara a onda 3."},
                {"id": "c5_rr", "label": "C5 R:R", "description": "Risco/retorno mínimo atendido."},
            ],
            params={
                "range_lookback": ParamDef(
                    type="int", default=80, min=20, max=220, step=5,
                    description="Barras usadas para formar o retangulo de valor"),
                "edge_zone_pct": ParamDef(
                    type="float", default=20.0, min=5.0, max=35.0, step=1.0,
                    description="Percentual das bordas considerado zona operavel"),
                "min_wave_move_pct": ParamDef(
                    type="float", default=0.25, min=0.05, max=3.0, step=0.05,
                    description="Movimento minimo da onda 1 em relacao ao preco"),
                "max_pullback_pct": ParamDef(
                    type="float", default=80.0, min=30.0, max=100.0, step=5.0,
                    description="Pullback maximo da onda 2 vs onda 1"),
                "min_rr": ParamDef(
                    type="float", default=2.0, min=0.5, max=5.0, step=0.1,
                    description="Risco/retorno minimo"),
                "stop_buffer_pct": ParamDef(
                    type="float", default=0.05, min=0.0, max=1.0, step=0.01,
                    description="Buffer do stop alem da extremidade/candle (%)"),
                "use_atr_filter": ParamDef(
                    type="int", default=1, min=0, max=1, step=1,
                    description="Usar ATR minimo para evitar range estreito"),
                "min_range_atr": ParamDef(
                    type="float", default=2.0, min=0.5, max=10.0, step=0.5,
                    description="Range minimo em multiplos de ATR"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.range_lookback = p["range_lookback"].default
        self.edge_zone_pct = p["edge_zone_pct"].default
        self.min_wave_move_pct = p["min_wave_move_pct"].default
        self.max_pullback_pct = p["max_pullback_pct"].default
        self.min_rr = p["min_rr"].default
        self.stop_buffer_pct = p["stop_buffer_pct"].default
        self.use_atr_filter = p["use_atr_filter"].default
        self.min_range_atr = p["min_range_atr"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k) and v is not None:
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        min_candles = max(int(self.range_lookback), 25)
        if len(candles) < min_candles:
            return None

        window = candles[-int(self.range_lookback):]
        df = pd.DataFrame([
            {"high": c.high, "low": c.low, "close": c.close}
            for c in window
        ])
        atr = ta.atr(df["high"], df["low"], df["close"], length=14)
        atr_now = float(atr.iloc[-1]) if atr is not None else 0.0

        range_high = max(c.high for c in window[:-1])
        range_low = min(c.low for c in window[:-1])
        range_size = range_high - range_low
        if range_size <= 0:
            return None

        close = candles[-1].close
        position_pct = (close - range_low) / range_size * 100
        edge = float(self.edge_zone_pct)
        in_bottom = position_pct <= edge
        in_top = position_pct >= 100 - edge
        in_middle = not in_bottom and not in_top
        range_atr = range_size / atr_now if atr_now > 0 else 0.0
        atr_ok = not int(self.use_atr_filter) or range_atr >= float(self.min_range_atr)

        long_setup = self._long_setup(candles, range_low, range_high)
        short_setup = self._short_setup(candles, range_low, range_high)

        signal = Signal.HOLD
        selected = long_setup if in_bottom else short_setup if in_top else {}
        hold_reason = ""
        if in_middle:
            hold_reason = "Meio do caminho - sem trade"
        elif not atr_ok:
            hold_reason = "Retangulo estreito demais vs ATR"
        elif in_bottom and not long_setup["valid"]:
            hold_reason = long_setup["reason"]
        elif in_top and not short_setup["valid"]:
            hold_reason = short_setup["reason"]
        else:
            signal = Signal.BUY if in_bottom else Signal.SELL

        rr = float(selected.get("rr", 0.0)) if selected else 0.0
        if signal != Signal.HOLD and rr < float(self.min_rr):
            signal = Signal.HOLD
            hold_reason = "Risco/retorno abaixo do minimo"

        sl_price = selected.get("sl_price")
        tp1_price = selected.get("target_price")
        if sl_price is None:
            sl_price = close - atr_now if in_bottom else close + atr_now
        if tp1_price is None:
            tp1_price = range_high if in_bottom else range_low

        # Calcula contagem de critérios atendidos para a UI
        criteria_total = 5
        criteria_met = 0
        if not in_middle: criteria_met += 1
        if atr_ok: criteria_met += 1
        if selected.get("wave_ok"): criteria_met += 1
        if selected.get("trigger_ok"): criteria_met += 1
        if rr >= float(self.min_rr): criteria_met += 1

        return StrategyResult(
            signal=signal,
            indicators={
                "range_high": round(range_high, 2),
                "range_low": round(range_low, 2),
                "range_pos_pct": round(position_pct, 2),
                "zone": -1 if in_bottom else 1 if in_top else 0,
                "middle_zone": 1 if in_middle else 0,
                "range_atr": round(range_atr, 2),
                "atr_ok": 1 if atr_ok else 0,
                "wave_ok": 1 if selected.get("wave_ok") else 0,
                "trigger_ok": 1 if selected.get("trigger_ok") else 0,
                "rr": round(rr, 2),
                "close": round(close, 2),
                "atr": round(atr_now, 2),
            },
            metadata={
                "sl_price": round(sl_price, 2),
                "tp1_price": round(tp1_price, 2),
                "sl_pct": round(abs(close - sl_price) / close, 5) if close else 0.0,
                "tp1_pct": round(abs(tp1_price - close) / close, 5) if close else 0.0,
                "ts_pct": round(abs(tp1_price - close) / close, 5) if close else 0.0,
                "zone": "bottom" if in_bottom else "top" if in_top else "middle",
            },
            hold_reason=hold_reason,
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )

    def _long_setup(self, candles: list, range_low: float, range_high: float) -> dict:
        prev = candles[-2]
        curr = candles[-1]
        recent = candles[-8:-1]
        swing_low = min(c.low for c in recent)
        swing_high = max(c.high for c in recent)
        wave_move_pct = (swing_high - swing_low) / swing_low * 100 if swing_low else 0.0
        no_break = curr.low >= range_low
        upbar = curr.high > prev.high and curr.low > prev.low and curr.close > curr.open

        stop = min(curr.low, swing_low, range_low) * (1 - float(self.stop_buffer_pct) / 100)
        target = range_high
        risk = curr.close - stop
        reward = target - curr.close
        rr = reward / risk if risk > 0 else 0.0

        wave_ok = wave_move_pct >= float(self.min_wave_move_pct) and no_break
        if not wave_ok:
            reason = "Onda 1/2 ainda nao confirmou fundo"
        elif not upbar:
            reason = "Aguardando upbar para onda 3"
        else:
            reason = ""

        return {
            "valid": wave_ok and upbar,
            "reason": reason,
            "wave_ok": wave_ok,
            "trigger_ok": upbar,
            "sl_price": stop,
            "target_price": target,
            "rr": rr,
        }

    def _short_setup(self, candles: list, range_low: float, range_high: float) -> dict:
        prev = candles[-2]
        curr = candles[-1]
        recent = candles[-8:-1]
        swing_low = min(c.low for c in recent)
        swing_high = max(c.high for c in recent)
        wave_move_pct = (swing_high - swing_low) / swing_high * 100 if swing_high else 0.0
        no_break = curr.high <= range_high
        downbar = curr.high < prev.high and curr.low < prev.low and curr.close < curr.open

        stop = max(curr.high, swing_high, range_high) * (1 + float(self.stop_buffer_pct) / 100)
        target = range_low
        risk = stop - curr.close
        reward = curr.close - target
        rr = reward / risk if risk > 0 else 0.0

        wave_ok = wave_move_pct >= float(self.min_wave_move_pct) and no_break
        if not wave_ok:
            reason = "Onda 1/2 ainda nao confirmou topo"
        elif not downbar:
            reason = "Aguardando downbar para onda 3"
        else:
            reason = ""

        return {
            "valid": wave_ok and downbar,
            "reason": reason,
            "wave_ok": wave_ok,
            "trigger_ok": downbar,
            "sl_price": stop,
            "target_price": target,
            "rr": rr,
        }
