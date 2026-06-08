import pandas as pd
import numpy as np
import pandas_ta as ta
from typing import Optional, List
from backend.strategies.base import BaseStrategy, Signal, StrategyResult, StrategyInfo, ParamDef

class AtemporalEventDrivenStrategy(BaseStrategy):
    """
    I004 - Atemporal Event-Driven Flow
    Foco: Dinâmica de causalidade do preço, agressão e liquidez.
    Usa blocos sintetizados baseados em ATR ao invés de tempo cronológico.
    """
    
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="IF003",
            name="IF003 - Atemporal Event-Driven Flow",
            description="Estratégia baseada em blocos atemporais (Range) e Volume Profile (POC). Foca em agressão institucional e zonas de liquidez.",
            recommended_timeframe="15m",
            tags=["Intelligence", "OrderFlow", "Atemporal", "POC", "ATR"],
            params={
                "atr_period": ParamDef("int", 14, "Período do ATR (Diário)", min=7, max=28),
                "atr_multiplier": ParamDef("float", 0.15, "Multiplicador do ATR para tamanho do bloco", min=0.05, max=0.5, step=0.01),
                "vol_window": ParamDef("int", 100, "Janela para cálculo do POC", min=50, max=500),
                "rr_ratio": ParamDef("float", 2.0, "Risco:Recompensa alvo", min=1.5, max=5.0, step=0.1),
            },
            criteria=[
                {"id": "c1", "label": "Proximidade do POC", "description": "Proximidade do POC (Zona de Valor)"},
                {"id": "c2", "label": "Reversão Atemporal", "description": "Reversão Atemporal"},
                {"id": "c3", "label": "Delta de Volume", "description": "Heurística de Delta de Volume (Urgência/Agressão)"},
                {"id": "c4", "label": "Confirmação de Direção", "description": "Confirmação de Direção (Fechamento forte)"},
            ],
        )

    def __init__(self):
        self.params = {
            "atr_period": 14,
            "atr_multiplier": 0.15,
            "vol_window": 100,
            "rr_ratio": 2.0
        }

    def set_params(self, params: dict):
        self.params.update(params)

    @classmethod
    def extra_timeframes(cls) -> List[str]:
        # Necessário para calcular o ATR Diário
        return ["1D"]

    def compute_with_context(self, candles: list, context: dict | None = None) -> Optional[StrategyResult]:
        if not candles or len(candles) < 20:
            return None
            
        # 1. Obter ATR Diário do contexto
        block_size = None
        if context and "extra_candles" in context and "1D" in context["extra_candles"]:
            daily_candles = context["extra_candles"]["1D"]
            if len(daily_candles) >= self.params["atr_period"]:
                df_daily = pd.DataFrame([vars(c) for c in daily_candles])
                atr_daily = ta.atr(df_daily['high'], df_daily['low'], df_daily['close'], length=self.params["atr_period"])
                if atr_daily is not None and not atr_daily.empty:
                    block_size = atr_daily.iloc[-1] * self.params["atr_multiplier"]

        # Fallback se não tiver dados diários
        if block_size is None or np.isnan(block_size):
            df_temp = pd.DataFrame([vars(c) for c in candles])
            atr_local = ta.atr(df_temp['high'], df_temp['low'], df_temp['close'], length=14)
            block_size = (atr_local.iloc[-1] if atr_local is not None else df_temp['close'].iloc[-1] * 0.01) * self.params["atr_multiplier"]

        # 2. Processar Candles em Blocos Atemporais (Sintetizador)
        # Vamos reconstruir os blocos a partir da lista de candles para garantir consistência
        blocks = []
        if candles:
            current_open = candles[0].open
            current_high = candles[0].high
            current_low = candles[0].low
            
            for c in candles:
                current_high = max(current_high, c.high)
                current_low = min(current_low, c.low)
                
                # Se o preço se moveu mais que um bloco
                diff_up = c.close - current_open
                diff_down = current_open - c.close
                
                if diff_up >= block_size:
                    blocks.append({'type': 'bull', 'open': current_open, 'close': current_open + block_size, 'vol': c.volume})
                    current_open = current_open + block_size
                    current_high = c.high
                    current_low = c.low
                elif diff_down >= block_size:
                    blocks.append({'type': 'bear', 'open': current_open, 'close': current_open - block_size, 'vol': c.volume})
                    current_open = current_open - block_size
                    current_high = c.high
                    current_low = c.low

        # 3. Cálculo do POC (Point of Control) na janela vol_window
        df = pd.DataFrame([vars(c) for c in candles])
        window = min(len(df), self.params["vol_window"])
        df_recent = df.iloc[-window:]
        
        # Simulação simples de Volume Profile
        price_min = df_recent['low'].min()
        price_max = df_recent['high'].max()
        bins = 20
        if price_max > price_min:
            hist, edges = np.histogram(df_recent['close'], bins=bins, weights=df_recent['volume'])
            poc_idx = np.argmax(hist)
            poc_price = (edges[poc_idx] + edges[poc_idx+1]) / 2
        else:
            poc_price = df_recent['close'].iloc[-1]

        # 4. Lógica de Gatilho
        # Precisamos de pelo menos 2 blocos atemporais para detectar reversão
        signal = Signal.HOLD
        reason = "Aguardando formação de blocos atemporais"
        criteria_met = 0
        criteria_total = 4
        
        near_poc = False
        is_reversal_long = False
        is_reversal_short = False
        high_vol = False
        strong_close_long = False
        strong_close_short = False
        
        last_price = candles[-1].close
        
        if len(blocks) >= 2:
            last_block = blocks[-1]
            prev_block = blocks[-2]
            
            # C1: Proximidade do POC (Zona de Valor)
            dist_poc = abs(last_price - poc_price) / poc_price if poc_price > 0 else 999.0
            near_poc = dist_poc < 0.005 # 0.5% de distância
            if near_poc: criteria_met += 1
            
            # C2: Reversão Atemporal
            is_reversal_long = (last_block['type'] == 'bull' and prev_block['type'] == 'bear')
            is_reversal_short = (last_block['type'] == 'bear' and prev_block['type'] == 'bull')
            if is_reversal_long or is_reversal_short: criteria_met += 1
            
            # C3: Heurística de Delta de Volume (Urgência/Agressão)
            # Volume do último candle acima da média dos últimos 20
            avg_vol = df['volume'].rolling(20).mean().iloc[-1]
            high_vol = candles[-1].volume > avg_vol
            if high_vol: criteria_met += 1
            
            # C4: Confirmação de Direção (Fechamento forte)
            # No LONG, fechamento no terço superior do candle
            candle_range = candles[-1].high - candles[-1].low
            if candle_range > 0:
                pos_pct = (candles[-1].close - candles[-1].low) / candle_range
                strong_close_long = pos_pct > 0.7
                strong_close_short = pos_pct < 0.3
                if (is_reversal_long and strong_close_long) or (is_reversal_short and strong_close_short):
                    criteria_met += 1
            
            # Gatilhos Finais
            if criteria_met >= 3:
                if is_reversal_long and near_poc:
                    signal = Signal.BUY
                    reason = "Gatilho I004 LONG: Reversão em Zona de Liquidez (POC)"
                elif is_reversal_short and near_poc:
                    signal = Signal.SELL
                    reason = "Gatilho I004 SHORT: Reversão em Zona de Liquidez (POC)"
                else:
                    reason = "Confluência detectada, aguardando toque preciso no POC ou reversão clara."
        
        # 5. Indicadores para o Frontend
        indicators = {
            "poc": round(poc_price, 2),
            "block_size": round(block_size, 2),
            "num_blocks": float(len(blocks)),
            "last_block_type": 1.0 if (len(blocks) > 0 and blocks[-1]['type'] == 'bull') else -1.0 if (len(blocks) > 0) else 0.0,
            "c1_ok": 1.0 if near_poc else 0.0,
            "c2_ok": 1.0 if (is_reversal_long or is_reversal_short) else 0.0,
            "c3_ok": 1.0 if high_vol else 0.0,
            "c4_ok": 1.0 if ((is_reversal_long and strong_close_long) or (is_reversal_short and strong_close_short)) else 0.0
        }

        # Metadata para ordens
        metadata = {}
        if signal != Signal.HOLD:
            sl = last_price - (block_size * 1.1) if signal == Signal.BUY else last_price + (block_size * 1.1)
            tp = last_price + (last_price - sl) * self.params["rr_ratio"]
            metadata = {
                "entry": round(last_price, 2),
                "stop": round(sl, 2),
                "tp1": round(tp, 2),
                "poc": round(poc_price, 2)
            }

        return StrategyResult(
            signal=signal,
            indicators=indicators,
            metadata=metadata,
            hold_reason=reason,
            criteria_met=criteria_met,
            criteria_total=criteria_total
        )

    def compute(self, candles: list) -> Optional[StrategyResult]:
        # Delega para a implementação com contexto
        return self.compute_with_context(candles)
