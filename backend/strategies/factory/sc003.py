import pandas as pd
import numpy as np
from typing import Optional, Any
from backend.strategies.base import BaseStrategy, StrategyInfo, ParamDef, StrategyResult, Signal

class WhaleFlowRegimeStrategy(BaseStrategy):
    """
    Whale Flow Regime Strategy
    Detecta fluxo institucional através de VWAP Bands, Delta de Volume e Clímax de Wyckoff.
    """

    def __init__(self):
        self.params = {
            "window": 20,
            "std_dev": 2.0,
            "vol_threshold": 1.5
        }

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="SC003",
            name="SC003 - Whale Flow Regime",
            description="Identifica rastros de baleias usando VWAP Bands (VAH/VAL) e Delta de Volume.",
            tags=["volume", "institutional", "order-flow"],
            criteria=[
                {"id": "c1_institutional_zone", "label": "C1 Zona Inst.", "description": "Preço em VAH/VAL ou zona institucional relevante."},
                {"id": "c2_delta", "label": "C2 Delta", "description": "Delta de volume aponta agressão direcional."},
                {"id": "c3_climax", "label": "C3 Clímax", "description": "Volume climático confirma fluxo de baleias."},
            ],
            params={
                "window": ParamDef("int", 20, "Janela para cálculo da VWAP e bandas.", 5, 100, 1),
                "std_dev": ParamDef("float", 2.0, "Desvios para as bandas (VAH/VAL).", 1.0, 4.0, 0.1),
                "vol_threshold": ParamDef("float", 1.5, "Multiplicador de volume para clímax.", 1.1, 5.0, 0.1),
            }
        )

    def set_params(self, params: dict) -> None:
        self.params.update(params)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        if len(candles) < self.params["window"] + 10:
            return None

        # Converter para DataFrame para cálculos vetoriais
        df = pd.DataFrame([c.__dict__ if hasattr(c, '__dict__') else c for c in candles])
        
        # 1. VWAP (Média Ponderada por Volume)
        tp = (df['high'] + df['low'] + df['close']) / 3
        df['pv'] = tp * df['volume']
        df['vwap'] = df['pv'].rolling(window=self.params["window"]).sum() / df['volume'].rolling(window=self.params["window"]).sum()
        
        # 2. Bandas de Desvio (Simulando VAH/VAL do Volume Profile)
        rolling_std = df['close'].rolling(window=self.params["window"]).std()
        df['vah'] = df['vwap'] + (rolling_std * self.params["std_dev"])
        df['val'] = df['vwap'] - (rolling_std * self.params["std_dev"])
        
        # 3. Volume Delta Estimado (Agressão)
        df['range'] = df['high'] - df['low']
        df['body_size'] = (df['close'] - df['open']).abs()
        df['close_loc'] = (df['close'] - df['low']) / (df['range'].replace(0, 0.0001))
        df['vol_ma'] = df['volume'].rolling(window=20).mean()
        df['vol_ratio'] = df['volume'] / df['vol_ma']
        
        # Delta: 1 (Compra Forte), -1 (Venda Forte), 0 (Neutro)
        df['delta'] = 0
        df.loc[(df['close_loc'] > 0.7) & (df['vol_ratio'] > 1.2), 'delta'] = 1
        df.loc[(df['close_loc'] < 0.3) & (df['vol_ratio'] > 1.2), 'delta'] = -1

        curr = df.iloc[-1]
        body_ma = df['body_size'].rolling(10).mean().iloc[-1]

        
        # ── Lógica de Sinais ──────────────────────────────────────────────────
        
        # Critérios:
        # C1: Preço em zona de exaustão (VAH/VAL)
        # C2: Volume Delta a favor da reversão
        # C3: Volume climático (Clímax de Wyckoff)
        # C4: Esforço vs Resultado (corpo pequeno com volume alto)
        
        is_fundo = curr['low'] < curr['val'] or curr['close'] < curr['val']
        is_topo  = curr['high'] > curr['vah'] or curr['close'] > curr['vah']
        vol_spike = curr['vol_ratio'] > self.params["vol_threshold"]
        buy_delta = curr['delta'] >= 0
        sell_delta = curr['delta'] <= 0
        
        signal = Signal.HOLD
        
        # COMPRA: Fundo da banda + Rejeição + Volume Climático
        if is_fundo and buy_delta and vol_spike:
            signal = Signal.BUY
            
        # VENDA: Topo da banda + Exaustão + Volume Climático
        elif is_topo and sell_delta and vol_spike:
            signal = Signal.SELL

        # ── Metadados para o UI ──────────────────────────────────────────────
        
        # Zone Label
        zone = "POC (Equilíbrio)"
        if is_topo: zone = "VAH (Topo)"
        elif is_fundo: zone = "VAL (Fundo)"

        indicators = {
            "vwap": float(curr['vwap']),
            "vah":  float(curr['vah']),
            "val":  float(curr['val']),
            "delta": float(curr['delta']),
            "vol_ratio": float(curr['vol_ratio'])
        }
        
        # Contador de critérios (Explainability)
        criteria_met = 0
        if is_fundo or is_topo: criteria_met += 1
        if vol_spike: criteria_met += 1
        if (signal == Signal.BUY and buy_delta) or (signal == Signal.SELL and sell_delta): criteria_met += 1
        if abs(curr['close_loc'] - 0.5) > 0.2: criteria_met += 1 # Rejeição de pavio
        
        return StrategyResult(
            signal=signal,
            criteria_met=min(criteria_met, 3),
            criteria_total=3,
            indicators=indicators,
            metadata={
                "zone": zone, 
                "absorption": 1 if (vol_spike and curr['body_size'] < body_ma) else 0
            }
        )
