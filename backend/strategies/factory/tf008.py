"""
strategies/cycle_shift.py — Cycle Shift (CJacob)

Identifica transições de ciclo macro baseadas na EMA 21 Semanal.
Inspirada pela análise de CJacob sobre o suporte de 82k.
"""

from __future__ import annotations
from typing import Optional
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult

class CycleShiftStrategy(BaseStrategy):

    @classmethod
    def extra_timeframes(cls) -> list[str]:
        # Esta estratégia obrigatoriamente olha para o Semanal (1W)
        return ["1W"]

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF008",
            name="TF008 - Cycle Shift (CJacob)",
            description = (
                "Estratégia macro baseada na tese de CJacob: "
                "(1) Primeiro fechamento semanal completo acima da EMA 21; "
                "(2) Confirmação da EMA 21 como suporte na semana seguinte; "
                "(3) Início de um novo ciclo de alta pós-bear market curto."
            ),
            tags   = ["macro", "trend", "weekly", "cjacob", "ema"],
            recommended_timeframe = "1D",
            criteria=[
                {"id": "c1_cycle", "label": "C1 Ciclo Macro", "description": "Fechamento semanal completo acima da EMA 21."},
                {"id": "c2_support", "label": "C2 Suporte", "description": "EMA 21 semanal confirmada como suporte."},
                {"id": "c3_retest", "label": "C3 Proximidade", "description": "Preço próximo da EMA semanal para entrada de ciclo."},
            ],
            params = {
                "ema_period": ParamDef("int", 21, "Média principal do ciclo", 10, 50, 1),
                "atr_period": ParamDef("int", 14, "ATR para volatilidade", 7, 30, 1),
                "sl_mult":    ParamDef("float", 2.5, "Stop Loss (x ATR)", 1.0, 5.0, 0.1),
                "tp_rr":      ParamDef("float", 4.0, "Alvo de ciclo (Risk:Reward)", 2.0, 10.0, 0.5),
            },
        )

    def __init__(self):
        self.ema_period = 21
        self.atr_period = 14
        self.sl_mult    = 2.5
        self.tp_rr      = 4.0

    def set_params(self, params: dict) -> None:
        self.ema_period = params.get("ema_period", self.ema_period)
        self.atr_period = params.get("atr_period", self.atr_period)
        self.sl_mult    = params.get("sl_mult", self.sl_mult)
        self.tp_rr      = params.get("tp_rr", self.tp_rr)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles: list, context: dict | None = None) -> Optional[StrategyResult]:
        if not candles or len(candles) < self.atr_period + 5:
            return None

        # 1. Obter dados semanais do contexto
        extra = (context or {}).get('extra_candles', {})
        candles_w = extra.get('1W', [])

        if len(candles_w) < self.ema_period + 5:
            df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close} for c in candles])
            atr = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
            atr_val = float(atr.iloc[-1]) if atr is not None else 0.0
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aguardando dados semanais ({len(candles_w)}/{self.ema_period + 5} barras 1W)",
                criteria_met=0,
                criteria_total=3,
                indicators={"close": round(candles[-1].close, 4), "atr": round(atr_val, 6),
                            "ema_21_w": 0, "dist_ema_w": 0, "c1_full_above": 0, "c2_support_ok": 0},
            )

        # 2. Processar Semanal
        df_w = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close} for c in candles_w])
        ema_w = ta.ema(df_w["close"], length=self.ema_period)
        
        if ema_w is None: return None
        
        ema_w_now = float(ema_w.iloc[-1])
        ema_w_prev = float(ema_w.iloc[-2])
        
        last_w = df_w.iloc[-1]
        prev_w = df_w.iloc[-2]

        # C1: Vela inteira anterior acima da EMA 21 (Low > EMA)
        c1_full_candle = prev_w["low"] > ema_w_prev
        
        # C2: Suporte confirmado (Preço atual acima da EMA e testando)
        c2_support = last_w["close"] > ema_w_now
        dist_to_ema = (last_w["low"] - ema_w_now) / ema_w_now
        c2_testing = dist_to_ema < 0.02 # Dentro de 2% da média é um teste
        
        # 3. Gatilho no timeframe do bot (ex: 4H ou 1D)
        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close} for c in candles])
        atr = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        atr_val = float(atr.iloc[-1]) if atr is not None else candles[-1].close * 0.01
        
        close = candles[-1].close
        
        # Sinal de Compra
        signal = Signal.HOLD
        if c1_full_candle and c2_support:
            # Se já tivemos a vela inteira e estamos acima no teste, entramos
            signal = Signal.BUY

        # Gestão de Risco
        sl_dist = atr_val * self.sl_mult
        sl_price = round(close - sl_dist, 4)
        tp1_price = round(close + sl_dist * self.tp_rr, 4)

        return StrategyResult(
            signal=signal,
            criteria_met=3 if signal in (Signal.BUY, Signal.SELL) else 0,
            criteria_total=3,
            indicators={
                "ema_21_w": round(ema_w_now, 2),
                "dist_ema_w": round(dist_to_ema * 100, 2),
                "c1_full_above": 1 if c1_full_candle else 0,
                "c2_support_ok": 1 if c2_support else 0,
                "atr": round(atr_val, 4)
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "author": "CJacob"
            }
        )
