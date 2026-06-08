"""
strategies/three_line_bar.py — S005 Three Line Bar (Fabrício Lorenz)

Filosofia: Identificar o fundo de um movimento de queda o mais rápido possível
para entrar no início de uma nova tendência de alta, usando APENAS 3 candles
consecutivos e suas máximas/mínimas.

Lógica:
  1. Detectar um movimento de queda recente (N candles com lows descendentes).
  2. Formação do padrão Three Line Bar:
       Candle 1: Mínima mais baixa do movimento (ponto de exaustão).
       Candle 2: Primeiro candle após C1 com máxima > máxima de C1.
       Candle 3: Máxima > máxima de C2 (confirmação do padrão).
  3. Gatilho: Compra 1 tick acima da máxima de C3.
  4. Stop Loss: 1 tick abaixo da mínima de C1.
  5. Alvo determinado pelo contexto:
       - A favor da tendência (EMA200): TP = 1x risco (RR 1:1)
       - Reversão de tendência: TP = 2x a 3x risco (RR configurável)
  6. Filtro adicional: IFR < 30 opcional para reversões.
"""

from __future__ import annotations
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class ThreeLineBarStrategy(BaseStrategy):

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="PA004",
            name="PA004 - Three Line Bar",
            description = (
                "Estratégia de reversão baseada exclusivamente em price action "
                "de 3 candles (Fabrício Lorenz). Detecta o fundo de um movimento "
                "de queda através da sequência: C1 (mínima mais baixa), "
                "C2 (máxima > C1), C3 (máxima > C2). Entrada na quebra da máxima "
                "de C3 com stop abaixo de C1. Alvo adaptativo conforme contexto: "
                "1:1 se a favor da tendência principal (EMA200), ou 2:1 a 3:1 "
                "para reversão de tendência. Filtro opcional de IFR < 30 para "
                "reversões. Ideal para timeframes de 15m a 1h."
            ),
            tags        = ["price_action", "reversal", "structure", "three_line_bar", "fabri"],
            recommended_timeframe = "15m",
            criteria=[
                {"id": "c1_3lb_pattern", "label": "C1 3LB Pattern", "description": "Estrutura Three Line Bar válida."},
                {"id": "c2_ema200", "label": "C2 EMA200", "description": "Contexto de tendência pela EMA200."},
                {"id": "c3_rsi", "label": "C3 RSI", "description": "Filtro de IFR/sobrevenda para reversão."},
                {"id": "c4_volume", "label": "C4 Volume", "description": "Volume valida interesse no setup."},
                {"id": "c5_risk", "label": "C5 Risco", "description": "Risco e alvo calculados de forma válida."},
            ],
            params = {
                "lookback_highs": ParamDef(
                    type="int", default=1, min=1, max=3, step=1,
                    description="Nº de candles anteriores a C1 para verificar se o preço veio caindo"),

                "ema_trend": ParamDef(
                    type="int", default=200, min=50, max=400, step=10,
                    description="EMA longa para detectar tendência principal"),

                "rr_trend": ParamDef(
                    type="float", default=1.0, min=0.5, max=3.0, step=0.1,
                    description="Risk:Reward quando a favor da tendência (1:1 recomendado)"),

                "rr_reversal": ParamDef(
                    type="float", default=2.0, min=1.5, max=5.0, step=0.5,
                    description="Risk:Reward para reversão de tendência (2:1 a 3:1)"),

                "use_rsi_filter": ParamDef(
                    type="int", default=1, min=0, max=1, step=1,
                    description="Ativar filtro RSI (< 30 para reversão)"),

                "rsi_period": ParamDef(
                    type="int", default=14, min=5, max=30, step=1,
                    description="Período do RSI"),

                "rsi_threshold": ParamDef(
                    type="int", default=30, min=10, max=40, step=5,
                    description="Limiar RSI para condição de sobrevenda"),

                "min_volume_ratio": ParamDef(
                    type="float", default=0.0, min=0.0, max=5.0, step=0.1,
                    description="Volume mínimo relativo à média (0 = desligado)"),

                "volume_period": ParamDef(
                    type="int", default=20, min=5, max=50, step=1,
                    description="Período da média de volume"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.lookback_highs    = p["lookback_highs"].default
        self.ema_trend         = p["ema_trend"].default
        self.rr_trend          = p["rr_trend"].default
        self.rr_reversal       = p["rr_reversal"].default
        self.use_rsi_filter    = p["use_rsi_filter"].default
        self.rsi_period        = p["rsi_period"].default
        self.rsi_threshold     = p["rsi_threshold"].default
        self.min_volume_ratio  = p["min_volume_ratio"].default
        self.volume_period     = p["volume_period"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        # Dados mínimos: EMA + RSI + volume + espaço para o padrão
        min_len = max(self.ema_trend, self.rsi_period, self.volume_period) + 10
        if len(candles) < min_len:
            return None

        df = pd.DataFrame([
            {"open": c.open, "high": c.high, "low": c.low,
             "close": c.close, "volume": c.volume}
            for c in candles
        ])

        # ── Indicadores ──────────────────────────────────────────────────────
        ema_trend_s = ta.ema(df["close"], length=self.ema_trend)
        rsi_s       = ta.rsi(df["close"], length=self.rsi_period)
        vol_ma_s    = ta.sma(df["volume"], length=self.volume_period)

        if ema_trend_s is None or rsi_s is None:
            return None

        ema_trend_val = float(ema_trend_s.iloc[-1])
        rsi_val       = float(rsi_s.iloc[-1]) if not pd.isna(rsi_s.iloc[-1]) else 50.0
        vol_ma_val    = float(vol_ma_s.iloc[-1]) if vol_ma_s is not None and not pd.isna(vol_ma_s.iloc[-1]) else 0.0

        # ── 1. Verificar movimento de queda recente ──────────────────────────
        n = len(candles)
        if n < 6 + self.lookback_highs:
            return None

        # C3 = último candle, C2 = penúltimo, C1 = antepenúltimo
        c3 = candles[-1]
        c2 = candles[-2]
        c1 = candles[-3]

        # C1 deve ter mínima mais baixa que os lookback_highs candles anteriores
        lookback_start = max(0, n - 3 - self.lookback_highs)
        lookback_candles = candles[lookback_start:-3]

        # Verifica se houve movimento de queda (lows decrescentes ou C1 é o mais baixo)
        c1_low = c1.low
        is_declining = all(c.low >= c1_low for c in lookback_candles) if lookback_candles else True

        # C1 deve ter a mínima mais baixa entre os últimos candles (incluindo C2 e C3)
        recent_lows = [c.low for c in candles[-(3 + self.lookback_highs):]]
        c1_is_lowest = c1_low <= min(recent_lows)

        if not (is_declining and c1_is_lowest):
            hold_reason = f"C1 ({c1_low:.2f}) nao e a minima mais baixa do movimento recente"
            return StrategyResult(
                signal=Signal.HOLD,
                criteria_met=0,
                criteria_total=5,
                hold_reason=hold_reason,
                indicators=self._calc_indicators(df, c3, ema_trend_val, rsi_val),
            )

        # ── 2. Verificar estrutura Three Line Bar ────────────────────────────
        # C2: máxima > máxima de C1
        c2_high = c2.high
        c1_high = c1.high

        if c2_high <= c1_high:
            return StrategyResult(
                signal=Signal.HOLD,
                criteria_met=0,
                criteria_total=5,
                hold_reason=f"C2 maxima ({c2_high:.2f}) <= C1 maxima ({c1_high:.2f}) — aguardando C2",
                indicators=self._calc_indicators(df, c3, ema_trend_val, rsi_val, extra={
                    "c1_low": round(c1_low, 2),
                }),
            )

        # C3: máxima > máxima de C2
        c3_high = c3.high

        if c3_high <= c2_high:
            return StrategyResult(
                signal=Signal.HOLD,
                criteria_met=0,
                criteria_total=5,
                hold_reason=f"C3 maxima ({c3_high:.2f}) <= C2 maxima ({c2_high:.2f}) — aguardando C3",
                indicators=self._calc_indicators(df, c3, ema_trend_val, rsi_val, extra={
                    "c1_low":  round(c1_low, 2),
                    "c2_high": round(c2_high, 2),
                }),
            )

        # ── 3. Verificar C3 fechou acima da abertura (candle de alta) ────────
        c3_bullish = c3.close > c3.open

        # ── 4. Filtro RSI (opcional) ─────────────────────────────────────────
        rsi_ok = True
        if self.use_rsi_filter:
            rsi_ok = rsi_val < self.rsi_threshold

        # ── 5. Filtro de Volume (opcional) ───────────────────────────────────
        vol_ok = True
        if self.min_volume_ratio > 0 and vol_ma_val > 0:
            vol_ratio = c3.volume / vol_ma_val
            vol_ok = vol_ratio >= self.min_volume_ratio
        else:
            vol_ratio = 0.0

        # ── 6. Determinar contexto (tendência principal vs reversão) ─────────
        close = c3.close
        in_uptrend = close > ema_trend_val  # acima da EMA200 = a favor da tendência

        # ── 7. Gatilho de entrada ────────────────────────────────────────────
        # Entrada: 1 tick acima da máxima de C3
        entry_price = c3_high * 1.0001  # 1 tick acima

        # Stop Loss: 1 tick abaixo da mínima de C1
        sl_price = c1_low * 0.9999

        risk = entry_price - sl_price
        if risk <= 0:
            return StrategyResult(
                signal=Signal.HOLD,
                criteria_met=0,
                criteria_total=5,
                hold_reason="Risco invalido (SL acima da entrada)",
                indicators=self._calc_indicators(df, c3, ema_trend_val, rsi_val, extra={
                    "c1_low":  round(c1_low, 2),
                    "c2_high": round(c2_high, 2),
                    "c3_high": round(c3_high, 2),
                }),
            )

        # ── 8. Alvo adaptativo ───────────────────────────────────────────────
        if in_uptrend:
            rr = self.rr_trend
            context = "trend_follow"  # a favor da tendência (pullback)
        else:
            rr = self.rr_reversal
            context = "reversal"      # capturando reversão

        tp_price = entry_price + (risk * rr)

        # ── 9. Decisão final ─────────────────────────────────────────────────
        if not rsi_ok:
            return StrategyResult(
                signal=Signal.HOLD,
                criteria_met=0,
                criteria_total=5,
                hold_reason=f"RSI {rsi_val:.0f} >= {self.rsi_threshold} (sobrevenda nao confirmada)",
                indicators=self._calc_indicators(
                    df, c3, ema_trend_val, rsi_val,
                    extra={
                        "c3_high": round(c3_high, 2),
                        "c1_low": round(c1_low, 2),
                        "c2_high": round(c2_high, 2),
                        "context": context,
                        "c3_bullish": 1 if c3_bullish else 0,
                        "vol_ratio": round(vol_ratio, 2),
                        "entry_price": round(entry_price, 2),
                        "rr": rr,
                        "in_uptrend": 1 if in_uptrend else 0,
                    }
                ),
            )

        return StrategyResult(
            signal=Signal.BUY,
            criteria_met=5,
            criteria_total=5,
            hold_reason="",
            indicators=self._calc_indicators(
                df, c3, ema_trend_val, rsi_val,
                extra={
                    "c3_high": round(c3_high, 2),
                    "c1_low": round(c1_low, 2),
                    "c2_high": round(c2_high, 2),
                    "context": context,
                    "c3_bullish": 1 if c3_bullish else 0,
                    "vol_ratio": round(vol_ratio, 2),
                    "entry_price": round(entry_price, 2),
                    "rr": rr,
                    "in_uptrend": 1 if in_uptrend else 0,
                }
            ),
            metadata={
                "sl_price": round(sl_price, 2),
                "tp1_price": round(tp_price, 2),
                "rr": rr,
                "context": context,
                "regime_state": f"three_line_bar_{context}",
                "pattern": {
                    "c1_low": round(c1_low, 2),
                    "c2_high": round(c2_high, 2),
                    "c3_high": round(c3_high, 2),
                    "entry_trigger": round(entry_price, 2),
                },
            },
        )

    def _calc_indicators(self, df, last_candle, ema_trend_val, rsi_val, extra=None) -> dict:
        ind = {
            "close": round(last_candle.close, 2),
            "ema_trend": round(ema_trend_val, 2),
            "rsi": round(rsi_val, 1),
            "in_uptrend": 1 if last_candle.close > ema_trend_val else 0,
        }
        if extra:
            ind.update(extra)
        return ind
