"""
strategies/multi_tf.py — Multi-Timeframe RSI/MACD

Design
──────
Usa três camadas temporais independentes para filtrar entradas de baixa
qualidade — o problema mais comum em estratégias de timeframe único.

  4H  (bias)      — EMA longa define a direção macro do mercado.
                    Só entramos a favor dessa tendência de longo prazo.

  1H  (tendência) — EMA 21/55 + RSI confirmam que a tendência de médio
                    prazo está alinhada com o bias de 4H.

  15m (gatilho)   — Cruzamento do histograma MACD de negativo → positivo
                    (BUY) ou positivo → negativo (SELL) dispara a entrada.

O bot deve ser configurado no timeframe do gatilho (recomendado: 15m ou 5m).
Os timeframes 1H e 4H são buscados automaticamente a cada candle fechado.

Regras de entrada
─────────────────
  BUY  quando: bias 4H = alta AND EMA21 > EMA55 (1H) AND RSI(1H) > 50
               AND histograma MACD (15m) cruza de negativo para positivo

  SELL quando: bias 4H = baixa AND EMA21 < EMA55 (1H) AND RSI(1H) < 50
               AND histograma MACD (15m) cruza de positivo para negativo
"""

from __future__ import annotations
from typing import Optional

import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class MultiTFStrategy(BaseStrategy):

    EXTRA_TIMEFRAMES = ['1H', '4H']

    @classmethod
    def extra_timeframes(cls) -> list[str]:
        return cls.EXTRA_TIMEFRAMES

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF007",
            name="TF007 - Multi-Timeframe Trend",
            description = (
                "Estratégia de três camadas temporais: "
                "(1) Bias de 4H via slope da EMA longa — define a direção macro; "
                "(2) EMA 21/55 + RSI no 1H confirmam a tendência de médio prazo; "
                "(3) Cruzamento do histograma MACD no timeframe do bot (recomendado 15m) "
                "dispara a entrada. Só opera quando as três camadas estão alinhadas. "
                "Configure o bot em 15m ou 5m para o gatilho; os contextos 1H e 4H "
                "são buscados automaticamente."
            ),
            tags   = ["trend", "momentum", "multi-tf"],
            recommended_timeframe = "15m",
            params = {
                "bias_ema": ParamDef(
                    type="int", default=20, min=10, max=50, step=5,
                    description="Período da EMA no 4H para medir o bias macro"),
                "rsi_period": ParamDef(
                    type="int", default=14, min=5, max=30, step=1,
                    description="Período do RSI no 1H para confirmação de momentum"),
                "rsi_threshold": ParamDef(
                    type="float", default=50.0, min=45.0, max=55.0, step=1.0,
                    description="RSI mínimo (BUY) / máximo (SELL) no 1H"),
                "rsi_buffer": ParamDef(
                    type="float", default=2.0, min=0.0, max=10.0, step=0.5,
                    description="Margem de tolerância para RSI no 1H para evitar bloqueio por ruído"),
                "macd_fast": ParamDef(
                    type="int", default=12, min=5, max=20, step=1,
                    description="EMA rápida do MACD (timeframe do bot)"),
                "macd_slow": ParamDef(
                    type="int", default=26, min=15, max=50, step=1,
                    description="EMA lenta do MACD (timeframe do bot)"),
                "macd_signal": ParamDef(
                    type="int", default=9, min=3, max=15, step=1,
                    description="Linha de sinal do MACD"),
                "atr_period": ParamDef(
                    type="int", default=14, min=5, max=30, step=1,
                    description="Período do ATR para gestão de risco (timeframe do bot)"),
                "sl_mult": ParamDef(
                    type="float", default=2.5, min=0.5, max=4.0, step=0.1,
                    description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(
                    type="float", default=2.0, min=1.0, max=5.0, step=0.5,
                    description="Risk:Reward do TP1"),
                "ts_mult": ParamDef(
                    type="float", default=3.0, min=1.5, max=6.0, step=0.5,
                    description="Multiplicador ATR para Trailing Stop (após TP1)"),
                "allow_continuation": ParamDef(
                    type="int", default=1, min=0, max=1, step=1,
                    description="Permite entrada por continuidade quando multi-TF está alinhado, sem exigir crossover exato"),
            },
            criteria=[
                {"id": "c1", "label": "Bias 4H", "description": "Tendência macro definida no 4H"},
                {"id": "c2", "label": "Tendência 1H", "description": "EMA + RSI confirmam no 1H"},
                {"id": "c3", "label": "Gatilho MACD", "description": "Cruzamento do histograma MACD"},
            ],
        )

    def __init__(self):
        p = self.info().params
        self.bias_ema      = p["bias_ema"].default
        self.rsi_period    = p["rsi_period"].default
        self.rsi_threshold = p["rsi_threshold"].default
        self.rsi_buffer    = p["rsi_buffer"].default
        self.macd_fast     = p["macd_fast"].default
        self.macd_slow     = p["macd_slow"].default
        self.macd_signal   = p["macd_signal"].default
        self.atr_period    = p["atr_period"].default
        self.sl_mult       = p["sl_mult"].default
        self.tp1_rr        = p["tp1_rr"].default
        self.ts_mult       = p["ts_mult"].default
        self.allow_continuation = p["allow_continuation"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    # compute() sem contexto — retorna None até os dados extras chegarem
    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(
        self,
        candles: list,
        context: dict | None = None,
    ) -> Optional[StrategyResult]:
        extra        = (context or {}).get('extra_candles', {})
        candles_1h   = extra.get('1H', [])
        candles_4h   = extra.get('4H', [])

        min_main = self.macd_slow + self.macd_signal + 5
        min_1h   = 55 + 5          # EMA 55
        min_4h   = self.bias_ema + 5

        if len(candles) < min_main:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_main} velas ({len(candles)} recebidas)",
                indicators={"close": round(candles[-1].close, 4), "atr": 0.0,
                            "bias_4h": 0.0, "trend_1h": 0.0, "macd_hist": 0.0,
                            "data_4h_ok": 0.0, "data_1h_ok": 0.0},
            )

        df = pd.DataFrame([
            {"high": c.high, "low": c.low, "close": c.close}
            for c in candles
        ])

        # ── MACD no timeframe do bot (gatilho) ────────────────────────────────
        macd_out = ta.macd(df["close"],
                           fast=self.macd_fast,
                           slow=self.macd_slow,
                           signal=self.macd_signal)
        if macd_out is None:
            return None

        hist_col  = f"MACDh_{self.macd_fast}_{self.macd_slow}_{self.macd_signal}"
        macd_col  = f"MACD_{self.macd_fast}_{self.macd_slow}_{self.macd_signal}"
        sig_col   = f"MACDs_{self.macd_fast}_{self.macd_slow}_{self.macd_signal}"

        hist_now  = float(macd_out[hist_col].iloc[-1])
        hist_prev = float(macd_out[hist_col].iloc[-2])
        hist_prev2 = float(macd_out[hist_col].iloc[-3]) if len(macd_out[hist_col]) >= 3 else hist_prev
        macd_val  = float(macd_out[macd_col].iloc[-1])
        sig_val   = float(macd_out[sig_col].iloc[-1])

        trigger_bull = hist_prev < 0 and hist_now >= 0
        trigger_bear = hist_prev > 0 and hist_now <= 0
        continuation_bull = hist_now > 0 and hist_prev > 0 and hist_now >= hist_prev >= hist_prev2
        continuation_bear = hist_now < 0 and hist_prev < 0 and hist_now <= hist_prev <= hist_prev2

        # ── ATR ───────────────────────────────────────────────────────────────
        atr_series = ta.atr(df["high"], df["low"], df["close"],
                            length=self.atr_period)
        if atr_series is None:
            return None
        atr_val = float(atr_series.iloc[-1])
        close   = candles[-1].close

        # ── Bias 4H ───────────────────────────────────────────────────────────
        data_4h_ok = len(candles_4h) >= min_4h
        bias        = 0
        ema_4h_now  = None
        ema_4h_slope = 0.0

        if data_4h_ok:
            df4 = pd.DataFrame([{"close": c.close} for c in candles_4h])
            ema4 = ta.ema(df4["close"], length=self.bias_ema)
            if ema4 is not None and not ema4.isna().all():
                ema_4h_now   = float(ema4.iloc[-1])
                # Slope calculado sobre 3 barras de 4H ≈ 12h de movimento
                slope_window = min(4, len(ema4.dropna()))
                ema_4h_slope = float(ema4.iloc[-1]) - float(ema4.iloc[-slope_window])
                bias = 1 if ema_4h_slope > 0 else -1 if ema_4h_slope < 0 else 0

        # ── Tendência 1H ──────────────────────────────────────────────────────
        data_1h_ok  = len(candles_1h) >= min_1h
        trend       = 0
        ef1h = el1h = rsi_1h_val = None

        if data_1h_ok:
            df1 = pd.DataFrame([{"close": c.close} for c in candles_1h])
            ema_f1 = ta.ema(df1["close"], length=21)
            ema_l1 = ta.ema(df1["close"], length=55)
            rsi_1h = ta.rsi(df1["close"], length=self.rsi_period)

            if (ema_f1 is not None and ema_l1 is not None
                    and rsi_1h is not None
                    and not ema_f1.isna().all()):
                ef1h      = float(ema_f1.iloc[-1])
                el1h      = float(ema_l1.iloc[-1])
                rsi_1h_val = float(rsi_1h.iloc[-1])
                buy_rsi = float(self.rsi_threshold) - float(self.rsi_buffer)
                sell_rsi = float(self.rsi_threshold) + float(self.rsi_buffer)
                if ef1h > el1h and rsi_1h_val > buy_rsi:
                    trend = 1
                elif ef1h < el1h and rsi_1h_val < sell_rsi:
                    trend = -1

        # ── Decisão final ─────────────────────────────────────────────────────
        hold_reason = ""
        bull_trigger_ok = trigger_bull or (bool(int(self.allow_continuation)) and continuation_bull)
        bear_trigger_ok = trigger_bear or (bool(int(self.allow_continuation)) and continuation_bear)

        if bias == 1 and trend == 1 and bull_trigger_ok:
            signal = Signal.BUY
        elif bias == -1 and trend == -1 and bear_trigger_ok:
            signal = Signal.SELL
        else:
            signal = Signal.HOLD
            if not data_4h_ok or not data_1h_ok:
                hold_reason = "Aguardando dados dos timeframes superiores"
            elif bias == 0:
                hold_reason = "Bias 4H neutro — sem direção macro definida"
            elif trend == 0:
                hold_reason = "Tendência 1H não confirmada (EMA ou RSI divergem)"
            elif bias != trend:
                hold_reason = f"Conflito de tendência: bias 4H {'alta' if bias==1 else 'baixa'} vs trend 1H {'alta' if trend==1 else 'baixa'}"
            elif not (bull_trigger_ok or bear_trigger_ok):
                hold_reason = (
                    f"Aguardando cruzamento MACD — hist: {hist_now:+.4f}"
                )

        # ── Preços de risco ───────────────────────────────────────────────────
        sl_dist = atr_val * self.sl_mult
        if signal == Signal.BUY:
            sl_price  = round(close - sl_dist, 4)
            tp1_price = round(close + sl_dist * self.tp1_rr, 4)
        else:
            sl_price  = round(close + sl_dist, 4)
            tp1_price = round(close - sl_dist * self.tp1_rr, 4)

        criteria_met = 3 if signal in (Signal.BUY, Signal.SELL) else 0
        
        return StrategyResult(
            signal      = signal,
            hold_reason = hold_reason,
            indicators  = {
                "close":        round(close, 4),
                "atr":          round(atr_val, 6),
                # 4H
                "bias_4h":      float(bias),
                "ema_4h_slope": round(ema_4h_slope, 4),
                "data_4h_ok":   float(data_4h_ok),
                # 1H
                "trend_1h":     float(trend),
                "rsi_1h":       round(rsi_1h_val, 2) if rsi_1h_val is not None else 0.0,
                "ema_fast_1h":  round(ef1h, 4) if ef1h is not None else 0.0,
                "ema_slow_1h":  round(el1h, 4) if el1h is not None else 0.0,
                "data_1h_ok":   float(data_1h_ok),
                # Gatilho
                "macd_hist":    round(hist_now, 6),
                "macd_val":     round(macd_val, 6),
                "signal_line":  round(sig_val, 6),
                "trigger_bull": float(trigger_bull),
                "trigger_bear": float(trigger_bear),
                "continuation_bull": float(continuation_bull),
                "continuation_bear": float(continuation_bear),
            },
            metadata = {
                "sl_price":  sl_price,
                "tp1_price": tp1_price,
                "sl_pct":    round(sl_dist / close, 5),
                "tp1_pct":   round(sl_dist * self.tp1_rr / close, 5),
                "ts_pct":    round(atr_val * self.ts_mult / close, 5),
                "diag": {
                    "bias":          bias,
                    "trend":         trend,
                    "trigger_bull":  trigger_bull,
                    "trigger_bear":  trigger_bear,
                    "continuation_bull": continuation_bull,
                    "continuation_bear": continuation_bear,
                    "data_4h_ok":    data_4h_ok,
                    "data_1h_ok":    data_1h_ok,
                },
            },
            criteria_met=criteria_met,
            criteria_total=3,
        )
