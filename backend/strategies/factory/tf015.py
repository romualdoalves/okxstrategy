"""Estratégia TF015 - EMA Deslocada + MACD
Gerado pela Fábrica de Estratégias
Data de criação: 2025-01-01
Revisado: 2026-06-17
"""

from __future__ import annotations
from typing import Optional
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class EmaDeslocadaMacdStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="TF015",
            name="TF015 - EMA Deslocada + MACD",
            description="Estratégia de day trade baseada no cruzamento de médias móveis exponenciais deslocadas, com confirmação de dois candles consecutivos e retorno à média não deslocada. Utiliza MACD como filtro de tendência.",
            tags=["trend", "momentum", "intraday"],
            recommended_timeframe="5m",
            criteria=[
                {"id": "c1_cross", "label": "C1 Cruzamento", "description": "EMA deslocada cruzou a EMA base na direção correta (linhas posicionadas para a tendência)"},
                {"id": "c2_candles", "label": "C2 Confirmação", "description": "Mínimo de N candles consecutivos de alta ou baixa recentes"},
                {"id": "c3_retrace", "label": "C3 Retorno à Média", "description": "Preço atual retorna e toca a EMA base com MACD a favor"},
            ],
            params={
                "ema_period": ParamDef(type="int", default=9, min=5, max=20, step=1, description="Período da EMA base e deslocada"),
                "ema_shift": ParamDef(type="int", default=1, min=1, max=3, step=1, description="Deslocamento para frente visual (shift positivo no pandas)"),
                "macd_fast": ParamDef(type="int", default=12, min=5, max=30, step=1, description="Período rápido do MACD"),
                "macd_slow": ParamDef(type="int", default=26, min=10, max=50, step=1, description="Período lento do MACD"),
                "macd_signal": ParamDef(type="int", default=9, min=5, max=20, step=1, description="Período do sinal do MACD"),
                "confirm_candles": ParamDef(type="int", default=2, min=1, max=5, step=1, description="Número de candles consecutivos de confirmação"),
                "atr_period": ParamDef(type="int", default=14, min=7, max=30, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=1.0, min=0.5, max=3.0, step=0.5, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Trailing Stop"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.ema_period = p["ema_period"].default
        self.ema_shift = p["ema_shift"].default
        self.macd_fast = p["macd_fast"].default
        self.macd_slow = p["macd_slow"].default
        self.macd_signal = p["macd_signal"].default
        self.confirm_candles = p["confirm_candles"].default
        self.atr_period = p["atr_period"].default
        self.sl_mult = p["sl_mult"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        min_c = max(self.ema_period + self.ema_shift + 10, self.macd_slow + self.macd_signal + 10, 60)
        if len(candles) < min_c:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_c} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "ema_base": 0.0, "ema_shifted": 0.0, "macd_hist": 0.0, "macd_signal": 0.0, "atr": 0.0},
            )

        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles])

        # 1. Cálculos dos Indicadores
        ema_base_series = ta.ema(df["close"], length=self.ema_period)
        if ema_base_series is None: return None
        
        # CORREÇÃO: Shift positivo joga a média para frente no gráfico (atrasa cronologicamente no vetor)
        ema_shifted_series = ema_base_series.shift(self.ema_shift)
        
        macd_result = ta.macd(df["close"], fast=self.macd_fast, slow=self.macd_slow, signal=self.macd_signal)
        if macd_result is None: return None
        macd_hist_series = macd_result.iloc[:, 2]
        
        atr_series = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        if atr_series is None: return None

        # Valores atuais (último candle indexado como -1)
        close = float(df["close"].iloc[-1])
        high = float(df["high"].iloc[-1])
        low = float(df["low"].iloc[-1])
        ema_base = float(ema_base_series.iloc[-1])
        ema_shifted = float(ema_shifted_series.iloc[-1])
        macd_hist = float(macd_hist_series.iloc[-1])
        atr = float(atr_series.iloc[-1])

        criteria_met = 0
        criteria_total = 3

        # C1: Estrutura do Cruzamento (Valida o alinhamento de tendência)
        c1_buy = ema_shifted < ema_base
        c1_sell = ema_shifted > ema_base
        if c1_buy or c1_sell:
            criteria_met += 1

        # C2: Confirmação de Candles Consecutivos (procura nos últimos candles se houve o momentum necessário)
        # Verifica se nas barras recentes (ex: penúltima e antepenúltima) houve o padrão sequencial
        c2_buy = False
        c2_sell = False
        
        # Checa se houve fechamentos consecutivos de alta nas barras imediatamente anteriores
        if len(candles) >= self.confirm_candles + 1:
            buy_sequence = True
            sell_sequence = True
            for i in range(1, self.confirm_candles + 1):
                # Avalia as variações anteriores ao candle atual para rastrear o gatilho de ignição
                if df["close"].iloc[-i - 1] <= df["close"].iloc[-i - 2]:
                    buy_sequence = False
                if df["close"].iloc[-i - 1] >= df["close"].iloc[-i - 2]:
                    sell_sequence = False
            c2_buy = buy_sequence
            c2_sell = sell_sequence

        if c2_buy or c2_sell:
            criteria_met += 1

        # C3: Retorno à Média Base no candle atual com MACD validando o viés
        c3_buy = low <= ema_base and close > ema_base and macd_hist > 0
        c3_sell = high >= ema_base and close < ema_base and macd_hist < 0
        if c3_buy or c3_sell:
            criteria_met += 1

        # 2. Definição do Sinal Final
        signal = Signal.HOLD
        hold_reason = ""

        if c1_buy and c2_buy and c3_buy:
            signal = Signal.BUY
        elif c1_sell and c2_sell and c3_sell:
            signal = Signal.SELL
        else:
            if not (c1_buy or c1_sell):
                hold_reason = "Médiass móveis desalinhadas"
            elif not (c2_buy or c2_sell):
                hold_reason = "Ausência de candles de ignição/confirmação no histórico recente"
            elif not (c3_buy or c3_sell):
                hold_reason = "Preço atual não executa o teste/retorno à EMA base"
            else:
                hold_reason = "Filtro MACD barrou a entrada (Falta de momentum no Histograma)"

        # 3. Gerenciamento de Risco (SL e TP baseados no ATR)
        sl_dist = atr * self.sl_mult
        if signal == Signal.BUY:
            sl_price = round(close - sl_dist, 4)
            tp1_price = round(close + sl_dist * self.tp1_rr, 4)
        elif signal == Signal.SELL:
            sl_price = round(close + sl_dist, 4)
            tp1_price = round(close - sl_dist * self.tp1_rr, 4)
        else:
            sl_price = 0.0
            tp1_price = 0.0

        return StrategyResult(
            signal=signal,
            hold_reason=hold_reason,
            indicators={
                "close": round(close, 4),
                "ema_base": round(ema_base, 4),
                "ema_shifted": round(ema_shifted, 4),
                "macd_hist": round(macd_hist, 6),
                "macd_signal": round(float(macd_result.iloc[-1, 1]), 6),
                "atr": round(atr, 6),
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(sl_dist / close, 5) if close != 0 else 0.0,
                "tp1_pct": round(sl_dist * self.tp1_rr / close, 5) if close != 0 else 0.0,
                "ts_pct": round(atr * self.ts_mult / close, 5) if close != 0 else 0.0,
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )
