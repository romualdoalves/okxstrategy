"""MR006 - MACD Exaustão F34
Estratégia de reversão à média que identifica exaustão do mercado usando MACD com parâmetros da proporção áurea (55,144,34) e o indicador F34 como gatilho visual.
Gerado pela Fábrica de Estratégias
"""
from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class MacdExaustãoF34Strategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="MR006",
            name="MR006 - MACD Exaustão F34",
            description="Estratégia de reversão à média que identifica exaustão do mercado usando MACD com parâmetros da proporção áurea (55,144,34) e o indicador F34 como gatilho visual. O MACD calibrado mede o 'esticamento' do mercado, enquanto o F34 confirma a reversão.",
            tags=["mean-reversion", "reversal", "momentum"],
            recommended_timeframe="15m",
            criteria=[
                {"id": "c1_exhaustion", "label": "C1 Exaustão", "description": "MACD signal em nível extremo (mínima ou máxima histórica)"},
                {"id": "c2_f34_confirmation", "label": "C2 Confirmação F34", "description": "F34 muda de cor indicando reversão (verde para compra, vermelho para venda)"},
                {"id": "c3_histogram_reversal", "label": "C3 Reversão Histograma", "description": "Histograma do MACD começa a diminuir (reversão de direção)"},
            ],
            params={
                "fast_period": ParamDef(type="int", default=55, min=30, max=80, step=1, description="Período da média curta do MACD (proporção áurea)"),
                "slow_period": ParamDef(type="int", default=144, min=100, max=200, step=1, description="Período da média longa do MACD (proporção áurea)"),
                "signal_period": ParamDef(type="int", default=34, min=20, max=50, step=1, description="Período da linha de sinal do MACD (proporção áurea)"),
                "f34_threshold": ParamDef(type="float", default=0.5, min=0.1, max=2.0, step=0.1, description="Limiar para considerar F34 como verde/vermelho (valor absoluto do indicador)"),
                "atr_period": ParamDef(type="int", default=14, min=7, max=30, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Trailing Stop após TP1"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.fast_period = p["fast_period"].default
        self.slow_period = p["slow_period"].default
        self.signal_period = p["signal_period"].default
        self.f34_threshold = p["f34_threshold"].default
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
        min_candles = self.slow_period + self.signal_period + 5
        if len(candles) < min_candles:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_candles} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "macd_line": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0, "f34": 0.0, "atr": 0.0},
            )

        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles])
        close_series = df["close"]

        # MACD com parâmetros áureos
        macd_result = ta.macd(close_series, fast=self.fast_period, slow=self.slow_period, signal=self.signal_period)
        if macd_result is None or len(macd_result) < 1:
            return None
        macd_line = macd_result.iloc[:, 0]
        macd_signal = macd_result.iloc[:, 1]
        macd_histogram = macd_result.iloc[:, 2]

        # ATR
        atr_series = ta.atr(df["high"], df["low"], close_series, length=self.atr_period)
        if atr_series is None or len(atr_series) < 1:
            return None

        # F34: indicador proprietário baseado na diferença entre preço e média móvel de 34 períodos
        ema34 = ta.ema(close_series, length=34)
        if ema34 is None or len(ema34) < 1:
            return None
        f34_series = (close_series - ema34) / ema34 * 100  # percentual

        # Últimos valores
        macd_line_val = float(macd_line.iloc[-1])
        macd_signal_val = float(macd_signal.iloc[-1])
        macd_histogram_val = float(macd_histogram.iloc[-1])
        f34_val = float(f34_series.iloc[-1])
        atr_val = float(atr_series.iloc[-1])
        close = float(close_series.iloc[-1])

        # Histórico para verificar reversão do histograma
        macd_histogram_prev = float(macd_histogram.iloc[-2]) if len(macd_histogram) >= 2 else macd_histogram_val

        # Critérios
        criteria_met = 0
        criteria_total=3

        # C1: MACD signal em nível extremo (mínima ou máxima histórica)
        # Usamos um percentil simples: se o signal está entre os 20% mais baixos ou 20% mais altos
        signal_series = macd_signal.dropna()
        if len(signal_series) >= 20:
            lower_bound = signal_series.quantile(0.20)
            upper_bound = signal_series.quantile(0.80)
            c1_exhaustion = macd_signal_val <= lower_bound or macd_signal_val >= upper_bound
        else:
            c1_exhaustion = False
        if c1_exhaustion:
            criteria_met += 1

        # C2: F34 confirma reversão (verde para compra, vermelho para venda)
        c2_f34_confirmation = abs(f34_val) >= self.f34_threshold
        if c2_f34_confirmation:
            criteria_met += 1

        # C3: Histograma mostra reversão (diminuição do valor absoluto)
        hist_abs_prev = abs(macd_histogram_prev)
        hist_abs_curr = abs(macd_histogram_val)
        c3_histogram_reversal = hist_abs_curr < hist_abs_prev
        if c3_histogram_reversal:
            criteria_met += 1

        # Decisão de entrada
        signal = Signal.HOLD
        hold_reason = ""

        # Compra: MACD signal em nível extremo baixo, F34 positivo acima do threshold, histograma positivo e diminuindo
        if c1_exhaustion and c2_f34_confirmation and c3_histogram_reversal:
            if macd_signal_val <= signal_series.quantile(0.20) and f34_val > 0 and macd_histogram_val > 0:
                signal = Signal.BUY
            elif macd_signal_val >= signal_series.quantile(0.80) and f34_val < 0 and macd_histogram_val < 0:
                signal = Signal.SELL

        if signal == Signal.HOLD:
            if not c1_exhaustion:
                hold_reason = "MACD signal não está em nível extremo"
            elif not c2_f34_confirmation:
                hold_reason = "F34 não confirma reversão"
            elif not c3_histogram_reversal:
                hold_reason = "Histograma não mostra reversão"
            else:
                hold_reason = "Condições de entrada não alinhadas (direção inconsistente)"

        # Gestão de risco
        sl_dist = atr_val * self.sl_mult
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
                "macd_line": round(macd_line_val, 6),
                "macd_signal": round(macd_signal_val, 6),
                "macd_histogram": round(macd_histogram_val, 6),
                "f34": round(f34_val, 6),
                "atr": round(atr_val, 6),
                "close": round(close, 4),
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(sl_dist / close, 5) if close != 0 else 0.0,
                "tp1_pct": round(sl_dist * self.tp1_rr / close, 5) if close != 0 else 0.0,
                "ts_pct": round(atr_val * self.ts_mult / close, 5) if close != 0 else 0.0,
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )