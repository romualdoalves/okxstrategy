"""
PA009 - Weis Wave Wyckoff Strategy
Gerado pela Fábrica de Estratégias
Data de criação: 2024-01-01
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class WeisWaveWyckoffStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="PA009",
            name="PA009 - Weis Wave Wyckoff",
            description="Estratégia de Price Action para Mini Índice em gráfico de 2 minutos usando o indicador Weis Wave (Nelógica) para medir esforço vs resultado. O Weis Wave agrupa volume por pernadas, identificando divergências (exaustão) e continuidade (força). O gatilho de entrada é confirmado pelo Price Action no rompimento de pivôs, com gestão de risco via stop loss abaixo do candle de rompimento e realização parcial.",
            tags=["pattern", "volume", "swing", "intraday"],
            recommended_timeframe="2m",
            criteria=[
                {"id": "c1_divergence", "label": "C1 Divergência", "description": "Weis Wave mostra divergência (preço faz topo mais alto, onda menor) ou continuidade (onda maior que anterior)"},
                {"id": "c2_price_action", "label": "C2 Price Action", "description": "Rompimento de pivô de alta/baixa confirmado por candle de rompimento"},
            ],
            params={
                "weis_wave_period": ParamDef(type="int", default=3, min=2, max=10, step=1, description="Período do Weis Wave (sensibilidade)"),
                "atr_period": ParamDef(type="int", default=14, min=5, max=50, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Trailing Stop após TP1"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.weis_wave_period = p["weis_wave_period"].default
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

    def compute_with_context(self, candles, context=None):
        min_candles = max(60, self.weis_wave_period * 5 + 10)
        if len(candles) < min_candles:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_candles} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "weis_wave": 0.0, "atr": 0.0},
            )

        df = pd.DataFrame([
            {"high": c.high, "low": c.low, "close": c.close, "volume": c.volume}
            for c in candles
        ])

        # Calcular ATR
        atr_series = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        if atr_series is None:
            return None
        atr = float(atr_series.iloc[-1])

        # Calcular Weis Wave (volume acumulado por pernada)
        # Identifica pernadas baseadas em mudanças de direção do preço
        weis_wave_values = []
        current_wave_volume = 0.0
        current_direction = 0  # 0 = neutro, 1 = alta, -1 = baixa

        for i in range(1, len(df)):
            price_change = df["close"].iloc[i] - df["close"].iloc[i-1]
            if price_change > 0:
                if current_direction == -1:
                    weis_wave_values.append(current_wave_volume)
                    current_wave_volume = 0.0
                current_direction = 1
                current_wave_volume += df["volume"].iloc[i]
            elif price_change < 0:
                if current_direction == 1:
                    weis_wave_values.append(current_wave_volume)
                    current_wave_volume = 0.0
                current_direction = -1
                current_wave_volume += df["volume"].iloc[i]
            else:
                current_wave_volume += df["volume"].iloc[i]

        # Adicionar última onda
        weis_wave_values.append(current_wave_volume)

        # Calcular ondas de preço correspondentes
        price_waves = []
        current_wave_start = df["close"].iloc[0]
        current_wave_direction = 0

        for i in range(1, len(df)):
            price_change = df["close"].iloc[i] - df["close"].iloc[i-1]
            if price_change > 0:
                if current_wave_direction == -1:
                    price_waves.append(abs(df["close"].iloc[i-1] - current_wave_start))
                    current_wave_start = df["close"].iloc[i-1]
                current_wave_direction = 1
            elif price_change < 0:
                if current_wave_direction == 1:
                    price_waves.append(abs(df["close"].iloc[i-1] - current_wave_start))
                    current_wave_start = df["close"].iloc[i-1]
                current_wave_direction = -1

        price_waves.append(abs(df["close"].iloc[-1] - current_wave_start))

        # Pegar últimas ondas para análise
        last_waves_volume = weis_wave_values[-self.weis_wave_period:] if len(weis_wave_values) >= self.weis_wave_period else weis_wave_values
        last_waves_price = price_waves[-self.weis_wave_period:] if len(price_waves) >= self.weis_wave_period else price_waves

        close = float(df["close"].iloc[-1])
        high = float(df["high"].iloc[-1])
        low = float(df["low"].iloc[-1])

        # Calcular Weis Wave atual
        weis_wave_value = float(last_waves_volume[-1]) if last_waves_volume else 0.0

        # Critérios
        criteria_met = 0
        criteria_total=2

        # C1: Divergência / Continuidade
        c1_met = False
        c1_buy = False
        c1_sell = False

        if len(last_waves_volume) >= 2 and len(last_waves_price) >= 2:
            # Verificar divergência (preço novo extremo com onda menor)
            # Para alta: preço subindo mas volume diminuindo
            if len(last_waves_price) >= 2 and len(last_waves_volume) >= 2:
                price_trend = last_waves_price[-1] > last_waves_price[-2]
                volume_trend = last_waves_volume[-1] > last_waves_volume[-2]

                # Continuidade (força): preço e volume na mesma direção
                if price_trend and volume_trend:
                    c1_met = True
                    c1_buy = True
                elif not price_trend and not volume_trend:
                    c1_met = True
                    c1_sell = True

                # Divergência (exaustão): preço novo extremo com volume menor
                if price_trend and not volume_trend:
                    c1_met = True
                    c1_sell = True  # Exaustão de alta
                elif not price_trend and volume_trend:
                    c1_met = True
                    c1_buy = True  # Exaustão de baixa

        if c1_met:
            criteria_met += 1

        # C2: Price Action - Rompimento de pivô
        c2_met = False
        c2_buy = False
        c2_sell = False

        # Identificar pivôs (últimos 5 candles)
        if len(df) >= 6:
            # Pivô de alta: candle atual acima da máxima dos últimos 3 candles
            recent_highs = df["high"].iloc[-4:-1].max()
            recent_lows = df["low"].iloc[-4:-1].min()

            # Rompimento de alta
            if close > recent_highs:
                c2_met = True
                c2_buy = True

            # Rompimento de baixa
            if close < recent_lows:
                c2_met = True
                c2_sell = True

        if c2_met:
            criteria_met += 1

        # Decisão final
        signal = Signal.HOLD
        hold_reason = ""

        if c1_buy and c2_buy and criteria_met == 2:
            signal = Signal.BUY
        elif c1_sell and c2_sell and criteria_met == 2:
            signal = Signal.SELL
        else:
            if not c1_met:
                hold_reason = "Mercado sem divergência ou continuidade clara"
            elif not c2_met:
                hold_reason = "Aguardando rompimento de pivô"
            else:
                hold_reason = "Weis Wave neutro ou conflitante"

        # Calcular stops e targets
        sl_dist = atr * self.sl_mult

        if signal == Signal.BUY:
            sl_price = round(close - sl_dist, 4)
            tp1_price = round(close + sl_dist * self.tp1_rr, 4)
        elif signal == Signal.SELL:
            sl_price = round(close + sl_dist, 4)
            tp1_price = round(close - sl_dist * self.tp1_rr, 4)
        else:
            sl_price = round(close - sl_dist, 4) if signal == Signal.BUY else round(close + sl_dist, 4)
            tp1_price = round(close + sl_dist * self.tp1_rr, 4) if signal == Signal.BUY else round(close - sl_dist * self.tp1_rr, 4)

        return StrategyResult(
            signal=signal,
            hold_reason=hold_reason,
            indicators={
                "weis_wave": round(weis_wave_value, 2),
                "close": round(close, 4),
                "atr": round(atr, 6),
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(sl_dist / close, 5),
                "tp1_pct": round(sl_dist * self.tp1_rr / close, 5),
                "ts_pct": round(atr * self.ts_mult / close, 5),
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )