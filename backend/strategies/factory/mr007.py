"""MR007 - Power Law DCA Dinâmico
Estratégia de reversão à média baseada na Lei de Potência do Bitcoin.
Gerado pela Fábrica de Estratégias
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class PowerLawDcaDinâmicoStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="MR007",
            name="MR007 - Power Law DCA Dinâmico",
            description="Estratégia de reversão à média baseada na Lei de Potência do Bitcoin. Calcula o preço justo via regressão log10 e opera DCA dinâmico conforme o Z-Score. Compra agressiva abaixo do piso, DCA normal em desconto, pausa na neutra e realiza parcialmente no topo do canal.",
            tags=["mean-reversion", "statistical", "swing", "investment"],
            recommended_timeframe="1d",
            criteria=[
                {"id": "c1_below_fair", "label": "C1 Abaixo do Justo", "description": "Z-Score < 0 (preço abaixo do valor justo)"},
                {"id": "c2_aggressive_discount", "label": "C2 Desconto Agressivo", "description": "Z-Score < -1.5 (piso do corredor)"},
                {"id": "c3_above_fair", "label": "C3 Acima do Justo", "description": "Z-Score > 0 (preço acima do valor justo)"},
                {"id": "c4_exhaustion", "label": "C4 Exaustão", "description": "Z-Score > 1.2 (zona de venda parcial)"},
                {"id": "c5_bubble", "label": "C5 Bolha", "description": "Z-Score > 2.0 (topo do canal)"},
            ],
            params={
                "atr_period": ParamDef(type="int", default=14, min=5, max=50, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=3.5, min=0.5, max=10.0, step=0.1, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=2.5, min=0.5, max=10.0, step=0.1, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=4.0, min=1.0, max=10.0, step=0.1, description="Multiplicador ATR para Trailing Stop"),
                "power_law_a": ParamDef(type="float", default=-17.584, min=-30.0, max=-10.0, step=0.001, description="Intercepto da regressão log10 da Power Law"),
                "power_law_b": ParamDef(type="float", default=5.82, min=4.0, max=7.0, step=0.01, description="Expoente da Power Law (Base 10)"),
                "std_dev": ParamDef(type="float", default=0.25, min=0.1, max=0.5, step=0.01, description="Desvio padrão do resíduo log10 para Z-Score"),
                "buy_aggressive_z": ParamDef(type="float", default=-1.5, min=-3.0, max=0.0, step=0.1, description="Limiar Z-Score para compra agressiva (Over-DCA)"),
                "buy_normal_z": ParamDef(type="float", default=0.0, min=-2.0, max=1.0, step=0.1, description="Limiar Z-Score para compra normal (DCA regular)"),
                "sell_partial_z": ParamDef(type="float", default=1.2, min=0.5, max=2.5, step=0.1, description="Limiar Z-Score para iniciar vendas parciais"),
                "sell_strong_z": ParamDef(type="float", default=2.0, min=1.5, max=3.0, step=0.1, description="Limiar Z-Score para venda forte"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.atr_period = p["atr_period"].default
        self.sl_mult = p["sl_mult"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default
        self.power_law_a = p["power_law_a"].default
        self.power_law_b = p["power_law_b"].default
        self.std_dev = p["std_dev"].default
        self.buy_aggressive_z = p["buy_aggressive_z"].default
        self.buy_normal_z = p["buy_normal_z"].default
        self.sell_partial_z = p["sell_partial_z"].default
        self.sell_strong_z = p["sell_strong_z"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles, context=None):
        min_c = self.atr_period + 5
        if len(candles) < min_c:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_c} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "z_score": 0.0, "fair_price": 0.0, "atr": 0.0, "days_alive": 0.0},
            )

        # Dias desde o bloco gênesis (2009-01-03). CandleBar.epoch é o início do candle em ms
        # (o atributo `.time` não existe: a estratégia ficava eternamente em "atributo de tempo ausente").
        genesis_timestamp = 1230940800  # 2009-01-03 00:00:00 UTC
        current_ts = float(candles[-1].epoch) / 1000.0

        if current_ts < genesis_timestamp:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Erro: Data do candle é anterior ao Bloco Gênesis.",
                indicators={"close": float(candles[-1].close), "z_score": 0.0, "fair_price": 0.0, "atr": 0.0, "days_alive": 0.0},
            )
        
        days_alive = (current_ts - genesis_timestamp) / 86400.0

        # Calcular preço justo via Power Law
        log_days = math.log10(days_alive) if days_alive > 0 else 0.0
        log_fair_price = self.power_law_a + self.power_law_b * log_days
        fair_price = 10 ** log_fair_price

        # Calcular Z-Score
        close = float(candles[-1].close)
        log_close = math.log10(close) if close > 0 else 0.0
        z_score = (log_close - log_fair_price) / self.std_dev if self.std_dev > 0 else 0.0

        # Construir dataframe otimizado para o cálculo do indicador técnico ATR
        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close} for c in candles])
        atr_s = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        
        # Proteção contra falha do pipeline técnico (Evita quebra por NoneType)
        if atr_s is None or atr_s.empty or pd.isna(atr_s.iloc[-1]):
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Aviso: Falha no cálculo do indicador ATR. Aguardando estabilidade.",
                indicators={"close": close, "z_score": round(z_score, 4), "fair_price": round(fair_price, 4), "atr": 0.0, "days_alive": round(days_alive, 2)},
            )
        
        atr = float(atr_s.iloc[-1])

        # Avaliação de Critérios Estatísticos (criteria_total=5)
        criteria_met = 0
        if z_score < 0: criteria_met += 1
        if z_score < self.buy_aggressive_z: criteria_met += 1
        if z_score > 0: criteria_met += 1
        if z_score > self.sell_partial_z: criteria_met += 1
        if z_score > self.sell_strong_z: criteria_met += 1

        # Determinar sinal operacional e preencher metadados informativos
        signal = Signal.HOLD
        if z_score < self.buy_aggressive_z:
            signal = Signal.BUY
            hold_reason = "Executando COMPRA AGRESSIVA: Preço no piso histórico de desconto."
        elif z_score < self.buy_normal_z:
            signal = Signal.BUY
            hold_reason = "Executando COMPRA PADRÃO: Ativo operando abaixo do valor de rede justo."
        elif z_score > self.sell_strong_z:
            signal = Signal.SELL
            hold_reason = "Executando VENDA FORTE: Topo macro do canal (Bolha detectada)."
        elif z_score > self.sell_partial_z:
            signal = Signal.SELL
            hold_reason = "Executando VENDA PARCIAL: Zona de exaustão de tendência de alta."
        else:
            hold_reason = "Zona Neutra de Equilíbrio (Aguardando assimetrias)"

        # Cálculos de Gerenciamento de Risco (SL / TP / TS)
        sl_dist = atr * self.sl_mult
        if signal == Signal.BUY:
            sl_price = round(close - sl_dist, 4)
            tp1_price = round(close + sl_dist * self.tp1_rr, 4)
        elif signal == Signal.SELL:
            sl_price = round(close + sl_dist, 4)
            tp1_price = round(close - sl_dist * self.tp1_rr, 4)
        else:
            sl_price = round(close - sl_dist, 4)
            tp1_price = round(close + sl_dist * self.tp1_rr, 4)

        return StrategyResult(
            signal=signal,
            hold_reason=hold_reason,
            indicators={
                "z_score": round(z_score, 4),
                "fair_price": round(fair_price, 4),
                "close": round(close, 4),
                "atr": round(atr, 6),
                "days_alive": round(days_alive, 2),
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(sl_dist / close, 5) if close > 0 else 0.0,
                "tp1_pct": round((sl_dist * self.tp1_rr) / close, 5) if close > 0 else 0.0,
                "ts_pct": round((atr * self.ts_mult) / close, 5) if close > 0 else 0.0,
            },
            criteria_met=criteria_met,
            criteria_total=5,
        )
