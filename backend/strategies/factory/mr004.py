import pandas as pd
import numpy as np
import pandas_ta as ta
from typing import Optional
from backend.strategies.base import BaseStrategy, Signal, StrategyResult, StrategyInfo, ParamDef

class HilegaMilegaStrategy(BaseStrategy):
    """
    Estratégia Hilega Milega (HM) - Chirag Rathore
    Foco: Sistema híbrido de RSI + Momentum + Volume para identificar tendências brutais.
    """
    
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="MR004",
            name="MR004 - Hilega Milega (RSI Hybrid)",
            description="Sistema híbrido que combina Força, Momentum e Volume. 'Acima de 50 é meu, abaixo de 50 é seu'.",
            recommended_timeframe="1H",
            tags=["RSI", "Momentum", "Hilega Milega", "Trend Following"],
            params={
                "rsi_period": ParamDef("int", 9, "Período do RSI", min=5, max=14),
                "ema_rsi": ParamDef("int", 3, "EMA do RSI (Linha Verde)", min=2, max=5),
                "wma_rsi": ParamDef("int", 21, "WMA do RSI (Linha Vermelha/Volume)", min=10, max=30),
            },
            criteria=[
                {"id": "c1", "label": "RSI > 50", "description": "RSI > 50"},
                {"id": "c2", "label": "Momentum OK", "description": "Momentum OK"},
                {"id": "c3", "label": "Volume OK", "description": "Volume OK"},
                {"id": "c4", "label": "Gap/Explosão OK", "description": "Gap/Explosão OK"},
            ],
        )

    def __init__(self):
        self.params = {
            "rsi_period": 9,
            "ema_rsi": 3,
            "wma_rsi": 21
        }

    def set_params(self, params: dict):
        self.params.update(params)

    def compute(self, df: list) -> Optional[StrategyResult]:
        if isinstance(df, list):
            import pandas as pd
            df = pd.DataFrame([vars(c) for c in df])

        if len(df) < 50:
            return StrategyResult(signal=Signal.HOLD, indicators={}, hold_reason="Aguardando dados históricos")

        # 1. Cálculo do Indicador Híbrido
        # RSI de 9 períodos
        rsi = ta.rsi(df['close'], length=self.params["rsi_period"])
        
        # EMA(3) aplicada sobre o RSI (Linha Verde)
        ema_rsi = ta.ema(rsi, length=self.params["ema_rsi"])
        
        # WMA(21) aplicada sobre o RSI (Linha Vermelha - Volume)
        wma_rsi = ta.wma(rsi, length=self.params["wma_rsi"])

        # 2. Valores atuais
        curr_rsi = rsi.iloc[-1]
        curr_ema = ema_rsi.iloc[-1]
        curr_wma = wma_rsi.iloc[-1]
        
        # 3. Contagem de Critérios (4 Critérios)
        criteria_total = 4
        criteria_met = 0
        
        signal = Signal.HOLD
        reason = "Aguardando confluência Hilega Milega"

        # Regra de Ouro: RSI > 50 (Bullish) ou < 50 (Bearish)
        trend_bull = curr_rsi > 50
        trend_bear = curr_rsi < 50

        # Lógica de Cruzamento (Linha Verde > Linha Vermelha)
        cross_bull = curr_ema > curr_wma
        cross_bear = curr_ema < curr_wma

        # Lógica de Volume (Linha Vermelha em relação ao RSI)
        vol_ok_bull = curr_wma < curr_rsi  # Volume confirmando força de alta
        vol_ok_bear = curr_wma > curr_rsi  # Volume confirmando força de baixa

        # Lógica de Gap (Momentum Forte)
        dist_ema_wma = abs(curr_ema - curr_wma)
        momentum_ok = dist_ema_wma > 2.0 # Gap mínimo para evitar sinais falsos

        # --- Avaliação LONG ---
        if trend_bull:
            criteria_met += 1 # C1: RSI > 50
            if cross_bull:
                criteria_met += 1 # C2: Momentum OK
            if vol_ok_bull:
                criteria_met += 1 # C3: Volume OK
            if momentum_ok:
                criteria_met += 1 # C4: Gap/Explosão OK
                
            if criteria_met == 4:
                signal = Signal.BUY
                reason = "Gatilho Hilega Milega LONG: Tendência Brutal Iniciada"

        # --- Avaliação SHORT ---
        elif trend_bear:
            criteria_met += 1 # C1: RSI < 50
            if cross_bear:
                criteria_met += 1 # C2: Momentum OK
            if vol_ok_bear:
                criteria_met += 1 # C3: Volume OK
            if momentum_ok:
                criteria_met += 1 # C4: Gap/Explosão OK
                
            if criteria_met == 4:
                signal = Signal.SELL
                reason = "Gatilho Hilega Milega SHORT: Tendência Brutal Iniciada"

        # 4. Indicadores para o Frontend
        indicators = {
            "rsi": round(curr_rsi, 2),
            "ema_rsi": round(curr_ema, 2),
            "wma_rsi": round(curr_wma, 2),
            "dist_hm": round(dist_ema_wma, 2),
            "close": round(float(df["close"].iloc[-1]), 6),
        }
        metadata = {}
        if signal in (Signal.BUY, Signal.SELL):
            close = float(df["close"].iloc[-1])
            atr = ta.atr(df["high"], df["low"], df["close"], length=14)
            atr_val = float(atr.iloc[-1]) if atr is not None and not pd.isna(atr.iloc[-1]) else close * 0.01
            sl_dist = atr_val * 2.0
            if signal == Signal.BUY:
                sl_price = close - sl_dist
                tp1_price = close + sl_dist * 2.0
            else:
                sl_price = close + sl_dist
                tp1_price = close - sl_dist * 2.0
            metadata = {
                "sl_price": round(sl_price, 6),
                "tp1_price": round(tp1_price, 6),
                "sl_pct": round(sl_dist / close, 5),
                "tp1_pct": round(sl_dist * 2.0 / close, 5),
                "ts_pct": round(sl_dist * 2.0 / close, 5),
            }
        
        return StrategyResult(
            signal=signal,
            indicators=indicators,
            metadata=metadata,
            hold_reason=reason,
            criteria_met=criteria_met,
            criteria_total=criteria_total
        )
