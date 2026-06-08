import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, StrategyInfo, ParamDef, StrategyResult, Signal

class JoeDiNapoliStrategy(BaseStrategy):
    def __init__(self):
        self.ema_trend_len = 50

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF005",
            name="TF005 - Joe Di Napoli",
            description="Setup clássico que utiliza EMA 3 para capturar fundos ascendentes a favor da tendência primária.",
            params={
                "ema_trend_len": ParamDef("int", 50, "Períodos da EMA de Tendência (Contexto)", 20, 200, 1)
            },
            recommended_timeframe="15m",
            tags=["Básica", "Tendência", "Pullback"],
            criteria=[
                {"id": "c1", "label": "Tendência EMA", "description": "Tendência primária favorável"},
                {"id": "c2", "label": "Setup Formado", "description": "Padrão de pivôs identificado"},
                {"id": "c3", "label": "Gatilho Acionado", "description": "Rompeu nível do gatilho"},
            ],
        )

    def set_params(self, params: dict) -> None:
        self.ema_trend_len = int(params.get("ema_trend_len", 50))

    def compute(self, candles: list) -> StrategyResult | None:
        if len(candles) < self.ema_trend_len + 5:
            return None

        df = pd.DataFrame([c.__dict__ for c in candles])
        
        df['ema3'] = ta.ema(df['close'], length=3)
        df['ema_trend'] = ta.ema(df['close'], length=self.ema_trend_len)
        
        # slope
        df['ema3_slope'] = df['ema3'].diff()
        df['ema3_turn_up'] = (df['ema3_slope'] > 0) & (df['ema3_slope'].shift(1) <= 0)
        df['ema3_turn_down'] = (df['ema3_slope'] < 0) & (df['ema3_slope'].shift(1) >= 0)
        
        # Encontra as últimas 3 inflexões da EMA3
        pivots = []
        for i in range(len(df) - 1, max(0, len(df) - 30), -1):
            if df['ema3_turn_up'].iloc[i]:
                pivots.append({'idx': i, 'type': 'fundo', 'price': df['low'].iloc[i], 'high': df['high'].iloc[i], 'low': df['low'].iloc[i]})
            elif df['ema3_turn_down'].iloc[i]:
                pivots.append({'idx': i, 'type': 'topo', 'price': df['high'].iloc[i], 'high': df['high'].iloc[i], 'low': df['low'].iloc[i]})
            
            if len(pivots) == 3:
                break

        close = df['close'].iloc[-1]
        ema_trend = df['ema_trend'].iloc[-1]
        ema_trend_prev = df['ema_trend'].iloc[-2]
        
        trend_ok = 0
        if ema_trend > ema_trend_prev and close > ema_trend:
            trend_ok = 1
        elif ema_trend < ema_trend_prev and close < ema_trend:
            trend_ok = -1

        setup_formed = 0
        trigger_crossed = 0
        signal = Signal.HOLD
        hold_reason = "Aguardando padrão Joe Di Napoli (Pivôs na EMA 3)"

        if len(pivots) >= 3:
            p1, p2, p3 = pivots[0], pivots[1], pivots[2]
            
            # Setup de Compra: Fundo, Topo, Fundo (lembrando que pivots[0] é o mais recente)
            if p1['type'] == 'fundo' and p2['type'] == 'topo' and p3['type'] == 'fundo':
                fundo1_low = p3['low']
                fundo2_low = p1['low']
                trigger_high = p1['high']
                
                if fundo2_low > fundo1_low:
                    setup_formed = 1
                    if trend_ok == 1:
                        # Verifica se o preço não perdeu a mínima do Fundo 2 (invalida setup)
                        recent_low = df['low'].iloc[p1['idx']:].min()
                        if recent_low >= fundo2_low:
                            if close > trigger_high:
                                trigger_crossed = 1
                                signal = Signal.BUY
                                hold_reason = ""
                            else:
                                hold_reason = "Aguardando rompimento da máxima do Fundo 2"
                        else:
                            hold_reason = "Setup invalidado (perdeu Fundo 2 antes do gatilho)"
                    else:
                        hold_reason = "Tendência longa não autoriza compra"

            # Setup de Venda: Topo, Fundo, Topo
            elif p1['type'] == 'topo' and p2['type'] == 'fundo' and p3['type'] == 'topo':
                topo1_high = p3['high']
                topo2_high = p1['high']
                trigger_low = p1['low']
                
                if topo2_high < topo1_high:
                    setup_formed = -1
                    if trend_ok == -1:
                        recent_high = df['high'].iloc[p1['idx']:].max()
                        if recent_high <= topo2_high:
                            if close < trigger_low:
                                trigger_crossed = -1
                                signal = Signal.SELL
                                hold_reason = ""
                            else:
                                hold_reason = "Aguardando perda da mínima do Topo 2"
                        else:
                            hold_reason = "Setup invalidado (rompeu Topo 2 antes do gatilho)"
                    else:
                        hold_reason = "Tendência longa não autoriza venda"

        indicators = {
            'ema3': float(df['ema3'].iloc[-1]),
            'ema_trend': float(ema_trend),
            'trend_ok': trend_ok,
            'setup_formed': setup_formed,
            'trigger_crossed': trigger_crossed,
            'close': float(close)
        }

        return StrategyResult(
            signal=signal,
            indicators=indicators,
            hold_reason=hold_reason,
            criteria_met=abs(trend_ok) + abs(setup_formed) + abs(trigger_crossed),
            criteria_total=3
        )
