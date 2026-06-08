"""
strategies/factory/rg004.py - RG004 Grafos de Markov

Modelo de regime institucional para 15m que discretiza o preco em estados
estruturais e opera apenas quando contexto, frequencia, microestrutura e
probabilidade de transicao apontam para o mesmo lado.
"""

from __future__ import annotations

from typing import Optional

import math
import numpy as np
import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


STATE_NEUTRAL = 0
STATE_CHEAP = 1
STATE_EXPENSIVE = 2
STATE_EQUILIBRIUM = 3
STATE_EXPANSION_UP = 4
STATE_EXPANSION_DOWN = 5
STATE_LIQUIDITY_BUY = 6
STATE_LIQUIDITY_SELL = 7

STATE_NAMES = {
    STATE_NEUTRAL: "neutral",
    STATE_CHEAP: "cheap_zone",
    STATE_EXPENSIVE: "expensive_zone",
    STATE_EQUILIBRIUM: "equilibrium",
    STATE_EXPANSION_UP: "expansion_up",
    STATE_EXPANSION_DOWN: "expansion_down",
    STATE_LIQUIDITY_BUY: "liquidity_buy",
    STATE_LIQUIDITY_SELL: "liquidity_sell",
}

BULLISH_STATES = (STATE_EXPANSION_UP, STATE_LIQUIDITY_BUY)
BEARISH_STATES = (STATE_EXPANSION_DOWN, STATE_LIQUIDITY_SELL)


class GrafosDeMarkovStrategy(BaseStrategy):
    """RG004 - Grafo/Markov institucional com quatro criterios fundamentais."""

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="RG004",
            name="RG004 - Grafos de Markov",
            description=(
                "Estratégia de regime que transforma candles de 15m em estados estruturais "
                "de preço caro/barato, frequência de volatilidade, captura de liquidez e "
                "probabilidade de transição de Markov. Opera somente quando os quatro "
                "critérios fundamentais estão alinhados: contexto, frequência, "
                "microestrutura e gatilho estocástico com R:R mínimo. "
                "Recomendada exclusivamente para XAU/USD, ES, NQ, EUR/USD, GBP/USD, "
                "BTC/USD e ETH/USD. Como a OKX opera apenas criptomoedas, use no OKX "
                "somente BTC-USDT ou ETH-USDT na OKX spot."
            ),
            recommended_timeframe="15m",
            recommended_symbol="BTC-USDT",
            tags=["regime", "markov", "graph", "price_action", "institutional"],
            criteria=[
                {
                    "id": "c1_location",
                    "label": "C1 Localização Relativa",
                    "description": "Preço em zona barata para compra ou cara para venda vs abertura e 50% macro.",
                },
                {
                    "id": "c2_volatility",
                    "label": "C2 Assinatura de Volatilidade",
                    "description": "Amplitude atual classificada como expansão/desbalanceamento ou equilíbrio.",
                },
                {
                    "id": "c3_rejection",
                    "label": "C3 Rejeição e Absorção",
                    "description": "Pavio dominante captura liquidez em máxima/mínima estrutural recente.",
                },
                {
                    "id": "c4_markov",
                    "label": "C4 Transição Condicional",
                    "description": "Probabilidade de transição favorável acima do limiar com R:R mínimo.",
                },
            ],
            params={
                "lookback_days": ParamDef(
                    type="int", default=30, min=10, max=90, step=5,
                    description="Janela móvel em dias para treinar a matriz de Markov.",
                ),
                "bars_per_day": ParamDef(
                    type="int", default=96, min=24, max=288, step=1,
                    description="Quantidade esperada de candles por dia (96 para 15m).",
                ),
                "macro_days": ParamDef(
                    type="int", default=3, min=2, max=10, step=1,
                    description="Dias usados para calcular a linha média macro de 50%.",
                ),
                "location_z": ParamDef(
                    type="float", default=0.35, min=0.1, max=2.0, step=0.05,
                    description="Distância mínima em ATR para classificar preço caro/barato.",
                ),
                "atr_period": ParamDef(
                    type="int", default=14, min=5, max=50, step=1,
                    description="Período do ATR para frequência e risco.",
                ),
                "expansion_threshold": ParamDef(
                    type="float", default=1.5, min=1.0, max=3.0, step=0.1,
                    description="Assinatura acima deste valor indica expansão/desbalanceamento.",
                ),
                "equilibrium_threshold": ParamDef(
                    type="float", default=0.5, min=0.1, max=1.0, step=0.05,
                    description="Assinatura abaixo deste valor indica acumulação/equilíbrio.",
                ),
                "wick_ratio": ParamDef(
                    type="float", default=0.6, min=0.3, max=0.9, step=0.05,
                    description="Pavio mínimo em relação ao range da vela para captura de liquidez.",
                ),
                "liquidity_lookback": ParamDef(
                    type="int", default=24, min=8, max=96, step=4,
                    description="Barras usadas para máxima/mínima estrutural tocada pelo pavio.",
                ),
                "prob_threshold": ParamDef(
                    type="float", default=0.55, min=0.5, max=0.9, step=0.01,
                    description="Probabilidade mínima da transição favorável de Markov.",
                ),
                "min_transition_count": ParamDef(
                    type="int", default=8, min=3, max=50, step=1,
                    description="Mínimo de transições observadas no estado atual para operar.",
                ),
                "sl_mult": ParamDef(
                    type="float", default=1.5, min=0.5, max=5.0, step=0.1,
                    description="Multiplicador ATR aplicado além do extremo do candle para Stop Loss.",
                ),
                "tp1_rr": ParamDef(
                    type="float", default=2.0, min=1.0, max=5.0, step=0.1,
                    description="Relação risco-retorno mínima e alvo TP1.",
                ),
                "ts_mult": ParamDef(
                    type="float", default=3.0, min=1.0, max=10.0, step=0.5,
                    description="Multiplicador ATR para Trailing Stop após TP1.",
                ),
            },
        )

    def __init__(self):
        p = self.info().params
        self.lookback_days = p["lookback_days"].default
        self.bars_per_day = p["bars_per_day"].default
        self.macro_days = p["macro_days"].default
        self.location_z = p["location_z"].default
        self.atr_period = p["atr_period"].default
        self.expansion_threshold = p["expansion_threshold"].default
        self.equilibrium_threshold = p["equilibrium_threshold"].default
        self.wick_ratio = p["wick_ratio"].default
        self.liquidity_lookback = p["liquidity_lookback"].default
        self.prob_threshold = p["prob_threshold"].default
        self.min_transition_count = p["min_transition_count"].default
        self.sl_mult = p["sl_mult"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default

    def set_params(self, params: dict) -> None:
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles: list, context: dict | None = None) -> Optional[StrategyResult]:
        runtime_window = 300
        macro_window = min(int(self.bars_per_day * self.macro_days), runtime_window)
        markov_window = min(int(self.bars_per_day * self.lookback_days), runtime_window)
        min_candles = max(
            int(self.atr_period) + 5,
            int(self.liquidity_lookback) + 5,
            min(macro_window, markov_window),
        )
        min_candles = min(min_candles, runtime_window)
        if len(candles) < min_candles:
            close = float(candles[-1].close) if candles else 0.0
            atr = self._simple_atr(candles, int(self.atr_period))
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo Markov/Grafo - aguardando {min_candles} candles ({len(candles)} recebidos)",
                indicators={
                    "close": close,
                    "atr": round(atr, 6),
                    "location_score": 0.0,
                    "vol_signature": 0.0,
                    "lower_wick_ratio": 0.0,
                    "upper_wick_ratio": 0.0,
                    "p_buy": 0.0,
                    "p_sell": 0.0,
                    "rr_buy": 0.0,
                    "rr_sell": 0.0,
                    "current_state": 0.0,
                    "state_centrality": 0.0,
                    "c1_location": 0.0,
                    "c2_volatility": 0.0,
                    "c3_rejection": 0.0,
                    "c4_markov": 0.0,
                },
                metadata={
                    "sl_price": close,
                    "tp1_price": close,
                    "sl_pct": 0.0,
                    "tp1_pct": 0.0,
                    "ts_pct": round(atr * float(self.ts_mult) / close, 5) if close else 0.0,
                },
                criteria_met=0,
                criteria_total=4,
            )

        df = pd.DataFrame([
            {
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            }
            for c in candles
        ])

        atr_s = ta.atr(df["high"], df["low"], df["close"], length=int(self.atr_period))
        if atr_s is None or atr_s.dropna().empty:
            return self._hold_result(candles, "ATR indisponível para classificar frequência")

        atr_series = atr_s.bfill().ffill()
        atr = self._safe_float(atr_series.iloc[-1], 0.0)
        if atr <= 0:
            return self._hold_result(candles, "ATR zerado - sem frequência útil para o grafo")

        lookback_bars = min(len(df), int(self.lookback_days * self.bars_per_day))
        work = df.iloc[-lookback_bars:].copy().reset_index(drop=True)
        work_atr = atr_series.iloc[-lookback_bars:].reset_index(drop=True)

        states = self._label_states(work, work_atr)
        matrix, counts = self._build_transition_matrix(states)
        centrality = self._stationary_distribution(matrix)

        current_state = int(states[-1])
        current_row = matrix[current_state]
        p_buy = float(sum(current_row[s] for s in BULLISH_STATES))
        p_sell = float(sum(current_row[s] for s in BEARISH_STATES))
        transition_count = int(counts[current_state].sum())

        close = float(df["close"].iloc[-1])
        high = float(df["high"].iloc[-1])
        low = float(df["low"].iloc[-1])
        open_ = float(df["open"].iloc[-1])

        location_score, day_open, macro_mid = self._location_score(df, atr)
        candle_range = max(high - low, 0.0)
        body_high = max(open_, close)
        body_low = min(open_, close)
        lower_wick_ratio = (body_low - low) / candle_range if candle_range > 0 else 0.0
        upper_wick_ratio = (high - body_high) / candle_range if candle_range > 0 else 0.0
        vol_signature = candle_range / atr if atr > 0 else 0.0

        prev = df.iloc[-self.liquidity_lookback - 1:-1]
        prev_high = float(prev["high"].max()) if not prev.empty else high
        prev_low = float(prev["low"].min()) if not prev.empty else low
        touched_prev_low = low <= prev_low
        touched_prev_high = high >= prev_high

        cheap_zone = location_score <= -float(self.location_z) or (close < macro_mid and close < day_open)
        expensive_zone = location_score >= float(self.location_z) or (close > macro_mid and close > day_open)
        volatility_stateful = (
            vol_signature >= float(self.expansion_threshold)
            or vol_signature <= float(self.equilibrium_threshold)
        )
        buy_rejection = lower_wick_ratio >= float(self.wick_ratio) and touched_prev_low and close > body_low
        sell_rejection = upper_wick_ratio >= float(self.wick_ratio) and touched_prev_high and close < body_high

        support, resistance = self._nearest_levels(df, close)
        buy_sl = min(low, support) - atr * float(self.sl_mult)
        sell_sl = max(high, resistance) + atr * float(self.sl_mult)
        buy_risk = max(close - buy_sl, atr * 0.25)
        sell_risk = max(sell_sl - close, atr * 0.25)
        buy_tp = close + buy_risk * float(self.tp1_rr)
        sell_tp = close - sell_risk * float(self.tp1_rr)

        rr_buy = max(resistance - close, 0.0) / buy_risk if buy_risk > 0 else 0.0
        rr_sell = max(close - support, 0.0) / sell_risk if sell_risk > 0 else 0.0

        c1_buy = bool(cheap_zone)
        c1_sell = bool(expensive_zone)
        c2 = bool(volatility_stateful)
        c3_buy = bool(buy_rejection)
        c3_sell = bool(sell_rejection)
        c4_buy = p_buy >= float(self.prob_threshold) and rr_buy >= float(self.tp1_rr)
        c4_sell = p_sell >= float(self.prob_threshold) and rr_sell >= float(self.tp1_rr)
        enough_markov = transition_count >= int(self.min_transition_count)

        buy_ready = c1_buy and c2 and c3_buy and c4_buy and enough_markov
        sell_ready = c1_sell and c2 and c3_sell and c4_sell and enough_markov

        if buy_ready and not sell_ready:
            signal = Signal.BUY
            sl_price = buy_sl
            tp1_price = buy_tp
            direction = "buy"
            criteria_met = 4
            hold_reason = ""
        elif sell_ready and not buy_ready:
            signal = Signal.SELL
            sl_price = sell_sl
            tp1_price = sell_tp
            direction = "sell"
            criteria_met = 4
            hold_reason = ""
        else:
            signal = Signal.HOLD
            buy_score = int(c1_buy) + int(c2) + int(c3_buy) + int(c4_buy and enough_markov)
            sell_score = int(c1_sell) + int(c2) + int(c3_sell) + int(c4_sell and enough_markov)
            if buy_score >= sell_score:
                sl_price = buy_sl
                tp1_price = buy_tp
                direction = "buy_watch"
                criteria_met = buy_score
            else:
                sl_price = sell_sl
                tp1_price = sell_tp
                direction = "sell_watch"
                criteria_met = sell_score
            hold_reason = self._hold_reason(
                c1_buy, c1_sell, c2, c3_buy, c3_sell, c4_buy, c4_sell,
                enough_markov, p_buy, p_sell, rr_buy, rr_sell, transition_count,
            )

        sl_dist = abs(close - sl_price)
        tp_dist = abs(tp1_price - close)
        indicators = {
            "close": round(close, 4),
            "atr": round(atr, 6),
            "day_open": round(day_open, 4),
            "macro_mid": round(macro_mid, 4),
            "location_score": round(location_score, 4),
            "vol_signature": round(vol_signature, 4),
            "lower_wick_ratio": round(lower_wick_ratio, 4),
            "upper_wick_ratio": round(upper_wick_ratio, 4),
            "p_buy": round(p_buy, 4),
            "p_sell": round(p_sell, 4),
            "rr_buy": round(rr_buy, 4),
            "rr_sell": round(rr_sell, 4),
            "current_state": float(current_state),
            "state_centrality": round(float(centrality[current_state]), 4),
            "transition_count": float(transition_count),
            "c1_location": 1.0 if (c1_buy or c1_sell) else 0.0,
            "c2_volatility": 1.0 if c2 else 0.0,
            "c3_rejection": 1.0 if (c3_buy or c3_sell) else 0.0,
            "c4_markov": 1.0 if ((c4_buy or c4_sell) and enough_markov) else 0.0,
        }

        return StrategyResult(
            signal=signal,
            indicators=indicators,
            metadata={
                "sl_price": round(sl_price, 4),
                "tp1_price": round(tp1_price, 4),
                "sl_pct": round(sl_dist / close, 5) if close else 0.0,
                "tp1_pct": round(tp_dist / close, 5) if close else 0.0,
                "ts_pct": round(atr * float(self.ts_mult) / close, 5) if close else 0.0,
                "direction": direction,
                "current_state": STATE_NAMES.get(current_state, "unknown"),
                "transition_matrix": matrix.round(4).tolist(),
                "state_centrality": {
                    STATE_NAMES[i]: round(float(v), 4)
                    for i, v in enumerate(centrality)
                },
                "support": round(support, 4),
                "resistance": round(resistance, 4),
            },
            hold_reason=hold_reason,
            criteria_met=criteria_met,
            criteria_total=4,
        )

    def _hold_result(self, candles: list, reason: str) -> StrategyResult:
        close = float(candles[-1].close) if candles else 0.0
        atr = self._simple_atr(candles, int(self.atr_period))
        return StrategyResult(
            signal=Signal.HOLD,
            indicators={
                "close": close,
                "atr": round(atr, 6),
                "location_score": 0.0,
                "vol_signature": 0.0,
                "lower_wick_ratio": 0.0,
                "upper_wick_ratio": 0.0,
                "p_buy": 0.0,
                "p_sell": 0.0,
                "rr_buy": 0.0,
                "rr_sell": 0.0,
                "current_state": 0.0,
                "state_centrality": 0.0,
                "c1_location": 0.0,
                "c2_volatility": 0.0,
                "c3_rejection": 0.0,
                "c4_markov": 0.0,
            },
            metadata={
                "sl_price": close,
                "tp1_price": close,
                "sl_pct": 0.0,
                "tp1_pct": 0.0,
                "ts_pct": round(atr * float(self.ts_mult) / close, 5) if close else 0.0,
            },
            hold_reason=reason,
            criteria_met=0,
            criteria_total=4,
        )

    def _label_states(self, df: pd.DataFrame, atr_s: pd.Series) -> np.ndarray:
        states = np.full(len(df), STATE_NEUTRAL, dtype=int)
        for i in range(len(df)):
            atr = self._safe_float(atr_s.iloc[i], 0.0)
            if atr <= 0:
                continue
            window = df.iloc[:i + 1]
            row = df.iloc[i]
            location_score, day_open, macro_mid = self._location_score(window, atr)
            candle_range = max(float(row["high"] - row["low"]), 0.0)
            if candle_range <= 0:
                continue
            body_high = max(float(row["open"]), float(row["close"]))
            body_low = min(float(row["open"]), float(row["close"]))
            lower_wick_ratio = (body_low - float(row["low"])) / candle_range
            upper_wick_ratio = (float(row["high"]) - body_high) / candle_range
            vol_signature = candle_range / atr
            prev = window.iloc[max(0, i - int(self.liquidity_lookback)):i]
            prev_low = float(prev["low"].min()) if not prev.empty else float(row["low"])
            prev_high = float(prev["high"].max()) if not prev.empty else float(row["high"])

            if lower_wick_ratio >= float(self.wick_ratio) and float(row["low"]) <= prev_low:
                states[i] = STATE_LIQUIDITY_BUY
            elif upper_wick_ratio >= float(self.wick_ratio) and float(row["high"]) >= prev_high:
                states[i] = STATE_LIQUIDITY_SELL
            elif vol_signature >= float(self.expansion_threshold):
                states[i] = STATE_EXPANSION_UP if float(row["close"]) >= float(row["open"]) else STATE_EXPANSION_DOWN
            elif vol_signature <= float(self.equilibrium_threshold):
                states[i] = STATE_EQUILIBRIUM
            elif location_score <= -float(self.location_z) or (float(row["close"]) < macro_mid and float(row["close"]) < day_open):
                states[i] = STATE_CHEAP
            elif location_score >= float(self.location_z) or (float(row["close"]) > macro_mid and float(row["close"]) > day_open):
                states[i] = STATE_EXPENSIVE
            else:
                states[i] = STATE_NEUTRAL
        return states

    def _build_transition_matrix(self, states: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        n_states = len(STATE_NAMES)
        counts = np.zeros((n_states, n_states), dtype=float)
        for i in range(len(states) - 1):
            counts[int(states[i]), int(states[i + 1])] += 1.0
        matrix = np.zeros_like(counts)
        for row in range(n_states):
            total = counts[row].sum()
            if total <= 0:
                matrix[row] = 1.0 / n_states
            else:
                matrix[row] = counts[row] / total
        return matrix, counts

    def _stationary_distribution(self, matrix: np.ndarray) -> np.ndarray:
        dist = np.full(matrix.shape[0], 1.0 / matrix.shape[0], dtype=float)
        for _ in range(32):
            dist = dist @ matrix
            total = dist.sum()
            if total > 0:
                dist = dist / total
        return dist

    def _location_score(self, df: pd.DataFrame, atr: float) -> tuple[float, float, float]:
        close = float(df["close"].iloc[-1])
        bars_per_day = max(1, int(self.bars_per_day))
        day_start = (len(df) - 1) // bars_per_day * bars_per_day
        day_open = float(df["open"].iloc[day_start])
        macro_len = min(len(df), int(self.macro_days) * bars_per_day)
        macro = df.iloc[-macro_len:]
        macro_mid = (float(macro["high"].max()) + float(macro["low"].min())) / 2.0
        denom = max(atr, close * 0.0001, 1e-9)
        location_score = (close - macro_mid) / denom
        return float(location_score), day_open, macro_mid

    def _nearest_levels(self, df: pd.DataFrame, close: float) -> tuple[float, float]:
        window = df.iloc[-min(len(df), int(self.bars_per_day * self.macro_days)):]
        lows = sorted(float(v) for v in window["low"].tail(max(int(self.liquidity_lookback), 1)))
        highs = sorted((float(v) for v in window["high"].tail(max(int(self.liquidity_lookback), 1))), reverse=True)
        supports = [v for v in lows if v < close]
        resistances = [v for v in highs if v > close]
        atr_proxy = max(close * 0.002, 1e-6)
        support = max(supports) if supports else close - atr_proxy
        resistance = min(resistances) if resistances else close + atr_proxy
        return support, resistance

    def _hold_reason(
        self,
        c1_buy: bool,
        c1_sell: bool,
        c2: bool,
        c3_buy: bool,
        c3_sell: bool,
        c4_buy: bool,
        c4_sell: bool,
        enough_markov: bool,
        p_buy: float,
        p_sell: float,
        rr_buy: float,
        rr_sell: float,
        transition_count: int,
    ) -> str:
        missing = []
        if not (c1_buy or c1_sell):
            missing.append("localização ainda neutra")
        if not c2:
            missing.append("frequência sem expansão/equilíbrio extremo")
        if not (c3_buy or c3_sell):
            missing.append("sem captura de liquidez por pavio")
        if not enough_markov:
            missing.append(f"amostra Markov curta ({transition_count})")
        if not (c4_buy or c4_sell):
            missing.append(
                f"transição/RR insuficiente (p_buy={p_buy:.2f}, p_sell={p_sell:.2f}, "
                f"rr_buy={rr_buy:.2f}, rr_sell={rr_sell:.2f})"
            )
        return "; ".join(missing) if missing else "Critérios mistos - aguardando alinhamento direcional"

    def _safe_float(self, value, default: float) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return default
        if math.isnan(result) or math.isinf(result):
            return default
        return result

    def _simple_atr(self, candles: list, period: int) -> float:
        if len(candles) < 2:
            return 0.0
        bars = candles[-min(len(candles), period + 1):]
        true_ranges = []
        for i in range(1, len(bars)):
            high = float(bars[i].high)
            low = float(bars[i].low)
            prev_close = float(bars[i - 1].close)
            true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        return float(np.mean(true_ranges)) if true_ranges else 0.0
