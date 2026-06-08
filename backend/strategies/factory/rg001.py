"""
strategies/markov_regime.py — Markov Hedge Fund Method

Detecta regime de mercado (Bull / Sideways / Bear) via cadeia de Markov
treinada no histórico de preços. Sinal direcional = P(Bull_próximo) − P(Bear_próximo).

Parâmetros do autor (Roan @RohOnChain / Lewis Jackson):
  window    = 20   — lookback para classificar cada barra
  threshold = 0.05 — fronteira ±5% Bull/Bear
  min_train = 252  — mínimo de barras rotuladas antes de operar

Referência: github.com/jackson-video-resources/markov-hedge-fund-method
"""

from __future__ import annotations
from typing import Optional

import numpy as np

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult

# Índices dos estados
BEAR, SIDE, BULL = 0, 1, 2
STATE_NAME = {BEAR: "Bear", SIDE: "Sideways", BULL: "Bull"}


class MarkovRegimeStrategy(BaseStrategy):

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="RG001",
            name="RG001 - Markov Regime (Hedge Fund Method)",
            description = (
                "Classifica cada barra como Bull, Sideways ou Bear usando retorno "
                "acumulado em janela móvel. Constrói uma matriz de transição 3×3 por "
                "máxima verossimilhança e projeta probabilidades do próximo estado via "
                "equações de Chapman-Kolmogorov. "
                "Sinal = P(Bull) − P(Bear) ∈ [−1, +1]. "
                "Entra LONG quando o regime favorece alta; sai quando o mercado vira Bear. "
                "Ideal como filtro de regime sobre estratégias técnicas existentes. "
                "A matriz de transição revela padrões invisíveis aos indicadores técnicos: "
                "em ativos tendenciais, o regime Bull raramente colapsa direto para Bear — "
                "quase sempre passa pelo Sideways primeiro, sinalizando uma janela de saída "
                "antes da reversão."
            ),
            recommended_timeframe = "1D",
            tags   = ["regime", "markov", "probabilistic", "hedge-fund", "filter"],
            criteria=[
                {"id": "c1_signal", "label": "C1 Sinal", "description": "Diferença P(Bull)-P(Bear) favorece direção."},
                {"id": "c2_regime", "label": "C2 Regime", "description": "Regime Markov não está contra a entrada."},
                {"id": "c3_persistence", "label": "C3 Persistência", "description": "Probabilidade de manter regime é suficiente."},
                {"id": "c4_stationary", "label": "C4 Estacionário", "description": "Distribuição estacionária favorece o lado da operação."},
            ],
            params = {
                "window": ParamDef(
                    type="int", default=20, min=5, max=60, step=1,
                    description="Lookback (barras) para classificar o regime de cada barra"),
                "threshold": ParamDef(
                    type="float", default=0.05, min=0.01, max=0.20, step=0.01,
                    description="Fronteira Bull/Bear: retorno > +threshold = Bull, < −threshold = Bear"),
                "min_train": ParamDef(
                    type="int", default=252, min=50, max=500, step=10,
                    description="Mínimo de barras rotuladas para treinar a matriz (252 ≈ 1 ano)"),
                "atr_mult_sl": ParamDef(
                    type="float", default=2.0, min=0.5, max=5.0, step=0.1,
                    description="Multiplicador ATR para Stop Loss"),
                "tp1_pct": ParamDef(
                    type="float", default=3.0, min=0.5, max=10.0, step=0.5,
                    description="Take Profit 1 (%)"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.window      = p["window"].default
        self.threshold   = p["threshold"].default
        self.min_train   = p["min_train"].default
        self.atr_mult_sl = p["atr_mult_sl"].default
        self.tp1_pct     = p["tp1_pct"].default

    def set_params(self, params: dict) -> None:
        self.window      = int(params.get("window",      self.window))
        self.threshold   = float(params.get("threshold", self.threshold))
        self.min_train   = int(params.get("min_train",   self.min_train))
        self.atr_mult_sl = float(params.get("atr_mult_sl", self.atr_mult_sl))
        self.tp1_pct     = float(params.get("tp1_pct",   self.tp1_pct))

    # ── Núcleo Markov ──────────────────────────────────────────────────────────

    def _label_regimes(self, closes: np.ndarray) -> np.ndarray:
        """
        Rotula cada barra com BEAR(0), SIDE(1) ou BULL(2).
        Barras sem lookback suficiente recebem −1.
        """
        n = len(closes)
        labels = np.full(n, -1, dtype=int)
        for i in range(self.window, n):
            ret = (closes[i] - closes[i - self.window]) / closes[i - self.window]
            if ret > self.threshold:
                labels[i] = BULL
            elif ret < -self.threshold:
                labels[i] = BEAR
            else:
                labels[i] = SIDE
        return labels

    def _build_matrix(self, labels: np.ndarray) -> np.ndarray:
        """
        Constrói matriz de transição 3×3 por contagem de transições consecutivas
        e normalização por linha (máxima verossimilhança).
        Linhas sem observações recebem distribuição uniforme.
        """
        counts = np.zeros((3, 3), dtype=float)
        valid = labels[labels >= 0]
        for i in range(len(valid) - 1):
            counts[valid[i], valid[i + 1]] += 1
        row_sums = counts.sum(axis=1, keepdims=True)
        row_sums[row_sums == 0] = 1.0
        matrix = counts / row_sums
        # Fallback uniforme para estados não observados
        for r in range(3):
            if counts[r].sum() == 0:
                matrix[r] = 1.0 / 3.0
        return matrix

    def _stationary(self, matrix: np.ndarray) -> np.ndarray:
        """
        Distribuição estacionária π = π × T.
        Calculada como autovetor esquerdo de T correspondente ao autovalor 1.
        """
        eigenvalues, eigenvectors = np.linalg.eig(matrix.T)
        idx = int(np.argmin(np.abs(eigenvalues - 1.0)))
        pi = np.abs(eigenvectors[:, idx].real)
        s = pi.sum()
        if s == 0:
            return np.array([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=float)
        return pi / s

    def _atr(self, candles: list, period: int = 14) -> float:
        n = min(period + 1, len(candles))
        bars = candles[-n:]
        trs = []
        for i in range(1, len(bars)):
            h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return float(np.mean(trs)) if trs else 0.0

    # ── compute ────────────────────────────────────────────────────────────────

    def compute(self, candles: list) -> Optional[StrategyResult]:
        needed = self.min_train + self.window
        if len(candles) < needed:
            return None

        closes = np.array([c.close for c in candles], dtype=float)
        labels = self._label_regimes(closes)
        valid_labels = labels[labels >= 0]

        if len(valid_labels) < self.min_train:
            return None

        current_state = int(labels[-1])
        if current_state < 0:
            return None

        matrix   = self._build_matrix(labels)
        p_next   = matrix[current_state]          # P(próximo | atual)
        p_bull   = float(p_next[BULL])
        p_side   = float(p_next[SIDE])
        p_bear   = float(p_next[BEAR])
        signal_v = p_bull - p_bear                 # ∈ [−1, +1]

        stationary  = self._stationary(matrix)
        persistence = float(matrix[current_state, current_state])
        regime_name = STATE_NAME[current_state]

        # ATR para SL dinâmico
        atr = self._atr(candles)

        # ── Critérios (para o checklist da UI) ──────────────────────────────
        c1 = signal_v > 0                          # sinal favorece alta
        c2 = current_state != BEAR                 # não estamos em regime Bear
        c3 = persistence >= 0.5                    # regime atual é persistente
        c4 = stationary[BULL] > stationary[BEAR]   # longo prazo favorece Bull
        criteria_met   = int(c1) + int(c2) + int(c3) + int(c4)
        criteria_total = 4

        # ── Sinal com Proteção de Transição (Evita Entradas Tardias / Warmup) ──
        prev_was_buy = False
        prev_was_sell = False
        if len(candles) >= needed + 1:
            closes_prev = closes[:-1]
            labels_prev = self._label_regimes(closes_prev)
            valid_labels_prev = labels_prev[labels_prev >= 0]
            if len(valid_labels_prev) >= self.min_train:
                current_state_prev = int(labels_prev[-1])
                if current_state_prev >= 0:
                    matrix_prev = self._build_matrix(labels_prev)
                    p_next_prev = matrix_prev[current_state_prev]
                    p_bull_prev = float(p_next_prev[BULL])
                    p_bear_prev = float(p_next_prev[BEAR])
                    signal_v_prev = p_bull_prev - p_bear_prev
                    
                    c1_prev = signal_v_prev > 0
                    c2_prev = current_state_prev != BEAR
                    
                    if c1_prev and c2_prev:
                        prev_was_buy = True
                    elif not c2_prev or signal_v_prev < -0.10:
                        prev_was_sell = True

        if c1 and c2:
            if not prev_was_buy:
                sig = Signal.BUY
                hold_reason = ""
            else:
                sig = Signal.HOLD
                hold_reason = (
                    f"Regime {regime_name} · signal={signal_v:+.2f} · "
                    f"LONG ativo (aguardando nova transição)"
                )
        elif not c2 or signal_v < -0.10:
            if not prev_was_sell:
                sig = Signal.SELL
                hold_reason = ""
            else:
                sig = Signal.HOLD
                hold_reason = (
                    f"Regime {regime_name} · signal={signal_v:+.2f} · "
                    f"SHORT ativo (aguardando nova transição)"
                )
        else:
            sig        = Signal.HOLD
            hold_reason = (
                f"Regime {regime_name} · signal={signal_v:+.2f} · "
                f"aguardando virada Bull"
            )

        # current_regime como float para o gráfico (0=Bear, 1=Side, 2=Bull)
        indicators = {
            "signal":       round(signal_v,           4),
            "p_bull":       round(p_bull,             4),
            "p_side":       round(p_side,             4),
            "p_bear":       round(p_bear,             4),
            "persistence":  round(persistence,        4),
            "stat_bull":    round(float(stationary[BULL]), 4),
            "stat_side":    round(float(stationary[SIDE]), 4),
            "stat_bear":    round(float(stationary[BEAR]), 4),
            "current_regime": float(current_state),
            "atr":          round(atr, 4),
        }

        return StrategyResult(
            signal        = sig,
            indicators    = indicators,
            hold_reason   = hold_reason,
            criteria_met  = criteria_met,
            criteria_total= criteria_total,
            metadata      = {
                "regime": regime_name,
                "transition_matrix": matrix.tolist(),
                "stationary_distribution": {
                    "Bull":     round(float(stationary[BULL]), 4),
                    "Sideways": round(float(stationary[SIDE]), 4),
                    "Bear":     round(float(stationary[BEAR]), 4),
                },
            },
        )
