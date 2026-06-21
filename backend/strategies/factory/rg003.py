"""
strategies/gex_gamma_exposure.py — I006 GEX Gamma Exposure Regime

Filosofia
─────────
Ao contrário de indicadores de correlação técnica (RSI, MACD), o GEX trabalha
por CAUSALIDADE: mede diretamente o volume de compra e venda posicionado por
market makers no mercado de opções, o que influencia a dinâmica de preços.

Dois regimes de gama
────────────────────
  Positivo (calls OI > puts OI ATM)
    → Market makers net short calls → hedge counter-trend → AMORTECIMENTO
    → Volatilidade baixa, mercado lateral / acumulo
    → Estratégia: rentabilizar a lateralidade; comprar suporte, vender resistência

  Negativo (puts OI > calls OI ATM)
    → Market makers net short puts → hedge pro-trend → AMPLIFICAÇÃO
    → Volatilidade alta; altas parabólicas ou quedas livres
    → Estratégia: operações direcionais; calls antes de alta, puts antes de queda

Fontes de dados
───────────────
  Primária  : GexSnapshot injetado via context["gex_data"] (Deribit public API)
  Fallback  : estimativa de regime via Bollinger Band Width + ATR ratio (OHLCV)

Pipeline de sinal
─────────────────
  1. REGIME  — detectar Positive / Negative Gamma (GEX ou estimativa)
  2. NÍVEIS  — mapear suportes (top put strikes) e resistências (top call strikes)
  3. GATILHO — direcional baseado no regime e posição do preço vs níveis
  4. RISCO   — SL/TP adaptados ao regime (mais estreito em pos. gamma, mais largo em neg.)

Critérios explainability (4 total)
───────────────────────────────────
  C1: Regime GEX confirmado (pos ou neg)
  C2: Preço próximo ou rompendo nível de OI relevante
  C3: Volume acima da média (confirmação de agressão)
  C4: ATR/BBW alinhado com o regime esperado
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class GexGammaExposureStrategy(BaseStrategy):
    """I006 — Gamma Exposure (GEX) Regime Strategy."""

    # Flag lida pelo BotInstance para ativar o GEX feed (Deribit)
    needs_gex_context: bool = True

    # ── Metadados ────────────────────────────────────────────────────────────

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="RG003",
            name="RG003 - GEX Gamma Exposure Regime",
            description=(
                "Estratégia baseada em Gamma Exposure (GEX) do mercado de opções de BTC. "
                "Ao contrário de indicadores técnicos tradicionais, o GEX trabalha por causalidade: "
                "mede o posicionamento de market makers em opções, que influencia diretamente a "
                "dinâmica de preço. Em Gama Positivo (calls > puts ATM), os market makers atuam "
                "de forma contra-tendência, gerando amortecimento e lateralidade — ideal para "
                "estratégias de acumulação passiva. Em Gama Negativo (puts > calls ATM), atuam "
                "a favor da tendência, amplificando movimentos — ideal para proteções e alavancagem "
                "direcional. Usa dados ao vivo da Deribit (gratuito) com fallback via BBW+ATR."
            ),
            recommended_timeframe="1h",
            tags=["Intelligence", "GEX", "Options", "Regime", "Institutional", "MarketMakers"],
            criteria=[
                {"id": "c1_regime", "label": "C1 Regime GEX", "description": "Regime GEX positivo ou negativo confirmado."},
                {"id": "c2_level", "label": "C2 Nível OI", "description": "Preço próximo ou rompendo nível relevante de OI."},
                {"id": "c3_volume", "label": "C3 Volume", "description": "Volume acima da média confirma agressão."},
                {"id": "c4_atr", "label": "C4 ATR", "description": "ATR/BBW alinhado ao regime esperado."},
            ],
            params={
                "level_proximity_pct": ParamDef(
                    "float", 0.8,
                    "% de proximidade ao nível de OI para acionar gatilho (em relação ao ATR).",
                    min=0.2, max=2.0, step=0.1,
                ),
                "vol_spike_mult": ParamDef(
                    "float", 1.4,
                    "Multiplicador de volume médio para confirmar agressão institucional.",
                    min=1.0, max=3.0, step=0.1,
                ),
                "atr_period": ParamDef(
                    "int", 14,
                    "Período do ATR para gestão de risco.",
                    min=7, max=30, step=1,
                ),
                "sl_mult_pos": ParamDef(
                    "float", 1.2,
                    "Multiplicador ATR para Stop Loss em Gama Positivo (mercado amortecido).",
                    min=0.5, max=3.0, step=0.1,
                ),
                "sl_mult_neg": ParamDef(
                    "float", 2.0,
                    "Multiplicador ATR para Stop Loss em Gama Negativo (mercado explosivo).",
                    min=0.5, max=4.0, step=0.1,
                ),
                "tp_rr_ratio": ParamDef(
                    "float", 2.0,
                    "Relação Risco:Recompensa para TP1 (TP = SL × ratio).",
                    min=1.0, max=5.0, step=0.1,
                ),
                "bbw_period": ParamDef(
                    "int", 20,
                    "Período das Bollinger Bands para estimativa de regime fallback.",
                    min=10, max=50, step=1,
                ),
                "bbw_neg_mult": ParamDef(
                    "float", 1.5,
                    "BBW acima de (mult × média 20p) classifica como Gama Negativo estimado.",
                    min=1.0, max=3.0, step=0.1,
                ),
            },
        )

    # ── Init ─────────────────────────────────────────────────────────────────

    def __init__(self):
        p = self.info().params
        self.level_proximity_pct = p["level_proximity_pct"].default
        self.vol_spike_mult      = p["vol_spike_mult"].default
        self.atr_period          = p["atr_period"].default
        self.sl_mult_pos         = p["sl_mult_pos"].default
        self.sl_mult_neg         = p["sl_mult_neg"].default
        self.tp_rr_ratio         = p["tp_rr_ratio"].default
        self.bbw_period          = p["bbw_period"].default
        self.bbw_neg_mult        = p["bbw_neg_mult"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    # ── Entry point ──────────────────────────────────────────────────────────

    def compute_with_context(
        self, candles: list, context: dict | None = None
    ) -> Optional[StrategyResult]:
        min_len = max(self.atr_period, self.bbw_period) + 10
        if len(candles) < min_len:
            return None

        df = pd.DataFrame([
            {
                "open":   c.open,
                "high":   c.high,
                "low":    c.low,
                "close":  c.close,
                "volume": c.volume,
            }
            for c in candles
        ])

        # ── 1. Indicadores base ──────────────────────────────────────────────
        atr_s = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        bb    = ta.bbands(df["close"], length=self.bbw_period, std=2.0)

        if atr_s is None or bb is None:
            return None

        atr_val = float(atr_s.iloc[-1])
        close   = candles[-1].close

        # Bollinger Band Width (normalised)
        bbu_col = [c for c in bb.columns if c.startswith("BBU")]
        bbl_col = [c for c in bb.columns if c.startswith("BBL")]
        bbm_col = [c for c in bb.columns if c.startswith("BBM")]
        if not bbu_col or not bbl_col or not bbm_col:
            return None

        bbw_series = (bb[bbu_col[0]] - bb[bbl_col[0]]) / bb[bbm_col[0]]
        bbw_now    = float(bbw_series.iloc[-1])
        bbw_ma     = float(bbw_series.rolling(20).mean().iloc[-1])

        # ── 2. Regime (GEX primário ou fallback) ────────────────────────────
        gex_snap = None
        if context:
            gex_snap = context.get("gex_data")

        regime, regime_source = self._classify_regime(
            gex_snap, bbw_now, bbw_ma
        )

        # ── 3. Níveis de suporte e resistência ──────────────────────────────
        support_levels, resistance_levels, max_pain, neg_peak_usd = \
            self._extract_levels(gex_snap, close, atr_val)

        nearest_support    = support_levels[0]    if support_levels    else None
        nearest_resistance = resistance_levels[0] if resistance_levels else None

        # Proximity threshold: level_proximity_pct × ATR
        prox = atr_val * self.level_proximity_pct

        near_support    = nearest_support    is not None and abs(close - nearest_support)    <= prox
        near_resistance = nearest_resistance is not None and abs(close - nearest_resistance) <= prox

        # Breakout detection (price closing beyond level vs previous candle)
        prev_close = candles[-2].close if len(candles) >= 2 else close
        broke_above = (nearest_resistance is not None
                       and prev_close < nearest_resistance <= close)
        broke_below = (nearest_support    is not None
                       and prev_close > nearest_support >= close)

        # ── 4. Volume confirmation ───────────────────────────────────────────
        vol_ma      = float(df["volume"].rolling(20).mean().iloc[-1])
        vol_current = candles[-1].volume
        vol_spike   = vol_current > vol_ma * self.vol_spike_mult

        # ── 5. ATR alignment with regime ────────────────────────────────────
        atr_ma  = float(atr_s.rolling(10).mean().iloc[-1])
        atr_exp = atr_val > atr_ma * 1.2     # ATR expanding → confirms neg gamma
        atr_con = atr_val < atr_ma * 0.9     # ATR contracting → confirms pos gamma
        atr_ok  = (regime == "positive_gamma" and atr_con) or \
                  (regime == "negative_gamma" and atr_exp) or \
                  regime == "neutral"

        # ── 6. Critérios ────────────────────────────────────────────────────
        criteria_total = 4
        criteria_met   = 0

        c1 = regime in ("positive_gamma", "negative_gamma")
        c3 = vol_spike
        c4 = atr_ok or (regime == "neutral")

        if c1: criteria_met += 1
        if c3: criteria_met += 1
        if c4: criteria_met += 1

        # ── 7. Lógica de sinal ───────────────────────────────────────────────
        signal     = Signal.HOLD
        hold_reason = ""

        if regime == "positive_gamma":
            # Mercado amortecido → range strategy
            c2_buy  = near_support
            c2_sell = near_resistance
            if c2_buy:  criteria_met += 1
            if c2_sell: criteria_met += 1

            if criteria_met >= 3 and c2_buy:
                signal = Signal.BUY
            elif criteria_met >= 3 and c2_sell:
                signal = Signal.SELL
            else:
                hold_reason = (
                    f"Gama Positivo — aguardando toque em suporte"
                    f" ({nearest_support or 'N/D'}) ou resistência"
                    f" ({nearest_resistance or 'N/D'})"
                )

        elif regime == "negative_gamma":
            # Mercado explosivo → breakout strategy
            c2_buy  = broke_above
            c2_sell = broke_below
            if c2_buy:  criteria_met += 1
            if c2_sell: criteria_met += 1

            if criteria_met >= 3 and c2_buy:
                signal = Signal.BUY
            elif criteria_met >= 3 and c2_sell:
                signal = Signal.SELL
            else:
                hold_reason = (
                    f"Gama Negativo — aguardando rompimento de"
                    f" resistência ({nearest_resistance or 'N/D'})"
                    f" ou suporte ({nearest_support or 'N/D'})"
                )

        else:
            hold_reason = "Regime neutro ou sem dados GEX — aguardando clareza direcional"

        # ── 8. Gestão de risco adaptada ao regime ───────────────────────────
        sl_mult = self.sl_mult_pos if regime == "positive_gamma" else self.sl_mult_neg

        if signal == Signal.BUY:
            sl_price  = round(close - atr_val * sl_mult, 2)
            tp1_price = round(close + atr_val * sl_mult * self.tp_rr_ratio, 2)
        else:
            sl_price  = round(close + atr_val * sl_mult, 2)
            tp1_price = round(close - atr_val * sl_mult * self.tp_rr_ratio, 2)

        # ── 9. Indicadores para o frontend ───────────────────────────────────
        regime_num = 1.0 if regime == "positive_gamma" else (
            -1.0 if regime == "negative_gamma" else 0.0
        )

        gex_val   = float(gex_snap.gex_value) if gex_snap else 0.0
        pcr_val   = float(gex_snap.pcr)       if gex_snap else 1.0
        max_pain_ = float(gex_snap.max_pain)  if gex_snap else close

        indicators: dict[str, float] = {
            "gex_regime":        regime_num,
            "gex_value":         gex_val,
            "pcr":               round(pcr_val, 3),
            "max_pain":          round(max_pain_, 2),
            "nearest_support":   round(nearest_support, 2)    if nearest_support    else 0.0,
            "nearest_resistance":round(nearest_resistance, 2) if nearest_resistance else 0.0,
            "bbw":               round(bbw_now, 4),
            "atr":               round(atr_val, 2),
            "vol_ratio":         round(vol_current / vol_ma, 2) if vol_ma > 0 else 1.0,
            "neg_peak_pressure_M": round(neg_peak_usd / 1e6, 2),
            "c1_regime":         1.0 if c1 else 0.0,
            "c2_level":          1.0 if (near_support or near_resistance or broke_above or broke_below) else 0.0,
            "c3_volume":         1.0 if c3 else 0.0,
            "c4_atr":            1.0 if c4 else 0.0,
        }

        metadata: dict = {
            "regime":         regime,
            "regime_source":  regime_source,
        }
        if signal != Signal.HOLD:
            metadata.update({
                "sl_price":   sl_price,
                "tp1_price":  tp1_price,
                "sl_pct":     round(abs(close - sl_price) / close, 5),
                "tp1_pct":    round(abs(close - tp1_price) / close, 5),
                "ts_pct":     round(abs(close - tp1_price) / close, 5),
                "entry_mode": "pos_gamma_range" if regime == "positive_gamma" else "neg_gamma_breakout",
            })
        if gex_snap:
            metadata["support_levels"]    = gex_snap.support_levels
            metadata["resistance_levels"] = gex_snap.resistance_levels

        return StrategyResult(
            signal=signal,
            indicators=indicators,
            metadata=metadata,
            hold_reason=hold_reason,
            criteria_met=criteria_met,
            criteria_total=criteria_total,
        )

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, context=None)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _classify_regime(
        self,
        gex_snap,
        bbw_now: float,
        bbw_ma: float,
    ) -> tuple[str, str]:
        """
        Returns (regime_str, source_str).
        Uses GEX snapshot when available, otherwise falls back to BBW.
        """
        if gex_snap is not None:
            return gex_snap.regime, "deribit_gex"

        # BBW fallback
        if bbw_ma > 0:
            if bbw_now > bbw_ma * self.bbw_neg_mult:
                return "negative_gamma", "bbw_estimate"
            if bbw_now < bbw_ma * 0.7:
                return "positive_gamma", "bbw_estimate"
        return "neutral", "bbw_estimate"

    def _extract_levels(
        self,
        gex_snap,
        close: float,
        atr_val: float,
    ) -> tuple[list[float], list[float], float, float]:
        """
        Returns (support_levels, resistance_levels, max_pain, neg_peak_usd).
        Falls back to ATR-based synthetic levels when no GEX data.
        """
        if gex_snap is not None:
            sup = sorted(
                [s for s in gex_snap.support_levels if s < close],
                reverse=True,
            )
            res = sorted(
                [r for r in gex_snap.resistance_levels if r > close],
            )
            return sup, res, gex_snap.max_pain, gex_snap.neg_peak_pressure_usd

        # Synthetic levels based on ATR multiples (fallback)
        sup = [round(close - atr_val * m, 2) for m in (1.0, 2.0, 3.0)]
        res = [round(close + atr_val * m, 2) for m in (1.0, 2.0, 3.0)]
        return sup, res, round(close, 2), 0.0
