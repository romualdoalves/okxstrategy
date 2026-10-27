"""SC005 - Abertura Diária Cripto
Estratégia de momentum que opera a abertura do dia cripto (00:00 UTC) no gráfico de 15 minutos.
Gerado e Otimizado pela Fábrica de Estratégias
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class AberturaDiáriaCriptoStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="SC005",
            name="SC005 - Abertura Diária Cripto",
            description="Estratégia de momentum que opera a abertura do dia cripto (00:00 UTC) no gráfico de 15 minutos. Entra na direção do primeiro candle de 15 minutos após as 21:00 BRT, desde que o corpo do candle supere uma amplitude mínima percentual. Utiliza stops e alvos percentuais fixos com relação 1:1.",
            tags=["momentum", "intraday", "breakout"],
            recommended_timeframe="15m",
            criteria=[
                {"id": "c1_horario", "label": "C1 Horário", "description": "Candle atual corresponde ao enésimo candle após o horário de abertura definido"},
                {"id": "c2_dia", "label": "C2 Dia da Semana", "description": "Dia da semana está habilitado nos parâmetros"},
                {"id": "c3_amplitude", "label": "C3 Amplitude Mínima", "description": "Corpo do candle (em %) >= amplitude_minima_pct"},
                {"id": "c4_direcao", "label": "C4 Direção", "description": "Close > Open para compra; Close < Open para venda"},
            ],
            params={
                "hora_abertura": ParamDef(type="int", default=2100, min=0, max=2359, step=100, description="Horário de abertura do dia cripto em HHMM (BRT). Ex: 2100 = 21:00 BRT (00:00 UTC)"),
                "candle_avaliado": ParamDef(type="int", default=1, min=1, max=10, step=1, description="Número do candle de 15 minutos após a abertura a ser avaliado para entrada"),
                "amplitude_minima_pct": ParamDef(type="float", default=0.2, min=0, max=5, step=0.1, description="Corpo mínimo do candle em porcentagem para considerar entrada"),
                "alvo_percentual": ParamDef(type="float", default=0.5, min=0.1, max=5, step=0.1, description="Take profit percentual sobre o preço de entrada"),
                "stop_percentual": ParamDef(type="float", default=0.5, min=0.1, max=5, step=0.1, description="Stop loss percentual sobre o preço de entrada"),
                "segunda": ParamDef(type="bool", default=True, description="Permitir operar na segunda-feira"),
                "terca": ParamDef(type="bool", default=True, description="Permitir operar na terça-feira"),
                "quarta": ParamDef(type="bool", default=True, description="Permitir operar na quarta-feira"),
                "quinta": ParamDef(type="bool", default=True, description="Permitir operar na quinta-feira"),
                "sexta": ParamDef(type="bool", default=True, description="Permitir operar na sexta-feira"),
                "sabado": ParamDef(type="bool", default=False, description="Permitir operar no sábado"),
                "domingo": ParamDef(type="bool", default=False, description="Permitir operar no domingo"),
                "atr_period": ParamDef(type="int", default=14, min=5, max=50, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2.0, min=0.5, max=5.0, step=0.1, description="Multiplicador ATR para Stop Loss (não usado diretamente, mas obrigatório)"),
                "tp1_rr": ParamDef(type="float", default=2.0, min=0.5, max=5.0, step=0.1, description="Relação risco-retorno do TP1 (não usado diretamente, mas obrigatório)"),
                "ts_mult": ParamDef(type="float", default=3.0, min=0.5, max=5.0, step=0.1, description="Multiplicador ATR para Trailing Stop após TP1 (não usado diretamente, mas obrigatório)"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.hora_abertura = p["hora_abertura"].default
        self.candle_avaliado = p["candle_avaliado"].default
        self.amplitude_minima_pct = p["amplitude_minima_pct"].default
        self.alvo_percentual = p["alvo_percentual"].default
        self.stop_percentual = p["stop_percentual"].default
        self.segunda = p["segunda"].default
        self.terca = p["terca"].default
        self.quarta = p["quarta"].default
        self.quinta = p["quinta"].default
        self.sexta = p["sexta"].default
        self.sabado = p["sabado"].default
        self.domingo = p["domingo"].default
        self.atr_period = p["atr_period"].default
        self.sl_mult = p["sl_mult"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles, context=None):
        min_c = max(self.atr_period + 5, 20)
        if len(candles) < min_c:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_c} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "open": float(candles[-1].open), "body_pct": 0.0, "atr": 0.0},
            )

        # Otimização de Performance: Fatiar apenas as velas necessárias para o cálculo do ATR
        candles_necessarios = candles[-min_c:]
        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close, "volume": c.volume, "open": c.open, "time": c.epoch} for c in candles_necessarios])
        
        atr_s = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        if atr_s is None or atr_s.empty:
            return None

        atr = float(atr_s.iloc[-1])
        close = float(candles[-1].close)
        open_price = float(candles[-1].open)
        body_pct = abs(close - open_price) / open_price * 100.0

        # CORREÇÃO CRÍTICA: Tratamento seguro do tempo de c.epoch (CandleBar não tem .time) (Suporta int, float e datetime)
        raw_time = candles[-1].epoch
        try:
            # Caso seja int/float (Unix timestamp), assume milissegundos se for muito grande
            if isinstance(raw_time, (int, float)):
                unit = "ms" if raw_time > 5000000000 else "s"
                current_time = pd.to_datetime(raw_time, unit=unit)
            else:
                current_time = pd.to_datetime(raw_time)
        except Exception:
            current_time = raw_time  # Fallback caso já seja um objeto com propriedades datetime

        current_hour = current_time.hour
        current_minute = current_time.minute

        hora_abertura_hh = self.hora_abertura // 100
        hora_abertura_mm = self.hora_abertura % 100

        # Cálculo dinâmico do índice do candle a partir da hora de abertura (Base 15 minutos)
        minutos_desde_abertura = (current_hour * 60 + current_minute) - (hora_abertura_hh * 60 + hora_abertura_mm)
        
        # Tratar o cruzamento da meia-noite
        if minutos_desde_abertura < 0:
            minutos_desde_abertura += 1440

        # Identifica numericamente qual candle de 15m estamos processando (1, 2, 3...)
        indice_candle_atual = (minutos_desde_abertura // 15) + 1
        
        # Validação do parâmetro do usuário
        is_target_candle = (indice_candle_atual == self.candle_avaliado)

        # Verificar dia da semana
        weekday = current_time.weekday()  # 0=segunda, 6=domingo
        dia_permitido = False
        if weekday == 0 and self.segunda:
            dia_permitido = True
        elif weekday == 1 and self.terca:
            dia_permitido = True
        elif weekday == 2 and self.quarta:
            dia_permitido = True
        elif weekday == 3 and self.quinta:
            dia_permitido = True
        elif weekday == 4 and self.sexta:
            dia_permitido = True
        elif weekday == 5 and self.sabado:
            dia_permitido = True
        elif weekday == 6 and self.domingo:
            dia_permitido = True

        criteria_met = 0
        criteria_total = 4

        # C1: Horário / Índice do Candle
        if is_target_candle:
            criteria_met += 1

        # C2: Dia da semana
        if dia_permitido:
            criteria_met += 1

        # C3: Amplitude mínima
        if body_pct >= self.amplitude_minima_pct:
            criteria_met += 1

        # C4: Direção
        if close != open_price:
            criteria_met += 1

        # Determinar sinal de execução
        signal = Signal.HOLD
        hold_reason = ""

        if is_target_candle and dia_permitido and body_pct >= self.amplitude_minima_pct:
            if close > open_price:
                signal = Signal.BUY
            elif close < open_price:
                signal = Signal.SELL
            else:
                hold_reason = "Candle sem direção definida (close == open)"
        else:
            if not is_target_candle:
                hold_reason = f"Aguardando a janela do candle alvo (Atual: {indice_candle_atual}, Alvo: {self.candle_avaliado})"
            elif not dia_permitido:
                hold_reason = f"Dia da semana ({weekday}) desabilitado nos parâmetros"
            elif body_pct < self.amplitude_minima_pct:
                hold_reason = f"Amplitude de corpo insuficiente ({round(body_pct, 2)}% < {self.amplitude_minima_pct}%)"

        # Calcular alvos e stops fixos baseados em porcentagem
        if signal == Signal.BUY:
            sl_price = round(close * (1 - self.stop_percentual / 100.0), 4)
            tp1_price = round(close * (1 + self.alvo_percentual / 100.0), 4)
        elif signal == Signal.SELL:
            sl_price = round(close * (1 + self.stop_percentual / 100.0), 4)
            tp1_price = round(close * (1 - self.alvo_percentual / 100.0), 4)
        else:
            sl_price = 0.0
            tp1_price = 0.0

        sl_pct = self.stop_percentual / 100.0
        tp1_pct = self.alvo_percentual / 100.0
        ts_pct = round(atr * self.ts_mult / close, 5)

        return StrategyResult(
            signal=signal,
            hold_reason=hold_reason,
            indicators={
                "close": round(close, 4),
                "open": round(open_price, 4),
                "body_pct": round(body_pct, 4),
                "atr": round(atr, 6),
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(sl_pct, 5),
                "tp1_pct": round(tp1_pct, 5),
                "ts_pct": ts_pct,
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )