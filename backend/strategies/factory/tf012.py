from typing import Optional
from backend.strategies.base import BaseStrategy, Signal, StrategyResult, StrategyInfo, ParamDef

class LiquidityClustersStrategy(BaseStrategy):
    """
    Estratégia Liquidity Clusters Magnitude (Baseada em LuxAlgo / Setup Certo Trades)
    Foco: Rejeição de preço através da análise de magnitude de pavios e EMA 200.
    """
    
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF012",
            name="TF012 - Liquidity Clusters Magnitude",
            description="Identifica zonas de rejeição e liquidez defendida usando a magnitude dos pavios (Wicks) filtrada pela EMA 200.",
            recommended_timeframe="5m",
            tags=["Liquidez", "LuxAlgo", "Trend Following", "Scalping"],
            params={
                "ema_period": ParamDef("int", 200, "Período da EMA de tendência principal", min=50, max=500),
                "magnitude_length": ParamDef("int", 14, "Janela para medir a magnitude das rejeições (ondas)", min=5, max=50),
                "smoothing_length": ParamDef("int", 9, "Suavização da Trend Metric (linha azul)", min=3, max=30),
                "threshold_extreme": ParamDef("float", 1.5, "Multiplicador para identificar ondas expressivas (Extremos)", min=1.0, max=5.0, step=0.1),
            },
            criteria=[
                {"id": "c1", "label": "Filtro EMA 200 OK", "description": "Filtro EMA 200 OK"},
                {"id": "c2", "label": "Extremo de Liquidez OK", "description": "Extremo de Liquidez OK"},
                {"id": "c3", "label": "Filtro EMA 200 OK", "description": "Filtro EMA 200 OK"},
                {"id": "c4", "label": "Extremo de Liquidez OK", "description": "Extremo de Liquidez OK"},
            ],
        )

    def __init__(self):
        # Valores padrão
        self.params = {
            "ema_period": 200,
            "magnitude_length": 14,
            "smoothing_length": 9,
            "threshold_extreme": 1.5
        }

    def set_params(self, params: dict):
        self.params.update(params)

    def compute(self, df: list) -> Optional[StrategyResult]:
        # Converte lista de candles para DataFrame se necessário (compatibilidade com bot_manager)
        if isinstance(df, list):
            import pandas as pd
            df = pd.DataFrame([vars(c) for c in df])

        if len(df) < 250:
            return StrategyResult(signal=Signal.HOLD, indicators={}, hold_reason="Aguardando dados históricos (EMA 200)")

        # 1. Indicadores Base: EMA 200
        ema200 = df['close'].ewm(span=self.params["ema_period"], adjust=False).mean()
        last_close = df['close'].iloc[-1]
        last_ema = ema200.iloc[-1]
        
        # 2. Lógica Liquidity Magnitude (Pavios)
        # Pavio Superior (Bearish) e Pavio Inferior (Bullish)
        df['upper_wick'] = df['high'] - df[['open', 'close']].max(axis=1)
        df['lower_wick'] = df[['open', 'close']].min(axis=1) - df['low']
        
        # Magnitude (Ondas) - Usamos EMA da magnitude dos pavios
        bull_mag = df['lower_wick'].ewm(span=self.params["magnitude_length"], adjust=False).mean()
        bear_mag = df['upper_wick'].ewm(span=self.params["magnitude_length"], adjust=False).mean()
        
        # Trend Metric (Linha Azul) - Diferença suavizada
        trend_metric = (bull_mag - bear_mag).ewm(span=self.params["smoothing_length"], adjust=False).mean()
        
        # Normalização simples para identificar extremos (usando desvio padrão)
        std_metric = trend_metric.rolling(window=50).std()
        upper_band = std_metric * self.params["threshold_extreme"]
        lower_band = -std_metric * self.params["threshold_extreme"]
        
        curr_trend = trend_metric.iloc[-1]
        prev_trend = trend_metric.iloc[-2]
        
        # 3. Identificação de Tendência e Extremos
        trend_long = last_close > last_ema
        trend_short = last_close < last_ema
        
        # Verificamos se houve um extremo recente (nas últimas 10 velas)
        recent_bull_extreme = (trend_metric.iloc[-10:-1] < lower_band.iloc[-10:-1]).any()
        recent_bear_extreme = (trend_metric.iloc[-10:-1] > upper_band.iloc[-10:-1]).any()

        # Contagem de critérios para o Dashboard
        criteria_total = 3
        criteria_met = 0
        
        # Lógica de critérios (Exemplo para Long)
        if trend_long:
            criteria_met += 1 # Filtro EMA 200 OK
            if recent_bull_extreme:
                criteria_met += 1 # Extremo de Liquidez OK
                # Gatilho é o último passo
        elif trend_short:
            criteria_met += 1 # Filtro EMA 200 OK
            if recent_bear_extreme:
                criteria_met += 1 # Extremo de Liquidez OK

        signal = Signal.HOLD
        reason = "Aguardando gatilho de Liquidez"
        
        # 4. Regras de Entrada (Conforme Setup Certo Trades)
        if trend_long and recent_bull_extreme:
            if prev_trend < 0 and curr_trend >= 0:
                signal = Signal.BUY
                criteria_met = 3 # Todos os critérios atingidos
                reason = "Gatilho Long: Rejeição Bullish Detectada + Cruzamento Trend Metric"
            else:
                reason = "Trend Long: Aguardando cruzamento da Trend Metric (azul) para cima"
        
        elif trend_short and recent_bear_extreme:
            if prev_trend > 0 and curr_trend <= 0:
                signal = Signal.SELL
                criteria_met = 3 # Todos os critérios atingidos
                reason = "Gatilho Short: Rejeição Bearish Detectada + Cruzamento Trend Metric"
            else:
                reason = "Trend Short: Aguardando cruzamento da Trend Metric (azul) para baixo"

        # 5. Indicadores para o Frontend
        indicators = {
            "ema200": round(last_ema, 6),
            "trend_metric": round(curr_trend, 6),
            "extreme_high": round(upper_band.iloc[-1], 6),
            "extreme_low": round(lower_band.iloc[-1], 6)
        }
        
        return StrategyResult(
            signal=signal, 
            indicators=indicators, 
            hold_reason=reason,
            criteria_met=criteria_met,
            criteria_total=criteria_total
        )
