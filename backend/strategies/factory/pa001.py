"""
strategies/pivot_sniper.py — EMA Pivot Sniper  (estratégia autoral)

Filosofia: "Slow down to speed up"
────────────────────────────────────────────────────────────────────────────────
As outras estratégias da plataforma operam com indicadores. Esta opera com
ESTRUTURA. A diferença é fundamental: indicadores descrevem o que o preço
*está a fazer*; a estrutura de pivôs revela o que o mercado *vai fazer*.

Três camadas de confluência obrigatórias (AND lógico):

  1. TENDÊNCIA 1-2-3
     EMA de referência (50 por defeito) + estrutura de pivôs:
       • BULL → close > EMA AND últimos swing-lows em ascensão (Higher Lows)
       • BEAR → close < EMA AND últimos swing-highs em descida (Lower Highs)
     Recusa entrar em mercados laterais ou sem estrutura clara.

  2. ZONA DE VALOR (Sniper Setup)
     Preço recua até à EMA E, ao mesmo tempo, toca um nível S/R horizontal
     testado ≥ 2 vezes. Dois grupos de traders activam simultaneamente →
     pressão dupla → impulso mais forte e mais rápido.

  3. GATILHO — Candle de Rejeição
     • Hammer  / Pin Bar bullish  → LONG
     • Shooting Star / Pin Bar bearish → SHORT
     • Engolfo (Bullish/Bearish Engulfing)
     Confirma que a pressão vendedora/compradora foi absorvida.

Por que funciona?
     Os traders de médias entram quando o preço toca a EMA.
     Os traders de price action entram quando o preço toca o S/R.
     Quando os dois pontos coincidem, ambos os grupos bolam ao mesmo tempo —
     isso é "combustível duplo" para o movimento direcional.

Parâmetros chave:
     ema_period        — A "bússola" do mercado. 50 para médio prazo (recomendado).
                         Cada ativo tem o seu ritmo; 20 para mais activo, 100 para
                         mais conservador.
     sr_min_touches    — Quantas vezes um nível deve ser testado para ser válido.
                         2 é suficiente; 3 é mais conservador.
     tp_rr             — Risk:Reward ratio. 3.0 = alvo a 3× o risco (1:3 do setup).

Risk:
     SL  = ATR × sl_mult abaixo/acima da entrada
     TP1 = SL_distância × tp_rr   (fecha 50% da posição)
     Trailing Stop após TP1 baseado em ATR.
"""

from __future__ import annotations
from typing import Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class PivotSniperStrategy(BaseStrategy):

    # ── Metadados ────────────────────────────────────────────────────────────

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="PA001",
            name="PA001 - EMA Pivot Sniper",
            description = (
                "Estratégia autoral de alta precisão baseada em confluência tripla: "
                "(1) Tendência confirmada pela estrutura de pivôs 1-2-3 — higher highs "
                "+ higher lows para alta, lower highs + lower lows para baixa, "
                "filtrada pela EMA de referência; "
                "(2) Pullback do preço até à EMA que coincide com nível S/R horizontal "
                "testado ≥ 2 vezes — dois grupos de traders activam simultaneamente, "
                "gerando pressão dupla; "
                "(3) Candle de rejeição na zona: Hammer, Shooting Star ou Engolfo. "
                "Produz poucos sinais mas de alta convicção. "
                "Risk:Reward mínimo de 1:3 configurável."
            ),
            tags   = ["price_action", "structure", "pivot", "ema", "sr", "confluence"],
            criteria=[
                {"id": "c1_trend", "label": "C1 Tendência 1-2-3", "description": "Bias direcional por EMA e estrutura de pivôs."},
                {"id": "c2_value_zone", "label": "C2 Zona EMA+S/R", "description": "Pullback até a EMA em confluência com suporte/resistência."},
                {"id": "c3_rejection", "label": "C3 Candle de rejeição", "description": "Hammer, shooting star ou engolfo na zona de valor."},
            ],
            params = {
                "ema_period": ParamDef(
                    type="int", default=50, min=10, max=200, step=5,
                    description="EMA de referência — a 'bússola' do activo (20/50/100)"),
                "pivot_lookback": ParamDef(
                    type="int", default=4, min=2, max=10, step=1,
                    description="Barras de cada lado para detectar swing-highs/lows"),
                "trend_pivots": ParamDef(
                    type="int", default=3, min=2, max=6, step=1,
                    description="Nº de pivôs recentes para confirmar estrutura 1-2-3"),
                "sr_lookback": ParamDef(
                    type="int", default=100, min=40, max=200, step=10,
                    description="Barras para trás para detectar níveis S/R"),
                "sr_min_touches": ParamDef(
                    type="int", default=2, min=2, max=4, step=1,
                    description="Toques mínimos num nível para ser considerado S/R válido"),
                "sr_cluster_mult": ParamDef(
                    type="float", default=0.6, min=0.2, max=1.5, step=0.1,
                    description="Tolerância do cluster S/R em múltiplos de ATR"),
                "ema_proximity_mult": ParamDef(
                    type="float", default=1.0, min=0.3, max=2.0, step=0.1,
                    description="Distância máxima da EMA para 'pullback válido' (× ATR)"),
                "sr_confluence_mult": ParamDef(
                    type="float", default=0.8, min=0.3, max=2.0, step=0.1,
                    description="Distância máxima de S/R para confluência (× ATR)"),
                "rejection_mult": ParamDef(
                    type="float", default=1.5, min=1.0, max=4.0, step=0.1,
                    description="Sombra mínima do candle de rejeição (× corpo)"),
                "atr_period": ParamDef(
                    type="int", default=14, min=5, max=30, step=1,
                    description="Período do ATR para risco adaptativo"),
                "sl_mult": ParamDef(
                    type="float", default=2.5, min=0.5, max=4.0, step=0.1,
                    description="Multiplicador ATR para Stop Loss"),
                "tp_rr": ParamDef(
                    type="float", default=3.0, min=1.5, max=6.0, step=0.5,
                    description="Risk:Reward ratio — alvo = SL_distância × tp_rr (1:3 recomendado)"),
                "ts_mult": ParamDef(
                    type="float", default=2.0, min=0.5, max=5.0, step=0.5,
                    description="Multiplicador ATR para Trailing Stop (ativado após TP1)"),
            },
        )

    # ── Inicialização ─────────────────────────────────────────────────────────

    def __init__(self):
        p = self.info().params
        self.ema_period        = p["ema_period"].default
        self.pivot_lookback    = p["pivot_lookback"].default
        self.trend_pivots      = p["trend_pivots"].default
        self.sr_lookback       = p["sr_lookback"].default
        self.sr_min_touches    = p["sr_min_touches"].default
        self.sr_cluster_mult   = p["sr_cluster_mult"].default
        self.ema_proximity_mult= p["ema_proximity_mult"].default
        self.sr_confluence_mult= p["sr_confluence_mult"].default
        self.rejection_mult    = p["rejection_mult"].default
        self.atr_period        = p["atr_period"].default
        self.sl_mult           = p["sl_mult"].default
        self.tp_rr             = p["tp_rr"].default
        self.ts_mult           = p["ts_mult"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    # ── Compute — lógica principal ────────────────────────────────────────────

    def compute(self, candles: list) -> Optional[StrategyResult]:
        criteria_total = 3
        min_len = max(self.ema_period, self.sr_lookback, self.atr_period) + self.pivot_lookback * 2 + 5
        if len(candles) < min_len:
            return None

        df = pd.DataFrame([
            {"open": c.open, "high": c.high, "low": c.low,
             "close": c.close, "volume": c.volume}
            for c in candles
        ])

        # ── Indicadores base ──────────────────────────────────────────────────
        ema_s  = ta.ema(df["close"], length=self.ema_period)
        atr_s  = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        if ema_s is None or atr_s is None:
            return None

        ema_now = float(ema_s.iloc[-1])
        atr_now = float(atr_s.iloc[-1])
        close   = candles[-1].close

        if atr_now == 0:
            return None

        # ── 1. Detecção de pivôs ──────────────────────────────────────────────
        # Usa as últimas sr_lookback velas para S/R + structure
        lb    = self.pivot_lookback
        swing_highs, swing_lows = self._find_pivots(candles, lb)

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return StrategyResult(
                signal      = Signal.HOLD,
                criteria_met=0,
                criteria_total=criteria_total,
                hold_reason = "Pivôs insuficientes — acumulando estrutura",
                indicators  = self._indicators(close, ema_now, atr_now, 0, 0.0),
            )

        # ── 2. Bias de tendência (estrutura 1-2-3 ou suave via EMA) ──────────
        bias, bias_reason, bias_soft = self._trend_bias(
            swing_highs, swing_lows, close, ema_now)

        if bias == 0:
            return StrategyResult(
                signal      = Signal.HOLD,
                criteria_met=0,
                criteria_total=criteria_total,
                hold_reason = f"Tendência indefinida — {bias_reason}",
                indicators  = self._indicators(close, ema_now, atr_now, 0, 0.0),
            )

        # ── 3. Pullback à EMA (zona de valor dinâmica) ────────────────────────
        ema_dist = abs(close - ema_now)
        near_ema = ema_dist <= atr_now * self.ema_proximity_mult

        # Para LONG: preço deve estar acima ou na EMA (pullback de cima)
        # Para SHORT: preço deve estar abaixo ou na EMA (pullback de baixo)
        correct_side = (bias == 1 and close >= ema_now - atr_now * 0.2) or \
                       (bias == -1 and close <= ema_now + atr_now * 0.2)

        soft_extra = {"bias_soft": float(bias_soft)}

        if not (near_ema and correct_side):
            dist_pct = round(ema_dist / ema_now * 100, 2)
            return StrategyResult(
                signal      = Signal.HOLD,
                criteria_met=1,
                criteria_total=criteria_total,
                hold_reason = f"Aguardando pullback para a EMA{self.ema_period} — distância {dist_pct}%",
                indicators  = self._indicators(close, ema_now, atr_now, bias, 0.0,
                                               extra=soft_extra),
            )

        # ── 4. Confluência com S/R horizontal ────────────────────────────────
        sr_levels = self._find_sr_levels(candles, atr_now)
        sr_ok, sr_level = self._near_sr(close, sr_levels, atr_now)

        if not sr_ok:
            sr_ok, sr_level = self._near_any_pivot(
                close, swing_highs, swing_lows, atr_now, bias)
            if not sr_ok:
                return StrategyResult(
                    signal      = Signal.HOLD,
                    criteria_met=1,
                    criteria_total=criteria_total,
                    hold_reason = "Sem confluência S/R na zona EMA — aguardando nível testado",
                    indicators  = self._indicators(close, ema_now, atr_now, bias, 0.0,
                                                   extra=soft_extra),
                )

        # ── 5. Candle de rejeição na zona de confluência ──────────────────────
        direction = "bull" if bias == 1 else "bear"
        rejection, pattern = self._rejection_candle(candles, direction)

        if not rejection:
            return StrategyResult(
                signal      = Signal.HOLD,
                criteria_met=2,
                criteria_total=criteria_total,
                hold_reason = f"Na zona EMA+S/R ({sr_level:.0f}) — aguardando candle de rejeição",
                indicators  = self._indicators(close, ema_now, atr_now, bias, sr_level,
                                               extra=soft_extra),
            )

        # ── Sinal confirmado — calcular risco ─────────────────────────────────
        signal    = Signal.BUY if bias == 1 else Signal.SELL
        sl_dist   = atr_now * self.sl_mult
        tp1_dist  = sl_dist * self.tp_rr

        if signal == Signal.BUY:
            sl_price  = round(close - sl_dist, 2)
            tp1_price = round(close + tp1_dist, 2)
        else:
            sl_price  = round(close + sl_dist, 2)
            tp1_price = round(close - tp1_dist, 2)

        return StrategyResult(
            signal      = signal,
            criteria_met=criteria_total if signal in (Signal.BUY, Signal.SELL) else 0,
            criteria_total=criteria_total,
            hold_reason = "",
            indicators  = self._indicators(close, ema_now, atr_now, bias, sr_level,
                                           extra={"sr_level":  round(sr_level, 2),
                                                  "pattern":   pattern,
                                                  "bias_soft": float(bias_soft)}),
            metadata = {
                "sl_price":  sl_price,
                "tp1_price": tp1_price,
                "sl_pct":    round(sl_dist / close, 5),
                "tp1_pct":   round(tp1_dist / close, 5),
                "ts_pct":    round(atr_now * self.ts_mult / close, 5),
                "sr_level":  round(sr_level, 2),
                "pattern":   pattern,
                "bias":      "bull" if bias == 1 else "bear",
                "rr":        self.tp_rr,
                "regime_state": f"pivot_sniper_{direction}",  # para análise posterior
            },
        )

    # ── Detecção de pivôs ─────────────────────────────────────────────────────

    def _find_pivots(self, candles: list, lb: int) -> tuple[list[float], list[float]]:
        """Swing-highs e swing-lows nos últimos sr_lookback candles."""
        window = candles[-(self.sr_lookback + lb * 2):]
        n      = len(window)
        highs, lows = [], []
        for i in range(lb, n - lb):
            h = window[i].high
            l = window[i].low
            if all(window[j].high <= h for j in range(i - lb, i + lb + 1) if j != i):
                highs.append(h)
            if all(window[j].low >= l for j in range(i - lb, i + lb + 1) if j != i):
                lows.append(l)
        return highs, lows

    # ── Bias de tendência (estrutura 1-2-3) ───────────────────────────────────

    def _trend_bias(
        self,
        swing_highs: list[float],
        swing_lows:  list[float],
        close:       float,
        ema:         float,
    ) -> tuple[int, str, bool]:
        """
        Retorna (bias, motivo, is_soft).

        bias   : 1 = BULL | -1 = BEAR | 0 = NEUTRO
        is_soft: True = bias apenas por posição na EMA, sem estrutura 1-2-3 confirmada.
                 Bias suave ainda exige confluência S/R + candle de rejeição — o
                 filtro downstream compensa a ausência de pivôs claros.

        Confirmação 1-2-3 simplificada:
          BULL forte → close > EMA AND últimos N lows em ascensão (Higher Lows)
          BEAR forte → close < EMA AND últimos N highs em descida (Lower Highs)
          BULL suave → close > EMA, sem HL confirmados (mercado comprimido)
          BEAR suave → close < EMA, sem LH confirmados (mercado comprimido)
        """
        n = self.trend_pivots
        recent_highs = swing_highs[-n:] if len(swing_highs) >= n else swing_highs
        recent_lows  = swing_lows[-n:]  if len(swing_lows)  >= n else swing_lows

        hl_count = sum(1 for i in range(1, len(recent_lows))
                       if recent_lows[i] > recent_lows[i - 1])
        lh_count = sum(1 for i in range(1, len(recent_highs))
                       if recent_highs[i] < recent_highs[i - 1])

        above_ema = close > ema
        below_ema = close < ema

        if above_ema and hl_count >= 1:
            return 1, "", False    # BULL forte: EMA + Higher Lows
        if below_ema and lh_count >= 1:
            return -1, "", False   # BEAR forte: EMA + Lower Highs

        # Bias suave: posição na EMA define direção mas sem pivôs confirmados.
        # Em mercados comprimidos (triângulos, ranges), a estrutura 1-2-3 não forma
        # pivôs claros — mas a confluência S/R + candle de rejeição ainda é válida.
        if above_ema:
            return 1, "", True     # BULL suave: acima da EMA, sem HL
        if below_ema:
            return -1, "", True    # BEAR suave: abaixo da EMA, sem LH

        return 0, "preço na EMA sem direção", False

    # ── Detecção de S/R horizontais ───────────────────────────────────────────

    def _find_sr_levels(self, candles: list, atr: float) -> list[float]:
        """
        Agrupa pivôs próximos em clusters; retorna apenas níveis testados
        ≥ sr_min_touches vezes.
        """
        highs, lows = self._find_pivots(candles, self.pivot_lookback)
        all_pivots  = highs + lows
        tolerance   = atr * self.sr_cluster_mult

        clusters: list[dict] = []
        for p in all_pivots:
            placed = False
            for c in clusters:
                if abs(p - c["price"]) <= tolerance:
                    # Atualiza média ponderada do cluster
                    c["count"] += 1
                    c["price"]  = (c["price"] * (c["count"] - 1) + p) / c["count"]
                    placed = True
                    break
            if not placed:
                clusters.append({"price": p, "count": 1})

        return [c["price"] for c in clusters if c["count"] >= self.sr_min_touches]

    def _near_sr(
        self, close: float, sr_levels: list[float], atr: float
    ) -> tuple[bool, float]:
        """Retorna (True, level) se o close está dentro da tolerância de confluência."""
        tolerance = atr * self.sr_confluence_mult
        best      = min(sr_levels, key=lambda l: abs(l - close), default=None)
        if best is not None and abs(best - close) <= tolerance:
            return True, best
        return False, 0.0

    def _near_any_pivot(
        self,
        close: float,
        swing_highs: list[float],
        swing_lows:  list[float],
        atr: float,
        bias: int,
    ) -> tuple[bool, float]:
        """
        Fallback: aceita o swing-pivot mais recente mesmo sem multi-toque,
        mas apenas na direcção correcta.
          BULL → cerca de um swing-low recente (suporte)
          BEAR → cerca de um swing-high recente (resistência)
        """
        tolerance = atr * self.sr_confluence_mult
        candidates = swing_lows[-3:] if bias == 1 else swing_highs[-3:]
        best = min(candidates, key=lambda l: abs(l - close), default=None)
        if best is not None and abs(best - close) <= tolerance:
            return True, best
        return False, 0.0

    # ── Candle de rejeição ────────────────────────────────────────────────────

    def _rejection_candle(
        self, candles: list, direction: str
    ) -> tuple[bool, str]:
        """
        Detecta padrões de rejeição na vela mais recente:
          • Hammer / Pin Bar (bull) / Shooting Star (bear)
          • Bullish / Bearish Engulfing
        Retorna (True, nome_do_padrão) ou (False, "").
        """
        if len(candles) < 2:
            return False, ""

        curr = candles[-1]
        prev = candles[-2]

        c_open  = curr.open
        c_close = curr.close
        c_high  = curr.high
        c_low   = curr.low

        body         = abs(c_close - c_open)
        full_range   = c_high - c_low
        upper_shadow = c_high - max(c_close, c_open)
        lower_shadow = min(c_close, c_open) - c_low

        # Corpo mínimo: evita dojis que têm sombras grandes mas sem direcção
        min_body = full_range * 0.08

        if full_range == 0 or body < min_body:
            return False, ""

        mult = self.rejection_mult

        if direction == "bull":
            # Hammer / Pin Bar bullish:
            #   sombra inferior ≥ mult × corpo + fecha acima da abertura
            is_hammer = (
                lower_shadow >= body * mult and
                upper_shadow <= body and        # sombra superior pequena
                c_close > c_open                # candle de alta
            )
            # Bullish Engulfing:
            #   vela actual (alta) engolfa completamente a vela anterior (baixa)
            is_engulfing = (
                c_close > c_open and            # actual = alta
                prev.close < prev.open and      # anterior = baixa
                c_close > prev.open and         # engolfa topo do corpo anterior
                c_open  < prev.close            # engolfa base do corpo anterior
            )
            if is_hammer:
                return True, "hammer"
            if is_engulfing:
                return True, "engulfing_bull"

        else:  # bear
            # Shooting Star / Pin Bar bearish:
            #   sombra superior ≥ mult × corpo + fecha abaixo da abertura
            is_star = (
                upper_shadow >= body * mult and
                lower_shadow <= body and        # sombra inferior pequena
                c_close < c_open                # candle de baixa
            )
            # Bearish Engulfing
            is_engulfing = (
                c_close < c_open and            # actual = baixa
                prev.close > prev.open and      # anterior = alta
                c_close < prev.open and         # engolfa base do corpo anterior
                c_open  > prev.close            # engolfa topo do corpo anterior
            )
            if is_star:
                return True, "shooting_star"
            if is_engulfing:
                return True, "engulfing_bear"

        return False, ""

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _indicators(
        close: float,
        ema: float,
        atr: float,
        bias: int,
        sr_level: float,
        extra: dict | None = None,
    ) -> dict:
        ind = {
            "close":       round(close, 2),
            "ema":         round(ema, 2),
            "atr":         round(atr, 4),
            "bias":        float(bias),      # 1 bull | -1 bear | 0 neutro
            "ema_dist_pct": round(abs(close - ema) / ema * 100, 3) if ema else 0,
            "sr_level":    round(sr_level, 2) if sr_level else 0.0,
        }
        if extra:
            ind.update(extra)
        return ind
