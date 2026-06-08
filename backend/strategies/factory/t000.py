"""
strategies/test_strategy.py — T000 Teste da Plataforma

Bot de diagnóstico que emite BUY em todo candle fechado.
Valida o pipeline completo: sinal → critérios de ordem → envio → fill → stop → fechamento.
Não aparece na listagem pública de estratégias (tag "test").
"""

from __future__ import annotations
from typing import Optional

from backend.strategies.base import BaseStrategy, Signal, StrategyInfo, StrategyResult


class TestPlatformStrategy(BaseStrategy):

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="T000",
            name="T000 - Teste da Plataforma",
            description=(
                "Bot de diagnóstico que emite BUY em todo candle fechado. "
                "Valida o pipeline completo sem restrição de sinal: "
                "sinal → critérios de ordem → envio → fill → stop → fechamento."
            ),
            tags=["test"],
            recommended_timeframe="1m",
            recommended_symbol="BTC-USDT",
            criteria=[
                {"id": "c1_pipeline", "label": "C1 Pipeline", "description": "Sinal de diagnóstico ativo para validar o pipeline."},
            ],
            params={},
        )

    def set_params(self, params: dict) -> None:
        pass

    def compute(self, candles: list) -> Optional[StrategyResult]:
        if not candles:
            return None
        last  = candles[-1]
        close = last.close
        return StrategyResult(
            signal=Signal.BUY,
            indicators={
                "close":    round(close, 2),
                "high":     round(last.high, 2),
                "low":      round(last.low, 2),
                "atr":      round(close * 0.005, 6),
                "test_ok":  1.0,
            },
            metadata={
                "sl_price":  round(close * 0.99, 4),
                "tp1_price": round(close * 1.02, 4),
            },
            criteria_met=1,
            criteria_total=1,
        )
