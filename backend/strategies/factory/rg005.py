"""Estratégia RG005 - Markov Regime Transition
Gerado pela Fábrica de Estratégias
Data de criação: 2024-01-01
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class MarkovRegimeTransitionStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="RG005",
            name="RG005 - Markov Regime Transition",
            description="Estratégia baseada em Cadeias de Markov para classificar o regime de mercado (Bull, Sideways, Bear) usando retornos de 20 dias. Calcula matriz de transição de estados para gerar sinal de direção (P(Bull) - P(Bear)) com dimensionamento proporcional à confiança. Inclui confirmação via Hidden Markov Model para alinhamento de estados.",
            tags=["statistical", "regime", "swing"],
            recommended_timeframe="1d",
            criteria=[
                {"id": "c1_state", "label": "C1 Estado Atual", "description": "Estado atual não é Sideways (bull ou bear)"},
                {"id": "c2_signal", "label": "C2 Sinal Direcional", "description": "Sinal (prob_bull - prob_bear) > 0 para compra ou < 0 para venda"},
                {"id": "c3_confidence", "label": "C3 Confiança Mínima", "description": "Confiança (|signal|) >= 0.2 (20%)"},
                {"id": "c4_hmm_confirm", "label": "C4 Confirmação HMM", "description": "Estado do Hidden Markov Model alinhado com o estado atual"},
            ],
            params={
                "lookback": ParamDef(type="int", default=20, min=10, max=50, step=1, description="Período de lookback para calcular retorno e definir estado"),
                "bull_threshold": ParamDef(type="float", default=5.0, min=1.0, max=20.0, step=0.5, description="Retorno percentual mínimo para classificar como Bull"),
                "bear_threshold": ParamDef(type="float", default=-5.0, min=-20.0, max=-1.0, step=0.5, description="Retorno percentual máximo para classificar como Bear"),
                "atr_period": ParamDef(type="int", default=14, min=7, max=30, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Trailing Stop após TP1"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.lookback = p["lookback"].default
        self.bull_threshold = p["bull_threshold"].default
        self.bear_threshold = p["bear_threshold"].default
        self.atr_period = p["atr_period"].default
        self.sl_mult = p["sl_mult"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def _classify_state(self, ret: float) -> int:
        """Classifica o estado com base no retorno percentual."""
        if ret >= self.bull_threshold:
            return 1  # Bull
        elif ret <= self.bear_threshold:
            return -1  # Bear
        else:
            return 0  # Sideways

    def _compute_transition_matrix(self, states: list) -> dict:
        """Calcula a matriz de transição de estados."""
        n = len(states)
        if n < 2:
            return {"p_bull": 0.0, "p_bear": 0.0, "p_sideways": 0.0}
        
        # Contagem de transições a partir do último estado
        last_state = states[-1]
        counts = {1: 0, -1: 0, 0: 0}
        total = 0
        
        for i in range(1, n):
            if states[i-1] == last_state:
                counts[states[i]] = counts.get(states[i], 0) + 1
                total += 1
        
        if total == 0:
            return {"p_bull": 0.0, "p_bear": 0.0, "p_sideways": 0.0}
        
        return {
            "p_bull": counts.get(1, 0) / total,
            "p_bear": counts.get(-1, 0) / total,
            "p_sideways": counts.get(0, 0) / total,
        }

    def _compute_hmm_state(self, returns: list) -> int:
        """Estima o estado do Hidden Markov Model usando EM simplificado."""
        n = len(returns)
        if n < 10:
            return 0
        
        # Inicialização: 3 estados com médias diferentes
        means = [0.0, 0.0, 0.0]
        stds = [1.0, 1.0, 1.0]
        
        # Inicialização k-means-like
        sorted_returns = sorted(returns)
        n_per_group = n // 3
        for i in range(3):
            start = i * n_per_group
            end = start + n_per_group if i < 2 else n
            group = sorted_returns[start:end]
            if group:
                means[i] = sum(group) / len(group)
                stds[i] = math.sqrt(sum((x - means[i]) ** 2 for x in group) / len(group)) if len(group) > 1 else 1.0
        
        # EM por 10 iterações
        for _ in range(10):
            # E-step: calcular responsabilidades
            responsibilities = []
            for ret in returns:
                probs = []
                for i in range(3):
                    if stds[i] > 0:
                        prob = math.exp(-0.5 * ((ret - means[i]) / stds[i]) ** 2) / (stds[i] * math.sqrt(2 * math.pi))
                    else:
                        prob = 1.0 if ret == means[i] else 0.0
                    probs.append(prob)
                total_prob = sum(probs)
                if total_prob > 0:
                    responsibilities.append([p / total_prob for p in probs])
                else:
                    responsibilities.append([1.0 / 3] * 3)
            
            # M-step: atualizar parâmetros
            for i in range(3):
                total_resp = sum(r[i] for r in responsibilities)
                if total_resp > 0:
                    means[i] = sum(r[i] * ret for r, ret in zip(responsibilities, returns)) / total_resp
                    variance = sum(r[i] * (ret - means[i]) ** 2 for r, ret in zip(responsibilities, returns)) / total_resp
                    stds[i] = math.sqrt(variance) if variance > 0 else 1.0
        
        # Classificar o último retorno
        last_ret = returns[-1]
        probs = []
        for i in range(3):
            if stds[i] > 0:
                prob = math.exp(-0.5 * ((last_ret - means[i]) / stds[i]) ** 2) / (stds[i] * math.sqrt(2 * math.pi))
            else:
                prob = 1.0 if last_ret == means[i] else 0.0
            probs.append(prob)
        
        # Mapear estados HMM para estados de regime
        # Ordenar por média: menor = Bear, médio = Sideways, maior = Bull
        sorted_indices = sorted(range(3), key=lambda i: means[i])
        hmm_state_map = {sorted_indices[0]: -1, sorted_indices[1]: 0, sorted_indices[2]: 1}
        
        best_state = max(range(3), key=lambda i: probs[i])
        return hmm_state_map[best_state]

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles, context=None):
        min_candles = max(self.lookback * 3, 60)
        if len(candles) < min_candles:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_candles} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "atr": 0.0, "state": 0.0, "prob_bull": 0.0, "prob_bear": 0.0, "signal": 0.0, "confidence": 0.0},
            )

        # Preparar dados
        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles])
        closes = df["close"].values
        close_current = float(closes[-1])

        # Calcular ATR
        atr_series = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        if atr_series is None:
            return None
        atr = float(atr_series.iloc[-1])

        # Calcular retornos de lookback dias
        n = len(closes)
        returns = []
        states = []
        
        # Calcular retornos em blocos de lookback dias (sem sobreposição)
        for i in range(self.lookback, n, self.lookback):
            if i < n:
                ret = (closes[i] - closes[i - self.lookback]) / closes[i - self.lookback] * 100
                returns.append(ret)
                states.append(self._classify_state(ret))
        
        # Último retorno (pode ser parcial)
        if n >= self.lookback:
            last_ret = (closes[-1] - closes[-self.lookback]) / closes[-self.lookback] * 100
            returns.append(last_ret)
            states.append(self._classify_state(last_ret))
        
        if len(states) < 2:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Aquecendo — histórico insuficiente para calcular transições",
                indicators={"close": close_current, "atr": round(atr, 6), "state": 0.0, "prob_bull": 0.0, "prob_bear": 0.0, "signal": 0.0, "confidence": 0.0},
            )

        # Estado atual
        current_state = states[-1]

        # Calcular matriz de transição
        transition = self._compute_transition_matrix(states)
        prob_bull = transition["p_bull"]
        prob_bear = transition["p_bear"]
        
        # Sinal = P(Bull) - P(Bear)
        signal_value = prob_bull - prob_bear
        confidence = abs(signal_value)

        # Calcular HMM state
        hmm_state = self._compute_hmm_state(returns)

        # Critérios
        criteria_met = 0
        criteria_total=4

        # C1: Estado atual não é Sideways
        if current_state != 0:
            criteria_met += 1

        # C2: Sinal direcional
        if signal_value > 0 or signal_value < 0:
            criteria_met += 1

        # C3: Confiança mínima >= 0.2
        if confidence >= 0.2:
            criteria_met += 1

        # C4: HMM confirma estado
        if hmm_state == current_state:
            criteria_met += 1

        # Decisão de entrada
        signal = Signal.HOLD
        hold_reason = ""

        if current_state == 1 and signal_value > 0 and confidence >= 0.2 and hmm_state == 1:
            signal = Signal.BUY
        elif current_state == -1 and signal_value < 0 and confidence >= 0.2 and hmm_state == -1:
            signal = Signal.SELL
        else:
            if current_state == 0:
                hold_reason = "Estado atual é Sideways"
            elif confidence < 0.2:
                hold_reason = "Sinal fraco (confiança < 20%)"
            elif hmm_state != current_state:
                hold_reason = "HMM não confirma estado"
            else:
                hold_reason = "Mercado sem direção clara"

        # Calcular SL e TP
        sl_dist = atr * self.sl_mult
        if signal == Signal.BUY:
            sl_price = round(close_current - sl_dist, 4)
            tp1_price = round(close_current + sl_dist * self.tp1_rr, 4)
        elif signal == Signal.SELL:
            sl_price = round(close_current + sl_dist, 4)
            tp1_price = round(close_current - sl_dist * self.tp1_rr, 4)
        else:
            sl_price = 0.0
            tp1_price = 0.0

        return StrategyResult(
            signal=signal,
            hold_reason=hold_reason,
            indicators={
                "close": round(close_current, 4),
                "atr": round(atr, 6),
                "state": float(current_state),
                "prob_bull": round(prob_bull, 4),
                "prob_bear": round(prob_bear, 4),
                "signal": round(signal_value, 4),
                "confidence": round(confidence, 4),
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(sl_dist / close_current, 5) if close_current > 0 else 0.0,
                "tp1_pct": round(sl_dist * self.tp1_rr / close_current, 5) if close_current > 0 else 0.0,
                "ts_pct": round(atr * self.ts_mult / close_current, 5) if close_current > 0 else 0.0,
            },
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )