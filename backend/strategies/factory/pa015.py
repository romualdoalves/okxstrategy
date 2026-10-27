"""PA015 - Fibonacci Retração+Projeção Strategy
Gerado pela Fábrica de Estratégias
Data de criação: 2024-01-01
Atualizado: 2026
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class FibonacciRetraçãoprojeçãoStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="PA015",
            name="PA015 - Fibonacci Retração+Projeção",
            description="Estratégia que unifica retração e projeção de Fibonacci (Mapeamento Maçuda/Marcos Ross). Traça-se do topo ao fundo em tendências de alta para projetar alvos automaticamente. Entradas em níveis de retração com Stop Loss técnico abaixo da região de 23.6% e alvos nas projeções de 127.2% ou 161.8%. Utiliza confluência com EMA.",
            tags=["pattern", "swing", "intraday"],
            recommended_timeframe="15m",
            criteria=[
                {"id": "c1_trend", "label": "C1 Tendência", "description": "Preço acima da EMA de longo prazo (tendência de alta)"},
                {"id": "c2_retracement", "label": "C2 Retração", "description": "Preço atingiu a região de retração de Fibonacci configurada"},
                {"id": "c3_volume", "label": "C3 Volume", "description": "Volume financeiro acima da média de 20 períodos"},
            ],
            params={
                "atr_period": ParamDef(type="int", default=14, min=5, max=50, step=1, description="Período do ATR para estatísticas do card"),
                "ema_period": ParamDef(type="int", default=66, min=20, max=100, step=1, description="Período da EMA para confluência de tendência"),
                "lookback_candles": ParamDef(type="int", default=50, min=10, max=100, step=1, description="Janela de candles para detectar topo/fundo recente"),
                "fib_retrace_entry": ParamDef(type="float", default=0.5, min=0.382, max=0.618, step=0.001, description="Nível de retração para entrada (0.5 = 50%)"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.atr_period = p["atr_period"].default
        self.ema_period = p["ema_period"].default
        self.lookback_candles = p["lookback_candles"].default
        self.fib_retrace_entry = p["fib_retrace_entry"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles, context=None):
        min_candles = max(self.ema_period + 5, 100)
        if len(candles) < min_candles:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_candles} velas ({len(candles)} recebidas)",
                indicators={
                    "close": float(candles[-1].close), "ema": 0.0, "atr": 0.0, 
                    "fib_high": 0.0, "fib_low": 0.0, "fib_retrace_50": 0.0, 
                    "fib_retrace_618": 0.0, "fib_proj_1272": 0.0, "fib_proj_1618": 0.0
                },
            )

        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles])

        # 1. Cálculos de Indicadores de Confluência
        ema_series = ta.ema(df["close"], length=self.ema_period)
        if ema_series is None: return None
        ema = float(ema_series.iloc[-1])

        atr_series = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        if atr_series is None: return None
        atr = float(atr_series.iloc[-1])

        volume_ma_series = ta.sma(df["volume"], length=20)
        if volume_ma_series is None: return None
        volume_ma = float(volume_ma_series.iloc[-1])

        close = float(candles[-1].close)
        high = float(candles[-1].high)
        low = float(candles[-1].low)
        volume = float(candles[-1].volume)

        # 2. Mapeamento da Pernada (Topos e Fundos)
        lookback = min(self.lookback_candles, len(candles) - 1)
        recent_high = float(df["high"].iloc[-lookback:].max())
        recent_low = float(df["low"].iloc[-lookback:].min())

        if recent_high <= recent_low:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Movimento de mercado inconsistente ou nulo",
                indicators={"close": round(close, 4), "ema": round(ema, 4), "atr": round(atr, 6), "fib_high": round(recent_high, 4), "fib_low": round(recent_low, 4), "fib_retrace_50": 0.0, "fib_retrace_618": 0.0, "fib_proj_1272": 0.0, "fib_proj_1618": 0.0},
            )

        # 3. Engenharia Matemática de Fibonacci (Configuração Unificada / Invertida)
        fib_range = recent_high - recent_low
        
        # Na lógica da ferramenta do vídeo (Traçado Topo -> Fundo):
        # 50% é o centro matemático invariável
        fib_retrace_50 = recent_high - (fib_range * 0.500)
        
        # O nível visual de 61.8% do Maçuda fica acima dos 50% (equivalente ao 38.2% clássico medido de cima)
        fib_retrace_618 = recent_high - (fib_range * 0.382) 
        
        # Nível técnico de gatilho dinâmico baseado no parâmetro informado
        if math.isclose(self.fib_retrace_entry, 0.5):
            fib_entry_level = fib_retrace_50
        else:
            # Mapeia dinamicamente o complemento caso o usuário selecione 0.618 de tela
            fib_entry_level = recent_high - (fib_range * (1.0 - self.fib_retrace_entry))

        # Stop Loss técnico: posicionado logo abaixo do nível de 23.6% em relação ao fundo (ou seja, 76.4% medido do topo)
        fib_stop_price = recent_high - (fib_range * 0.764)

        # Projeções de Alvos (Expansões projetadas para cima, além do Topo recente)
        fib_proj_1272 = recent_high + (fib_range * 0.272)
        fib_proj_1618 = recent_high + (fib_range * 0.618)

        # 4. Avaliação de Critérios
        criteria_met = 0
        criteria_total = 3

        # C1: Tendência macro defensiva
        c1_cond = close > ema
        if c1_cond: criteria_met += 1

        # C2: Preço testando a zona de retração na vela atual
        c2_cond = low <= fib_entry_level and high >= fib_entry_level
        if c2_cond: criteria_met += 1

        # C3: Confirmação de volume do pullback
        c3_cond = volume > volume_ma
        if c3_cond: criteria_met += 1

        # 5. Determinação do Sinal Operacional (Focado estritamente em Compras na Ponta Compradora)
        signal = Signal.HOLD
        hold_reason = ""

        if c1_cond and c2_cond and c3_cond:
            signal = Signal.BUY
        else:
            if not c1_cond:
                hold_reason = "Preço abaixo da EMA (Tendência de Baixa no Intraday)"
            elif not c2_cond:
                hold_reason = f"Preço fora da zona de retração alvo ({round(fib_entry_level, 4)})"
            elif not c3_cond:
                hold_reason = "Volume financeiro abaixo da média de 20 períodos"

        # 6. Atribuição de Stops Técnicos baseados em Fibonacci
        if signal == Signal.BUY:
            sl_price = round(fib_stop_price, 4)
            tp1_price = round(fib_proj_1272, 4)
            
            # Salvaguarda para evitar stops colados ou inválidos se o range for muito curto
            if sl_price >= close:
                sl_price = round(close - (atr * 2.0), 4)
        else:
            sl_price = 0.0
            tp1_price = 0.0

        # Cálculos de metadados percentuais para o dashboard
        sl_pct = round((close - sl_price) / close, 5) if (signal == Signal.BUY and close > 0) else 0.0
        tp1_pct = round((tp1_price - close) / close, 5) if (signal == Signal.BUY and close > 0) else 0.0

        return StrategyResult(
            signal=signal,
            hold_reason=hold_reason,
            indicators={
                "close": round(close, 4),
                "ema": round(ema, 4),
                "atr": round(atr, 6),
                "fib_high": round(recent_high, 4),
                "fib_low": round(recent_low, 4),
                "fib_retrace_50": round(fib_retrace_50, 4),
                "fib_retrace_618": round(fib_retrace_618, 4),
                "fib_proj_1272": round(fib_proj_1272, 4),
                "fib_proj_1618": round(fib_proj_1618, 4),
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": sl_pct,
                "tp1_pct": tp1_pct,
                "ts_pct": round((atr * 3.0) / close, 5) if close > 0 else 0.0,
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )