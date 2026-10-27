"""PA011 - Liquidez AMD 1H
Estratégia de Price Action baseada no ciclo Acumulação-Manipulação-Distribuição (AMD) no gráfico de 1 hora.
Gerado pela Fábrica de Estratégias
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class LiquidezAmd1hStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="PA011",
            name="PA011 - Liquidez AMD 1H",
            description="Estratégia de Price Action baseada no ciclo Acumulação-Manipulação-Distribuição (AMD) no gráfico de 1 hora. Busca capturar reversões após manipulação de liquidez nos máximos/mínimos do dia anterior, com entrada no fechamento da vela manipuladora e alvo 1:2.",
            tags=["pattern", "reversal", "swing"],
            recommended_timeframe="1h",
            criteria=[
                {"id": "c1_accumulation", "label": "C1 Acumulação", "description": "Preço em consolidação lateral nas últimas 3-5 velas, próximo ao máximo ou mínimo do dia anterior"},
                {"id": "c2_manipulation", "label": "C2 Manipulação", "description": "Vela atual rompeu a máxima/mínima do dia anterior com pavio, mas fechou dentro do intervalo (rejeição)"},
                {"id": "c3_rejection", "label": "C3 Rejeição", "description": "Corpo da vela fechou no lado oposto ao rompimento (ex: pavio abaixo da mínima, corpo acima)"},
            ],
            params={
                "atr_period": ParamDef(type="int", default=14, min=5, max=50, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Trailing Stop após TP1"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.atr_period = p["atr_period"].default
        self.sl_mult = p["sl_mult"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles, context=None) -> Optional[StrategyResult]:
        min_candles = 48
        if len(candles) < min_candles:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_candles} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "high": float(candles[-1].high), "low": float(candles[-1].low), "atr": 0.0, "prev_day_high": 0.0, "prev_day_low": 0.0},
                metadata={"sl_price": 0.0, "tp1_price": 0.0, "sl_pct": 0.0, "tp1_pct": 0.0, "ts_pct": 0.0},
                criteria_met=0,
                criteria_total=3,
            )

        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close, "volume": c.volume, "time": c.time} for c in candles])

        # Calcular ATR
        atr_series = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        if atr_series is None or atr_series.isna().all():
            return None
        atr = float(atr_series.iloc[-1])

        # Determinar dia anterior (últimas 24 velas para 1h)
        # Máxima e mínima do dia anterior (velas 24 a 47 atrás)
        if len(df) >= 48:
            prev_day_df = df.iloc[-48:-24]
        else:
            prev_day_df = df.iloc[:-24] if len(df) > 24 else df

        prev_day_high = float(prev_day_df["high"].max())
        prev_day_low = float(prev_day_df["low"].min())

        close = float(df["close"].iloc[-1])
        high = float(df["high"].iloc[-1])
        low = float(df["low"].iloc[-1])
        open_price = float(df["open"].iloc[-1]) if "open" in df.columns else close

        # C1: Acumulação - verificar consolidação nas últimas 4 velas
        last_4 = df.iloc[-5:-1] if len(df) >= 5 else df.iloc[-4:]
        if len(last_4) >= 3:
            range_4 = float(last_4["high"].max() - last_4["low"].min())
            avg_range = float((last_4["high"] - last_4["low"]).mean())
            # Consolidação: range das últimas 4 velas é pequeno em relação ao ATR
            c1_accumulation = (range_4 < atr * 1.5) and (avg_range < atr * 0.8)
        else:
            c1_accumulation = False

        # C2: Manipulação - vela atual rompeu máxima/mínima do dia anterior com pavio
        # Pavio: high > prev_day_high ou low < prev_day_low
        # Mas fechou dentro do intervalo (close entre prev_day_low e prev_day_high)
        rompeu_alta = high > prev_day_high
        rompeu_baixa = low < prev_day_low
        fechou_dentro = prev_day_low <= close <= prev_day_high

        c2_manipulation_buy = rompeu_baixa and fechou_dentro
        c2_manipulation_sell = rompeu_alta and fechou_dentro

        # C3: Rejeição - corpo fechou no lado oposto ao rompimento
        # Para compra: pavio abaixo da mínima, corpo acima da mínima do dia anterior
        # Para venda: pavio acima da máxima, corpo abaixo da máxima do dia anterior
        corpo_alto = max(open_price, close)
        corpo_baixo = min(open_price, close)

        c3_rejection_buy = rompeu_baixa and (corpo_baixo >= prev_day_low)
        c3_rejection_sell = rompeu_alta and (corpo_alto <= prev_day_high)

        # Determinar sinal
        criteria_met = 0
        criteria_total=3
        signal = Signal.HOLD
        hold_reason = ""

        if c1_accumulation:
            criteria_met += 1

        if c2_manipulation_buy or c2_manipulation_sell:
            criteria_met += 1

        if c3_rejection_buy or c3_rejection_sell:
            criteria_met += 1

        # Decisão de entrada
        if c1_accumulation and c2_manipulation_buy and c3_rejection_buy:
            signal = Signal.BUY
        elif c1_accumulation and c2_manipulation_sell and c3_rejection_sell:
            signal = Signal.SELL
        else:
            if not c1_accumulation:
                hold_reason = "Mercado sem acumulação clara"
            elif not (c2_manipulation_buy or c2_manipulation_sell):
                hold_reason = "Rompimento sem rejeição (corpo fechou fora do intervalo)"
            else:
                hold_reason = "Aguardando formação da vela de 1h"

        # Calcular SL e TP
        sl_dist = atr * self.sl_mult
        if signal == Signal.BUY:
            sl_price = round(close - sl_dist, 4)
            tp1_price = round(close + sl_dist * self.tp1_rr, 4)
        elif signal == Signal.SELL:
            sl_price = round(close + sl_dist, 4)
            tp1_price = round(close - sl_dist * self.tp1_rr, 4)
        else:
            sl_price = 0.0
            tp1_price = 0.0

        return StrategyResult(
            signal=signal,
            hold_reason=hold_reason,
            indicators={
                "close": round(close, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "atr": round(atr, 6),
                "prev_day_high": round(prev_day_high, 4),
                "prev_day_low": round(prev_day_low, 4),
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(sl_dist / close, 5) if close != 0 else 0.0,
                "tp1_pct": round(sl_dist * self.tp1_rr / close, 5) if close != 0 else 0.0,
                "ts_pct": round(atr * self.ts_mult / close, 5) if close != 0 else 0.0,
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )