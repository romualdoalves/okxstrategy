"""
strategies/cte_structure_capture.py — CTE: Captura e Transição de Estrutura

Estratégia de reversão institucional de price action em dois tempos:

  HTF (1H): Identifica captura de liquidez — preço varre uma mínima/máxima
            significante e fecha de volta ao range com pavio longo (rejeição).

  LTF (5M): Após a varredura HTF, detecta CHoCH (Change of Character) —
            rompimento do último topo descendente numa estrutura de baixa.
            Aguarda recuo para zona de desconto Fibonacci 61.8%–78.6%.

Fluxo de critérios (barra de progresso na UI):
  1. Varredura HTF (sweep + pavio de rejeição detectado)
  2. Rejeição confirmada (fechamento de volta ao range — implícito no critério 1)
  3. CHoCH no 5M (quebra da estrutura de baixa após a varredura)
  4. Preço na zona Fibonacci (recuo 61.8%–78.6% do movimento de CHoCH)
  5. Liquidez suficiente (ATR ativo — evita entrada em horários mortos)

Saída:
  BUY : captura de sell-side liquidity (SSL) + CHoCH + zona Fib
  SELL: captura de buy-side liquidity (BSL) + CHoCH + zona Fib
  Stop: abaixo/acima do extremo de rejeição HTF ± buffer ATR
  TP1 : R:R configurável a partir da entrada
  TP2 : nível de liquidez oposta no HTF
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import numpy as np

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class CteStructureCaptureStrategy(BaseStrategy):

    EXTRA_TIMEFRAMES = ["1H"]

    @classmethod
    def extra_timeframes(cls) -> list[str]:
        return cls.EXTRA_TIMEFRAMES

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="PA006",
            name="PA006 - CTE – Captura e Transição de Estrutura",
            description=(
                "Reversão institucional de price action em dois tempos. "
                "No 1H identifica captura de liquidez: preço varre mínima/máxima "
                "significante e fecha de volta com pavio longo. "
                "No 5M confirma CHoCH (quebra da estrutura) e aguarda recuo "
                "para zona Fibonacci 61.8%–78.6% antes de entrar. "
                "Stop abaixo do fundo de rejeição; TP1 via R:R, TP2 na liquidez oposta HTF."
            ),
            recommended_timeframe="5m",
            tags=["price_action", "institutional", "CHoCH", "fibonacci",
                  "liquidity", "reversal", "multi_tf", "ICT"],
            criteria=[
                {"id": "c1_zone", "label": "C1 Zone", "description": "Preço em zona institucional."},
                {"id": "c2_impulse", "label": "C2 Impulse", "description": "Impulso confirma deslocamento."},
                {"id": "c3_fvg", "label": "C3 FVG", "description": "Fair Value Gap válido."},
                {"id": "c4_order_block", "label": "C4 OB", "description": "Order block confirma contexto."},
                {"id": "c5_risk", "label": "C5 Risk", "description": "Risco/retorno válido."},
            ],
            params={
                "htf_swing_lookback": ParamDef(
                    type="int", default=20,
                    description="Barras 1H para buscar swing high/low significante",
                    min=10, max=50,
                ),
                "wick_ratio": ParamDef(
                    type="float", default=0.55,
                    description="Wick mínimo (fração do range da vela HTF) para confirmar rejeição",
                    min=0.3, max=0.9, step=0.05,
                ),
                "choch_lookback": ParamDef(
                    type="int", default=30,
                    description="Barras 5M para detectar estrutura antes do CHoCH",
                    min=10, max=60,
                ),
                "fib_low": ParamDef(
                    type="float", default=0.618,
                    description="Nível Fibonacci inferior da zona de entrada (desconto)",
                    min=0.5, max=0.75, step=0.01,
                ),
                "fib_high": ParamDef(
                    type="float", default=0.786,
                    description="Nível Fibonacci superior da zona de entrada (desconto)",
                    min=0.7, max=0.9, step=0.01,
                ),
                "atr_period": ParamDef(
                    type="int", default=14,
                    description="Período do ATR para stop loss e filtro de liquidez",
                    min=7, max=28,
                ),
                "atr_sl_mult": ParamDef(
                    type="float", default=0.3,
                    description="Buffer ATR adicionado ao extremo de rejeição para o stop",
                    min=0.1, max=1.0, step=0.1,
                ),
                "tp1_rr": ParamDef(
                    type="float", default=1.5,
                    description="Relação R:R para TP1 (saída parcial)",
                    min=1.0, max=3.0, step=0.1,
                ),
            },
        )

    # ------------------------------------------------------------------
    def __init__(self):
        p = self.info().params
        self.htf_swing_lookback = p["htf_swing_lookback"].default
        self.wick_ratio         = p["wick_ratio"].default
        self.choch_lookback     = p["choch_lookback"].default
        self.fib_low            = p["fib_low"].default
        self.fib_high           = p["fib_high"].default
        self.atr_period         = p["atr_period"].default
        self.atr_sl_mult        = p["atr_sl_mult"].default
        self.tp1_rr             = p["tp1_rr"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self._evaluate(candles, htf_candles=None)

    def compute_with_context(
        self,
        candles: list,
        context: dict | None = None,
    ) -> Optional[StrategyResult]:
        htf_candles = None
        if context:
            extra = context.get("extra_candles", {})
            htf_candles = extra.get("1H") or extra.get("1h")
        return self._evaluate(candles, htf_candles)

    # ------------------------------------------------------------------
    # Core evaluation
    # ------------------------------------------------------------------

    def _evaluate(self, candles: list, htf_candles) -> Optional[StrategyResult]:
        min_bars = max(self.choch_lookback + 5, 35)
        if len(candles) < min_bars:
            return StrategyResult(
                signal=Signal.HOLD,
                indicators={"sweep_detected": 0.0, "choch_detected": 0.0,
                            "fib_low": 0.0, "fib_high": 0.0},
                hold_reason=f"Aquecendo ({len(candles)}/{min_bars} barras 5M)",
                criteria_met=0,
                criteria_total=5,
            )

        ltf_df = pd.DataFrame([
            {"open": c.open, "high": c.high, "low": c.low, "close": c.close}
            for c in candles
        ])

        # ── Critério 1: varredura HTF ──────────────────────────────────
        htf_sweep = None
        if htf_candles and len(htf_candles) >= 12:
            htf_df = pd.DataFrame([
                {"open": c.open, "high": c.high, "low": c.low, "close": c.close}
                for c in htf_candles
            ])
            htf_sweep = self._detect_htf_sweep(htf_df)

        # Fallback: usar LTF como proxy quando não há contexto HTF
        if htf_sweep is None:
            htf_sweep = self._detect_htf_sweep(ltf_df)

        c1 = htf_sweep is not None
        met = 1 if c1 else 0

        if not c1:
            return StrategyResult(
                signal=Signal.HOLD,
                indicators=self._inds(ltf_df),
                hold_reason="Aguardando captura de liquidez no HTF (varredura + rejeição)",
                criteria_met=met,
                criteria_total=5,
            )

        # ── Critério 2: rejeição confirmada ───────────────────────────
        # Já garantido pelo _detect_htf_sweep (close de volta ao range)
        met += 1

        # ── Critério 3: CHoCH no 5M ───────────────────────────────────
        choch = self._detect_choch(ltf_df, htf_sweep["type"])
        c3 = choch is not None
        met += 1 if c3 else 0

        if not c3:
            return StrategyResult(
                signal=Signal.HOLD,
                indicators=self._inds(ltf_df, sweep=htf_sweep),
                hold_reason=(
                    f"Varredura {htf_sweep['type'].upper()} detectada — "
                    "aguardando CHoCH no 5M"
                ),
                criteria_met=met,
                criteria_total=5,
            )

        # ── Critério 4: preço na zona Fibonacci ───────────────────────
        direction    = choch["direction"]
        sweep_ext    = choch["sweep_extreme"]
        choch_anchor = choch.get("choch_high") or choch.get("choch_low", sweep_ext)
        current      = float(ltf_df.iloc[-1]["close"])

        if direction == "buy":
            move          = choch_anchor - sweep_ext
            fib_entry_lo  = choch_anchor - self.fib_high * move   # 78.6% deep
            fib_entry_hi  = choch_anchor - self.fib_low  * move   # 61.8% shallow
        else:
            move          = sweep_ext - choch_anchor
            fib_entry_lo  = choch_anchor + self.fib_low  * move
            fib_entry_hi  = choch_anchor + self.fib_high * move

        c4 = fib_entry_lo <= current <= fib_entry_hi
        met += 1 if c4 else 0

        if not c4:
            return StrategyResult(
                signal=Signal.HOLD,
                indicators=self._inds(ltf_df, sweep=htf_sweep, choch=choch,
                                      fib_lo=fib_entry_lo, fib_hi=fib_entry_hi),
                hold_reason=(
                    f"CHoCH confirmado — aguardando recuo para zona Fib "
                    f"[{fib_entry_lo:.4f} – {fib_entry_hi:.4f}]"
                ),
                criteria_met=met,
                criteria_total=5,
            )

        # ── Critério 5: liquidez (ATR ativo) ──────────────────────────
        atr_series  = self._atr(ltf_df)
        atr_now     = float(atr_series.iloc[-1])
        atr_avg     = float(atr_series.rolling(20, min_periods=5).mean().iloc[-1])
        c5          = atr_now >= 0.25 * atr_avg   # ATR não colapsou
        met += 1 if c5 else 0

        # ── Cálculo de entrada / stop / alvos ─────────────────────────
        if direction == "buy":
            entry = current
            stop  = sweep_ext - self.atr_sl_mult * atr_now
            risk  = entry - stop
            if risk <= 0:
                return StrategyResult(
                    signal=Signal.HOLD,
                    indicators=self._inds(ltf_df),
                    hold_reason="Risco calculado inválido (stop acima da entrada)",
                    criteria_met=met, criteria_total=5,
                )
            tp1 = entry + self.tp1_rr * risk
            tp2 = htf_sweep.get("htf_tp_level", entry + 2.5 * risk)
            sig = Signal.BUY if c5 else Signal.HOLD
            reason = (
                "CTE BUY: captura SSL + CHoCH + zona Fib confirmados"
                if sig == Signal.BUY
                else "Zona Fib atingida — ATR baixo, aguardando liquidez"
            )
        else:
            entry = current
            stop  = sweep_ext + self.atr_sl_mult * atr_now
            risk  = stop - entry
            if risk <= 0:
                return StrategyResult(
                    signal=Signal.HOLD,
                    indicators=self._inds(ltf_df),
                    hold_reason="Risco calculado inválido (stop abaixo da entrada)",
                    criteria_met=met, criteria_total=5,
                )
            tp1 = entry - self.tp1_rr * risk
            tp2 = htf_sweep.get("htf_tp_level", entry - 2.5 * risk)
            sig = Signal.SELL if c5 else Signal.HOLD
            reason = (
                "CTE SELL: captura BSL + CHoCH + zona Fib confirmados"
                if sig == Signal.SELL
                else "Zona Fib atingida — ATR baixo, aguardando liquidez"
            )

        return StrategyResult(
            signal=sig,
            indicators=self._inds(
                ltf_df, sweep=htf_sweep, choch=choch,
                fib_lo=fib_entry_lo, fib_hi=fib_entry_hi,
                entry=entry, stop=stop, tp1=tp1,
            ),
            metadata={
                "entry":        round(entry, 4),
                "stop":         round(stop, 4),
                "tp1":          round(tp1, 4),
                "tp2":          round(tp2, 4),
                "sweep_type":   htf_sweep["type"],
                "sweep_extreme": round(sweep_ext, 4),
                "choch_level":  round(choch["choch_level"], 4),
                "fib_zone":     f"{fib_entry_lo:.4f}–{fib_entry_hi:.4f}",
                "direction":    direction,
            },
            hold_reason="" if sig != Signal.HOLD else reason,
            criteria_met=met,
            criteria_total=5,
        )

    # ------------------------------------------------------------------
    # HTF sweep detection
    # ------------------------------------------------------------------

    def _detect_htf_sweep(self, df: pd.DataFrame) -> dict | None:
        """
        Busca a varredura de liquidez mais recente no dataframe fornecido.

        SSL (sell-side liquidity):
          Bar rompe abaixo do swing low anterior e fecha acima dele → pavio
          longo inferior → sinal de reversão de alta.

        BSL (buy-side liquidity):
          Bar rompe acima do swing high anterior e fecha abaixo dele → pavio
          longo superior → sinal de reversão de baixa.
        """
        lookback = min(self.htf_swing_lookback, len(df) - 3)
        if lookback < 5:
            return None

        for i in range(len(df) - 2, max(lookback, 5) - 1, -1):
            bar   = df.iloc[i]
            prior = df.iloc[max(0, i - lookback): i]
            if len(prior) < 5:
                continue

            bar_range = bar["high"] - bar["low"]
            if bar_range < 1e-9:
                continue

            swing_low  = float(prior["low"].min())
            swing_high = float(prior["high"].max())

            # ── SSL: varredura de mínima ───────────────────────────
            if bar["low"] < swing_low and bar["close"] > swing_low:
                body_lo  = min(bar["open"], bar["close"])
                wick_len = body_lo - bar["low"]
                if wick_len / bar_range >= self.wick_ratio:
                    return {
                        "type":          "ssl",
                        "bar_idx":       i,
                        "sweep_extreme": float(bar["low"]),
                        "swing_level":   swing_low,
                        "htf_tp_level":  swing_high,
                    }

            # ── BSL: varredura de máxima ───────────────────────────
            if bar["high"] > swing_high and bar["close"] < swing_high:
                body_hi  = max(bar["open"], bar["close"])
                wick_len = bar["high"] - body_hi
                if wick_len / bar_range >= self.wick_ratio:
                    return {
                        "type":          "bsl",
                        "bar_idx":       i,
                        "sweep_extreme": float(bar["high"]),
                        "swing_level":   swing_high,
                        "htf_tp_level":  swing_low,
                    }

        return None

    # ------------------------------------------------------------------
    # CHoCH detection on LTF
    # ------------------------------------------------------------------

    def _detect_choch(self, df: pd.DataFrame, sweep_type: str) -> dict | None:
        """
        Após a varredura HTF, busca a quebra de estrutura (CHoCH) no LTF.

        Para SSL (buy):
          Localiza o fundo mais recente → busca a última máxima descendente
          antes desse fundo → confirma se o preço atual fechou acima dela.

        Para BSL (sell):
          Localiza o topo mais recente → busca a última mínima ascendente
          antes desse topo → confirma se o preço atual fechou abaixo dela.
        """
        lookback = min(self.choch_lookback, len(df) - 3)
        if lookback < 10:
            return None

        recent = df.iloc[-lookback:].reset_index(drop=True)

        if sweep_type == "ssl":
            # Fundo da varredura no LTF
            low_idx    = int(recent["low"].idxmin())
            post_sweep = recent.iloc[low_idx:]
            if len(post_sweep) < 3:
                return None

            # Última máxima descendente (CHoCH level) — maior high nos primeiros
            # candles após o fundo antes de qualquer impulso
            search_bars = post_sweep.iloc[1: min(12, len(post_sweep))]
            if search_bars.empty:
                return None
            choch_level = float(search_bars["high"].max())
            last_close  = float(df.iloc[-1]["close"])

            if last_close > choch_level:
                choch_high = float(df["high"].iloc[-min(8, len(df)):].max())
                return {
                    "direction":     "buy",
                    "choch_level":   choch_level,
                    "sweep_extreme": float(post_sweep.iloc[0]["low"]),
                    "choch_high":    choch_high,
                }

        elif sweep_type == "bsl":
            high_idx   = int(recent["high"].idxmax())
            post_sweep = recent.iloc[high_idx:]
            if len(post_sweep) < 3:
                return None

            search_bars = post_sweep.iloc[1: min(12, len(post_sweep))]
            if search_bars.empty:
                return None
            choch_level = float(search_bars["low"].min())
            last_close  = float(df.iloc[-1]["close"])

            if last_close < choch_level:
                choch_low = float(df["low"].iloc[-min(8, len(df)):].min())
                return {
                    "direction":     "sell",
                    "choch_level":   choch_level,
                    "sweep_extreme": float(post_sweep.iloc[0]["high"]),
                    "choch_low":     choch_low,
                }

        return None

    # ------------------------------------------------------------------
    # ATR helper
    # ------------------------------------------------------------------

    def _atr(self, df: pd.DataFrame) -> pd.Series:
        hl   = df["high"] - df["low"]
        hc   = (df["high"] - df["close"].shift()).abs()
        lc   = (df["low"]  - df["close"].shift()).abs()
        tr   = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        return tr.rolling(self.atr_period, min_periods=1).mean()

    # ------------------------------------------------------------------
    # Indicators dict for frontend
    # ------------------------------------------------------------------

    def _inds(
        self, df,
        sweep=None, choch=None,
        fib_lo=0.0, fib_hi=0.0,
        entry=0.0, stop=0.0, tp1=0.0,
    ) -> dict:
        last = df.iloc[-1]
        return {
            "close":           round(float(last["close"]), 4),
            "high":            round(float(last["high"]),  4),
            "low":             round(float(last["low"]),   4),
            "sweep_detected":  1.0 if sweep else 0.0,
            "sweep_type":      sweep["type"] if sweep else "",
            "choch_detected":  1.0 if choch else 0.0,
            "fib_low":         round(fib_lo, 4),
            "fib_high":        round(fib_hi, 4),
            "entry":           round(entry, 4),
            "stop":            round(stop,  4),
            "tp1":             round(tp1,   4),
        }
