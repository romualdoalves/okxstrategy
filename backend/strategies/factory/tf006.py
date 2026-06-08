"""
strategies/linda_raschke.py — A007 Linda Raschke Swing Trade

Estratégia de Swing Trade baseada na metodologia da trader Linda Raschke.
Combina três camadas temporais:
  1. Filtro de força — ADX(14) semanal ≥ 25 e subindo
  2. Filtro de momento — Canal de Donchian 52 semanas (renovando extremo)
  3. Gatilho diário — Recuo à EMA 21 + rompimento da máxima semanal anterior
                      OU virada da EMA 8

SL: mínima (BUY) / máxima (SELL) do candle de sinal
TP: entry ± 2× amplitude do candle de sinal
"""

from __future__ import annotations
from typing import Optional, Any
import numpy as np
import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class LindaRaschkeStrategy(BaseStrategy):

    @classmethod
    def extra_timeframes(cls) -> list[str]:
        return ["1W"]

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF006",
            name="TF006 - Linda Raschke (Swing Trade)",
            description=(
                "Swing Trade baseado na metodologia de Linda Raschke. "
                "Três camadas: (1) ADX(14) semanal ≥ 25 em alta — ativo tendencial; "
                "(2) Canal de Donchian 52 semanas — renovando máximas ou mínimas históricas; "
                "(3) Recuo à EMA 21 diária + gatilho: rompimento da máxima semanal anterior "
                "OU EMA 8 virando na direção da tendência. "
                "SL na mínima/máxima do candle de sinal. TP = 2× amplitude do candle."
            ),
            tags=["swing", "trend", "donchian", "adx", "ema", "raschke", "weekly"],
            recommended_timeframe="1D",
            params={
                "adx_period": ParamDef(
                    type="int", default=14, min=7, max=28, step=1,
                    description="Período do ADX no semanal.",
                ),
                "adx_threshold": ParamDef(
                    type="int", default=25, min=20, max=40, step=1,
                    description="ADX mínimo para confirmar tendência forte.",
                ),
                "donchian_period": ParamDef(
                    type="int", default=52, min=26, max=104, step=1,
                    description="Semanas do Canal de Donchian (52 = 1 ano).",
                ),
                "ema_fast": ParamDef(
                    type="int", default=8, min=3, max=21, step=1,
                    description="EMA rápida diária (gatilho de virada).",
                ),
                "ema_slow": ParamDef(
                    type="int", default=21, min=10, max=50, step=1,
                    description="EMA lenta diária — referência de regressão à média.",
                ),
                "ema_touch_bars": ParamDef(
                    type="int", default=5, min=1, max=10, step=1,
                    description="Quantas velas diárias olhar para trás em busca do toque na EMA 21.",
                ),
                "tp_multiplier": ParamDef(
                    type="float", default=2.0, min=1.0, max=4.0, step=0.5,
                    description="Múltiplo da amplitude do candle de sinal para o Take Profit.",
                ),
            },
            criteria=[{"id": "c1", "label": "— ADX", "description": "— ADX"}, {"id": "c2", "label": "— Donchian break", "description": "— Donchian break"}, {"id": "c3", "label": "— Recuo à EMA 21", "description": "— Recuo à EMA 21"}, {"id": "c4", "label": "— Gatilho de entrada", "description": "— Gatilho de entrada"}],
        )

    def __init__(self):
        p = self.info().params
        self.adx_period      = p["adx_period"].default
        self.adx_threshold   = p["adx_threshold"].default
        self.donchian_period = p["donchian_period"].default
        self.ema_fast        = p["ema_fast"].default
        self.ema_slow        = p["ema_slow"].default
        self.ema_touch_bars  = p["ema_touch_bars"].default
        self.tp_multiplier   = p["tp_multiplier"].default

    def set_params(self, params: dict) -> None:
        self.adx_period      = int(params.get("adx_period",      self.adx_period))
        self.adx_threshold   = int(params.get("adx_threshold",   self.adx_threshold))
        self.donchian_period = int(params.get("donchian_period",  self.donchian_period))
        self.ema_fast        = int(params.get("ema_fast",         self.ema_fast))
        self.ema_slow        = int(params.get("ema_slow",         self.ema_slow))
        self.ema_touch_bars  = int(params.get("ema_touch_bars",   self.ema_touch_bars))
        self.tp_multiplier   = float(params.get("tp_multiplier",  self.tp_multiplier))

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(
        self,
        candles: list,
        context: dict | None = None,
    ) -> Optional[StrategyResult]:
        # ── Dados diários (timeframe principal do bot) ────────────────────────
        min_daily = self.ema_slow + self.ema_touch_bars + 5
        if not candles or len(candles) < min_daily:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_daily} velas diárias ({len(candles)} recebidas).",
                indicators=self._empty_indicators(),
            )

        df = pd.DataFrame({
            "open":   [c.open   for c in candles],
            "high":   [c.high   for c in candles],
            "low":    [c.low    for c in candles],
            "close":  [c.close  for c in candles],
            "volume": [c.volume for c in candles],
        })

        # ── EMA 8 e EMA 21 diários ────────────────────────────────────────────
        ema8_s  = ta.ema(df["close"], length=self.ema_fast)
        ema21_s = ta.ema(df["close"], length=self.ema_slow)
        atr_s   = ta.atr(df["high"], df["low"], df["close"], length=14)

        if ema8_s is None or ema21_s is None:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Dados insuficientes para calcular EMAs diárias.",
                indicators=self._empty_indicators(),
            )

        ema8_val  = float(ema8_s.iloc[-1])
        ema21_val = float(ema21_s.iloc[-1])
        atr_val   = float(atr_s.iloc[-1]) if atr_s is not None else df["close"].iloc[-1] * 0.01

        last      = candles[-1]
        close     = float(last.close)
        high_d    = float(last.high)
        low_d     = float(last.low)

        dist_ema21_pct = (close - ema21_val) / ema21_val * 100 if ema21_val > 0 else 0.0

        # ── Toque na EMA 21 (últimas N velas) ────────────────────────────────
        touched_ema21 = 0
        window = candles[-(self.ema_touch_bars + 1):]
        ema21_window = ema21_s.iloc[-(self.ema_touch_bars + 1):]
        for i, c in enumerate(window):
            ema_ref = float(ema21_window.iloc[i]) if i < len(ema21_window) else ema21_val
            if c.low <= ema_ref <= c.high:
                touched_ema21 = 1
                break

        # ── EMA 8 slope (virada de direção) ──────────────────────────────────
        ema8_slope = 0.0
        if len(ema8_s) >= 3:
            e0 = float(ema8_s.iloc[-1])
            e1 = float(ema8_s.iloc[-2])
            e2 = float(ema8_s.iloc[-3])
            ema8_slope = e0 - e1
            ema8_turned_up   = (e0 > e1) and (e1 < e2)  # virou para cima
            ema8_turned_down = (e0 < e1) and (e1 > e2)  # virou para baixo
        else:
            ema8_turned_up = ema8_turned_down = False

        # ── Dados semanais (extra_timeframes) ────────────────────────────────
        candles_w = (context or {}).get("extra_candles", {}).get("1W", [])
        min_weekly = self.donchian_period + self.adx_period + 5

        if len(candles_w) < min_weekly:
            inds = self._build_indicators(
                ema8_val, ema21_val, atr_val, ema8_slope,
                touched_ema21, dist_ema21_pct,
            )
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aguardando dados semanais ({len(candles_w)}/{min_weekly} barras 1W).",
                indicators=inds,
                criteria_met=0, criteria_total=4,
            )

        df_w = pd.DataFrame({
            "high":  [c.high  for c in candles_w],
            "low":   [c.low   for c in candles_w],
            "close": [c.close for c in candles_w],
        })

        # ── C1: ADX(14) semanal ───────────────────────────────────────────────
        adx_df = ta.adx(df_w["high"], df_w["low"], df_w["close"], length=self.adx_period)
        if adx_df is None or adx_df.empty:
            inds = self._build_indicators(
                ema8_val, ema21_val, atr_val, ema8_slope,
                touched_ema21, dist_ema21_pct,
            )
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Dados insuficientes para calcular ADX semanal.",
                indicators=inds,
            )

        adx_col = [c for c in adx_df.columns if c.startswith("ADX_")]
        if not adx_col:
            inds = self._build_indicators(
                ema8_val, ema21_val, atr_val, ema8_slope,
                touched_ema21, dist_ema21_pct,
            )
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Coluna ADX não encontrada.",
                indicators=inds,
            )

        adx_series = adx_df[adx_col[0]]
        adx_val    = float(adx_series.iloc[-1])
        adx_prev   = float(adx_series.iloc[-2]) if len(adx_series) >= 2 else adx_val
        adx_rising = 1 if adx_val > adx_prev else 0

        # ── C2: Canal de Donchian 52 semanas ─────────────────────────────────
        don = ta.donchian(
            df_w["high"], df_w["low"],
            lower_length=self.donchian_period,
            upper_length=self.donchian_period,
        )
        if don is None or don.empty:
            inds = self._build_indicators(
                ema8_val, ema21_val, atr_val, ema8_slope,
                touched_ema21, dist_ema21_pct,
                adx_w=adx_val, adx_rising=adx_rising,
            )
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Dados insuficientes para o Canal de Donchian.",
                indicators=inds,
            )

        upper_col = [c for c in don.columns if "DCU" in c or "upper" in c.lower()]
        lower_col = [c for c in don.columns if "DCL" in c or "lower" in c.lower()]

        don_upper = float(don[upper_col[0]].iloc[-1]) if upper_col else 0.0
        don_lower = float(don[lower_col[0]].iloc[-1]) if lower_col else 0.0

        weekly_close = float(df_w["close"].iloc[-1])
        # Semana anterior (índice -2 — a última está em formação)
        prev_week_high = float(df_w["high"].iloc[-2]) if len(candles_w) >= 2 else 0.0
        prev_week_low  = float(df_w["low"].iloc[-2])  if len(candles_w) >= 2 else 0.0

        # Break: +1 = renovando máxima 52s, -1 = renovando mínima, 0 = dentro
        if don_upper > 0 and weekly_close >= don_upper:
            donchian_break = 1
        elif don_lower > 0 and weekly_close <= don_lower:
            donchian_break = -1
        else:
            donchian_break = 0

        # ── Avaliação dos critérios ───────────────────────────────────────────
        inds = self._build_indicators(
            ema8_val, ema21_val, atr_val, ema8_slope,
            touched_ema21, dist_ema21_pct,
            adx_w=adx_val, adx_rising=adx_rising,
            donchian_upper=don_upper, donchian_lower=don_lower,
            donchian_break=donchian_break,
            prev_week_high=prev_week_high, prev_week_low=prev_week_low,
        )

        # C1 — ADX
        c1_ok = adx_val >= float(self.adx_threshold) and bool(adx_rising)

        if not c1_ok:
            if adx_val < 20:
                reason = f"ADX {adx_val:.1f} < 20 — ativo lateral ignorado."
            elif adx_val < float(self.adx_threshold):
                reason = f"ADX {adx_val:.1f} — aguardando cruzar {self.adx_threshold}."
            else:
                reason = f"ADX {adx_val:.1f} acima de {self.adx_threshold} mas sem aceleração."
            return StrategyResult(
                signal=Signal.HOLD, hold_reason=reason, indicators=inds,
                criteria_met=0, criteria_total=4,
            )

        # C2 — Donchian break
        c2_ok = donchian_break != 0
        if not c2_ok:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=(
                    f"ADX OK ({adx_val:.1f}) — aguardando rompimento do Canal "
                    f"de Donchian 52W (upper {don_upper:.2f} / lower {don_lower:.2f})."
                ),
                indicators=inds,
                criteria_met=1, criteria_total=4,
            )

        # Direção determinada pelo Donchian break
        bias = "BUY" if donchian_break == 1 else "SELL"

        # C3 — Recuo à EMA 21
        c3_ok = bool(touched_ema21)
        if not c3_ok:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=(
                    f"Setup {bias} ativo (ADX {adx_val:.1f}, Donchian {'máxima' if bias=='BUY' else 'mínima'} 52W) "
                    f"— aguardando recuo à EMA 21 ({dist_ema21_pct:+.2f}% de distância)."
                ),
                indicators=inds,
                criteria_met=2, criteria_total=4,
            )

        # C4 — Gatilho de entrada
        trigger_type = 0
        if bias == "BUY":
            if prev_week_high > 0 and close > prev_week_high:
                trigger_type = 1  # rompeu máxima da semana anterior
            elif ema8_turned_up:
                trigger_type = 2  # EMA 8 virou para cima
        else:  # SELL
            if prev_week_low > 0 and close < prev_week_low:
                trigger_type = 1  # rompeu mínima da semana anterior
            elif ema8_turned_down:
                trigger_type = 2  # EMA 8 virou para baixo

        inds["trigger_type"] = float(trigger_type)

        c4_ok = trigger_type > 0
        if not c4_ok:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=(
                    f"Setup {bias} completo (ADX + Donchian + EMA 21 tocada) "
                    f"— aguardando gatilho: romper {'máxima' if bias=='BUY' else 'mínima'} "
                    f"semanal anterior ({prev_week_high if bias=='BUY' else prev_week_low:.4f}) "
                    f"ou EMA 8 virar na direção."
                ),
                indicators=inds,
                criteria_met=3, criteria_total=4,
            )

        # ── Sinal confirmado: calcular SL e TP ───────────────────────────────
        amplitude = high_d - low_d
        if amplitude <= 0:
            amplitude = atr_val * 0.5  # fallback para velas sem amplitude

        if bias == "BUY":
            sl_price  = round(low_d, 8)
            tp1_price = round(close + float(self.tp_multiplier) * amplitude, 8)
            signal    = Signal.BUY
        else:
            sl_price  = round(high_d, 8)
            tp1_price = round(close - float(self.tp_multiplier) * amplitude, 8)
            signal    = Signal.SELL

        trigger_label = "rompimento da máxima semanal" if trigger_type == 1 else "virada da EMA 8"
        return StrategyResult(
            signal=signal,
            hold_reason="",
            indicators=inds,
            metadata={
                "sl_price":  sl_price,
                "tp1_price": tp1_price,
                "trigger":   trigger_label,
            },
            criteria_met=4, criteria_total=4,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _empty_indicators(self) -> dict[str, Any]:
        return {
            "close": 0.0, "high": 0.0, "low": 0.0, "atr": 0.0,
            "ema8": 0.0, "ema21": 0.0, "ema8_slope": 0.0,
            "touched_ema21": 0.0, "dist_ema21_pct": 0.0,
            "adx_w": 0.0, "adx_rising": 0.0,
            "donchian_upper": 0.0, "donchian_lower": 0.0,
            "donchian_break": 0.0,
            "prev_week_high": 0.0, "prev_week_low": 0.0,
            "trigger_type": 0.0,
        }

    def _build_indicators(
        self,
        ema8: float, ema21: float, atr: float, ema8_slope: float,
        touched_ema21: int, dist_ema21_pct: float,
        adx_w: float = 0.0, adx_rising: int = 0,
        donchian_upper: float = 0.0, donchian_lower: float = 0.0,
        donchian_break: int = 0,
        prev_week_high: float = 0.0, prev_week_low: float = 0.0,
        trigger_type: int = 0,
    ) -> dict[str, Any]:
        return {
            "ema8":            round(ema8,  6),
            "ema_fast":        round(ema8,  6),   # alias para compatibilidade UI
            "ema21":           round(ema21, 6),
            "ema_slow":        round(ema21, 6),   # alias para compatibilidade UI
            "atr":             round(atr,   6),
            "ema8_slope":      round(ema8_slope, 6),
            "touched_ema21":   float(touched_ema21),
            "dist_ema21_pct":  round(dist_ema21_pct, 4),
            "adx_w":           round(adx_w, 2),
            "adx_rising":      float(adx_rising),
            "donchian_upper":  round(donchian_upper, 6),
            "donchian_lower":  round(donchian_lower, 6),
            "donchian_break":  float(donchian_break),
            "prev_week_high":  round(prev_week_high, 6),
            "prev_week_low":   round(prev_week_low,  6),
            "trigger_type":    float(trigger_type),
        }
