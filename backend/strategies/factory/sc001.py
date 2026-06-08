
"""
strategies/pattern_scalp.py — E010 Pattern Scalp (Reversão de Manipulação)

Lógica:
1. Identifica o "Opening Range" ou candles de exaustão de 15m.
2. Filtra pela volatilidade: Range do candle > 20% do ATR Diário.
3. Gatilho: Candle de rejeição (John Wick) ou Engolfo (Power Tower).
"""

from __future__ import annotations
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult

class PatternScalpStrategy(BaseStrategy):

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="SC001",
            name="SC001 - Pattern Scalp",
            description = (
                "Estratégia de reversão baseada em manipulação institucional. "
                "Detecta candles de 15m que excedem 20% do ATR diário (liquidez sweep) "
                "e opera a reversão imediata ao detectar padrões de rejeição (John Wick) "
                "ou engolfo (Power Tower). Ideal para aberturas de mercado e volatilidade extrema."
            ),
            tags   = ["scalp", "opening-range", "momentum"],
            recommended_timeframe = "5m",
            criteria=[
                {"id": "c1_manipulation", "label": "C1 Manipulação", "description": "Candle excede limiar de ATR diário."},
                {"id": "c2_rejection_pattern", "label": "C2 Padrão Rejeição", "description": "John Wick, Power Tower ou padrão de rejeição equivalente."},
            ],
            params = {
                "atr_mult_threshold": ParamDef(
                    type="float", default=0.20, min=0.10, max=0.50, step=0.05,
                    description="Percentual do ATR Diário para considerar manipulação (20% recomendado)"),
                "rejection_ratio": ParamDef(
                    type="float", default=2.0, min=1.0, max=4.0, step=0.1,
                    description="Tamanho da sombra vs corpo para o 'John Wick'"),
                "tp_rr": ParamDef(
                    type="float", default=2.0, min=1.0, max=5.0, step=0.5,
                    description="Risk:Reward para o scalping"),
            },
        )

    def __init__(self):
        self.atr_mult_threshold = 0.20
        self.rejection_ratio    = 2.0
        self.tp_rr              = 2.0

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> StrategyResult | None:
        # Precisamos de pelo menos 1 vela diária (simplificado aqui para usar os últimos candles)
        # Em um bot de 15m, 96 candles = 1 dia.
        if len(candles) < 100:
            return None

        df = pd.DataFrame([c.__dict__ for c in candles])
        
        # 1. Calcular ATR Diário aproximado (usando rolling de 96 períodos no 15m)
        # Idealmente o bot deveria receber o ATR real do D1, mas aqui estimamos:
        df['high_low'] = df['high'] - df['low']
        daily_range_est = df['high_low'].rolling(96).max().iloc[-1]
        
        last_candle = candles[-1]
        candle_range = last_candle.high - last_candle.low
        
        # 2. Check de Manipulação (Candle de exaustão)
        is_manipulation = candle_range > (daily_range_est * self.atr_mult_threshold)

        # 3. Identificar Padrão de Rejeição (calcula antes de montar indicators)
        signal = Signal.HOLD
        pattern = ""
        pattern_desc = ""
        hold_reason = "Aguardando candle de exaustão (>20% ATR Est.)"

        if is_manipulation:
            body       = abs(last_candle.close - last_candle.open)
            upper_wick = last_candle.high - max(last_candle.close, last_candle.open)
            lower_wick = min(last_candle.close, last_candle.open) - last_candle.low

            if lower_wick > (body * self.rejection_ratio) and last_candle.close > last_candle.open:
                signal      = Signal.BUY
                pattern     = "John Wick"
                pattern_desc = "Pavio inferior longo — reversão de alta"
            elif upper_wick > (body * self.rejection_ratio) and last_candle.close < last_candle.open:
                signal      = Signal.SELL
                pattern     = "John Wick"
                pattern_desc = "Pavio superior longo — reversão de baixa"
            elif len(candles) >= 2:
                prev = candles[-2]
                if last_candle.close > prev.open and prev.close < prev.open and last_candle.close > last_candle.open:
                    signal      = Signal.BUY
                    pattern     = "Power Tower"
                    pattern_desc = "Engolfo de alta — reversão de alta"
                elif last_candle.close < prev.open and prev.close > prev.open and last_candle.close < last_candle.open:
                    signal      = Signal.SELL
                    pattern     = "Power Tower"
                    pattern_desc = "Engolfo de baixa — reversão de baixa"

            if signal == Signal.HOLD:
                hold_reason = "Manipulação detectada, aguardando gatilho de rejeição"

        indicators = {
            "daily_range_est":  round(daily_range_est, 2),
            "candle_range":     round(candle_range, 2),
            "open_high":        last_candle.high if is_manipulation else 0,
            "open_low":         last_candle.low  if is_manipulation else 0,
            "atr_manipulation": is_manipulation,
            "pattern":          pattern,
            "pattern_desc":     pattern_desc,
        }

        return StrategyResult(signal, indicators, hold_reason=hold_reason, criteria_met=2 if signal in (Signal.BUY, Signal.SELL) else 0, criteria_total=2)
