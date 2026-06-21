import pandas as pd
import numpy as np
import pandas_ta as ta
from typing import Optional
from backend.strategies.base import BaseStrategy, Signal, StrategyResult, StrategyInfo, ParamDef

class WarriorReversalStrategy(BaseStrategy):
    """
    Estratégia Warrior Trading Reversal (Ross Cameron)
    Foco: Reversão de exaustão quando o preço fecha totalmente fora das Bandas de Bollinger.
    """
    
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="MR003",
            name="MR003 - Warrior Trading Reversal",
            description="Estratégia de reversão do Ross Cameron. Opera a volta à média quando um candle fecha totalmente fora das bandas, com confirmação de rompimento do extremo.",
            recommended_timeframe="5m",
            tags=["Bollinger", "Reversion", "Ross Cameron", "Scalping"],
            params={
                "bb_length": ParamDef("int", 20, "Período das Bandas de Bollinger", min=10, max=50),
                "bb_std": ParamDef("float", 2.0, "Desvio Padrão das Bandas", min=1.5, max=3.0, step=0.1),
                "rsi_period": ParamDef("int", 14, "Período do RSI", min=7, max=30),
                "ema_fast": ParamDef("int", 9, "EMA rápida para primeiro alvo", min=5, max=15),
            },
            criteria=[
                {"id": "c1", "label": "Exaustão Detectada", "description": "Condição 1: Exaustão Detectada"},
                {"id": "c2", "label": "Filtro RSI", "description": "Filtro RSI (Opcional do Ross, mas ajuda muito)"},
                {"id": "c3", "label": "Filtro MACD", "description": "Filtro MACD (Perdendo força vendedora)"},
                {"id": "c4", "label": "Gatilho de Rompimento", "description": "Preço rompe o extremo da vela estendida"},
            ],
        )

    def __init__(self):
        self.params = {
            "bb_length": 20,
            "bb_std": 2.0,
            "rsi_period": 14,
            "ema_fast": 9
        }

    def set_params(self, params: dict):
        self.params.update(params)

    def compute(self, df: list) -> Optional[StrategyResult]:
        if isinstance(df, list):
            import pandas as pd
            df = pd.DataFrame([vars(c) for c in df])

        if len(df) < 50:
            return StrategyResult(signal=Signal.HOLD, indicators={}, hold_reason="Aguardando dados históricos")

        # 1. Cálculos dos Indicadores
        # Bandas de Bollinger
        bb = ta.bbands(df['close'], length=self.params["bb_length"], std=self.params["bb_std"])
        if bb is None: return None
        
        upper_band = bb.iloc[:, 2] # BBU_20_2.0
        lower_band = bb.iloc[:, 0] # BBL_20_2.0
        mid_band = bb.iloc[:, 1]   # BBM_20_2.0

        # Médias e VWAP
        ema9 = ta.ema(df['close'], length=self.params["ema_fast"])
        ema20 = ta.ema(df['close'], length=20)
        rsi = ta.rsi(df['close'], length=self.params["rsi_period"])
        
        # MACD
        macd_df = ta.macd(df['close'])
        macd_val = macd_df.iloc[:, 0] if macd_df is not None else pd.Series([0]*len(df))
        macd_sig = macd_df.iloc[:, 2] if macd_df is not None else pd.Series([0]*len(df))

        # Valores atuais e anteriores
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 2. Lógica de "Candle Fora da Banda" (Totalmente fora)
        # LONG: Mínima e Máxima do candle anterior abaixo da banda inferior
        outside_lower = prev['high'] < lower_band.iloc[-2]
        
        # SHORT: Mínima e Máxima do candle anterior acima da banda superior
        outside_upper = prev['low'] > upper_band.iloc[-2]

        # 3. Contagem de Critérios
        criteria_total = 4
        criteria_met = 0
        
        signal = Signal.HOLD
        reason = "Aguardando exaustão fora das bandas"

        # --- Lógica para COMPRA (LONG) ---
        if outside_lower:
            criteria_met += 1 # Condição 1: Exaustão Detectada
            
            # Filtro RSI (Opcional do Ross, mas ajuda muito)
            if rsi.iloc[-1] < 40: criteria_met += 1
            
            # Filtro MACD (Perdendo força vendedora)
            if macd_val.iloc[-1] > macd_val.iloc[-2]: criteria_met += 1
            
            # Gatilho: Preço atual rompe a máxima da vela que ficou fora
            if curr['close'] > prev['high']:
                signal = Signal.BUY
                criteria_met = 4
                reason = "Gatilho Long: Rompimento da máxima da vela estendida"
            else:
                reason = f"Alerta Long: Aguardando romper máxima de {prev['high']:.2f}"

        # --- Lógica para VENDA (SHORT) ---
        elif outside_upper:
            criteria_met += 1 # Condição 1: Exaustão Detectada
            
            # Filtro RSI
            if rsi.iloc[-1] > 60: criteria_met += 1
            
            # Filtro MACD (Perdendo força compradora)
            if macd_val.iloc[-1] < macd_val.iloc[-2]: criteria_met += 1
            
            # Gatilho: Preço atual rompe a mínima da vela que ficou fora
            if curr['close'] < prev['low']:
                signal = Signal.SELL
                criteria_met = 4
                reason = "Gatilho Short: Rompimento da mínima da vela estendida"
            else:
                reason = f"Alerta Short: Aguardando romper mínima de {prev['low']:.2f}"

        # 4. Indicadores para o Frontend
        indicators = {
            "bb_upper": round(upper_band.iloc[-1], 6),
            "bb_lower": round(lower_band.iloc[-1], 6),
            "bb_mid": round(mid_band.iloc[-1], 6),
            "ema9": round(ema9.iloc[-1], 6),
            "rsi": round(rsi.iloc[-1], 2),
            "outside_band": 1.0 if (outside_lower or outside_upper) else 0.0,
            "macd_delta": round(float(macd_val.iloc[-1] - macd_val.iloc[-2]), 6),
        }
        metadata = {}
        if signal in (Signal.BUY, Signal.SELL):
            entry = float(curr["close"])
            atr = ta.atr(df["high"], df["low"], df["close"], length=14)
            atr_val = float(atr.iloc[-1]) if atr is not None and not pd.isna(atr.iloc[-1]) else entry * 0.01
            sl_dist = atr_val * 2.0
            if signal == Signal.BUY:
                sl_price = entry - sl_dist
                tp1_price = entry + sl_dist * 2.0
            else:
                sl_price = entry + sl_dist
                tp1_price = entry - sl_dist * 2.0
            metadata = {
                "sl_price": round(sl_price, 6),
                "tp1_price": round(tp1_price, 6),
                "sl_pct": round(sl_dist / entry, 5),
                "tp1_pct": round(sl_dist * 2.0 / entry, 5),
                "ts_pct": round(sl_dist * 2.0 / entry, 5),
            }
        
        return StrategyResult(
            signal=signal,
            indicators=indicators,
            metadata=metadata,
            hold_reason=reason,
            criteria_met=criteria_met,
            criteria_total=criteria_total
        )
