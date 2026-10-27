"""
Estratégia: TF016 - Miracle Trend Continuation
Gerado pela Fábrica de Estratégias; revisada na auditoria das TF (spot long-only):
  - descrição alinhada ao código (viés = EMA rápida/lenta do PRÓPRIO timeframe, não há TF extra);
  - C3 "dominância" exige volume ACIMA da média (antes 65% da média: volume abaixo da média passava);
  - risco/retorno real: o TP1 é calculado sobre a distância efetiva até o stop (antes sobre 2×ATR, que não
    era o stop usado);
  - SELL = sair da posição (viés perdido), não o setup espelhado de short, que não existe em spot.
"""

from __future__ import annotations
from typing import Optional
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class MiracleTrendContinuationStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF016",
            name="TF016 - Miracle Trend Continuation",
            description=(
                "Continuação de tendência: compra o pullback até a EMA lenta quando a EMA rápida está acima dela, "
                "com volume acima da média e candle de rejeição (pavio inferior). Stop logo abaixo do pavio "
                "(nunca mais curto que sl_mult×ATR) e TP1 pelo risco/retorno real sobre esse stop. "
                "Sai (SELL) quando o viés se perde: EMA rápida abaixo da lenta e preço abaixo da zona."
            ),
            tags=["trend", "pullback", "volume"],
            recommended_timeframe="1h",
            criteria=[
                {"id": "c1_bias", "label": "C1 Viés", "description": "EMA rápida acima da EMA lenta (tendência de alta no timeframe do bot)"},
                {"id": "c2_zone", "label": "C2 Zona de Interesse", "description": "Preço toca a vizinhança da EMA lenta (±0,25 ATR)"},
                {"id": "c3_dominance", "label": "C3 Dominância", "description": "Volume do candle acima de dominance_threshold% da média de 20 candles"},
                {"id": "c4_rejection", "label": "C4 Gatilho Rejeição", "description": "Pavio inferior > 40% do range e candle de alta"},
            ],
            params={
                "atr_period": ParamDef(type="int", default=14, min=5, max=50, step=1, description="Período do ATR para gestão de risco e volatilidade"),
                "sl_mult": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Distância MÍNIMA do stop ao preço de entrada (× ATR)"),
                "tp1_rr": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Relação risco-retorno do TP1 sobre a distância efetiva do stop"),
                "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Trailing Stop após TP1"),
                "dominance_threshold": ParamDef(type="float", default=120.0, min=50.0, max=300.0, step=10.0, description="Volume mínimo do candle em % da média de 20 candles (>100 = acima da média)"),
                "ema_fast": ParamDef(type="int", default=9, min=5, max=20, step=1, description="Período da EMA rápida"),
                "ema_slow": ParamDef(type="int", default=21, min=10, max=50, step=1, description="Período da EMA lenta (zona de interesse)"),
                "exit_on_bias_loss": ParamDef(type="int", default=1, min=0, max=1, step=1, description="SELL (saída) quando o viés se perde (1=sim)"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.atr_period = p["atr_period"].default
        self.sl_mult = p["sl_mult"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default
        self.dominance_threshold = p["dominance_threshold"].default
        self.ema_fast = p["ema_fast"].default
        self.ema_slow = p["ema_slow"].default
        self.exit_on_bias_loss = p["exit_on_bias_loss"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles, context=None):
        min_candles = max(self.ema_slow + 20, 100)
        if len(candles) < min_candles:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_candles} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "ema_fast": 0.0, "ema_slow": 0.0, "atr": 0.0, "high": 0.0, "low": 0.0, "volume": 0.0},
                criteria_total=4,
            )

        df = pd.DataFrame([{"open": float(c.open), "high": float(c.high), "low": float(c.low), "close": float(c.close), "volume": float(c.volume)} for c in candles])

        ema_fast_s = ta.ema(df["close"], length=self.ema_fast)
        ema_slow_s = ta.ema(df["close"], length=self.ema_slow)
        atr_s = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)

        if ema_fast_s is None or ema_slow_s is None or atr_s is None:
            return None

        ema_fast_val = float(ema_fast_s.iloc[-1])
        ema_slow_val = float(ema_slow_s.iloc[-1])
        atr = float(atr_s.iloc[-1])

        open_p = df["open"].iloc[-1]
        close = df["close"].iloc[-1]
        high = df["high"].iloc[-1]
        low = df["low"].iloc[-1]
        volume = df["volume"].iloc[-1]

        avg_volume = float(df["volume"].rolling(window=20).mean().iloc[-1]) if len(df) >= 20 else float(df["volume"].mean())
        volume_ratio = volume / avg_volume if avg_volume > 0 else 1.0

        zone_tolerance = atr * 0.25
        candle_range = high - low if (high - low) > 0 else 0.0001
        lower_shadow = min(close, open_p) - low

        c1 = ema_fast_val > ema_slow_val
        c2 = low <= (ema_slow_val + zone_tolerance) and close >= (ema_slow_val - zone_tolerance)
        c3 = volume_ratio >= (self.dominance_threshold / 100.0) and close > open_p
        c4 = lower_shadow > (candle_range * 0.4) and close >= open_p

        bias_lost = ema_fast_val < ema_slow_val and close < (ema_slow_val - zone_tolerance)

        if c1 and c2 and c3 and c4:
            signal = Signal.BUY
            criteria_met = 4
            hold_reason = ""
        elif self.exit_on_bias_loss and bias_lost:
            # Contrato spot long-only: SELL = sair da posição. Flat: o bot ignora.
            signal = Signal.SELL
            criteria_met = 0
            hold_reason = "Saída: viés perdido (EMA rápida < lenta e preço abaixo da zona)"
        else:
            signal = Signal.HOLD
            criteria_met = int(c1) + int(c2) + int(c3) + int(c4)
            reasons = []
            if not c1:
                reasons.append("Viés desalinhado")
            if not c2:
                reasons.append("Fora da POI")
            if not c3:
                reasons.append("Volume insuficiente")
            if not c4:
                reasons.append("Sem gatilho de rejeição")
            hold_reason = " | ".join(reasons) if reasons else "Condições não satisfeitas"

        sl_price = tp1_price = 0.0
        risk = 0.0
        if signal == Signal.BUY:
            # Stop abaixo do pavio de rejeição, nunca mais curto que sl_mult × ATR; o TP1 usa o risco REAL.
            risk = max(close - (low - atr * 0.5), atr * self.sl_mult)
            sl_price = round(close - risk, 4)
            tp1_price = round(close + risk * self.tp1_rr, 4)

        return StrategyResult(
            signal=signal,
            hold_reason=hold_reason,
            indicators={
                "close": round(close, 4),
                "ema_fast": round(ema_fast_val, 4),
                "ema_slow": round(ema_slow_val, 4),
                "atr": round(atr, 6),
                "high": round(high, 4),
                "low": round(low, 4),
                "volume": round(volume, 2),
                "volume_ratio": round(volume_ratio, 2),
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(risk / close, 5) if close > 0 and risk > 0 else 0.0,
                "tp1_pct": round(risk * self.tp1_rr / close, 5) if close > 0 and risk > 0 else 0.0,
                "ts_pct": round((atr * self.ts_mult) / close, 5) if close > 0 else 0.0,
            },
            criteria_met=criteria_met,
            criteria_total=4,
        )
