"""
strategies/double_bollinger.py — S006 Double Bollinger Band (Straight Kim)
"""

from __future__ import annotations
from typing import Optional
import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class DoubleBollingerStrategy(BaseStrategy):

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="MR002",
            name="MR002 - Double Bollinger Band",
            description = (
                "Estratégia baseada no setup Double B (Straight Kim). "
                "Utiliza bandas externas (4, 4, Open) e internas (20, 2, Close). "
                "1. Reversão: Preço excede ambas as bandas e fecha dentro da interna com rejeição. "
                "2. Rompimento: Expansão forte rompendo as bandas e a zona de oferta/demanda recente. "
                "Foca em R:R de 3:1 para consistência de longo prazo."
            ),
            tags        = ["bollinger", "reversal", "breakout", "price_action"],
            recommended_timeframe = "15m",
            criteria=[
                {"id": "c1_outer_band", "label": "C1 Banda 4σ", "description": "Preço toca/excede banda externa de exaustão."},
                {"id": "c2_inner_band", "label": "C2 Banda 2σ", "description": "Rejeição em relação à banda interna."},
                {"id": "c3_sr_zone", "label": "C3 Zona R/S", "description": "Preço interage com zona recente de suporte/resistência."},
            ],
            params = {
                "outer_length": ParamDef(type="int",   default=4,   min=2,   max=10, description="Período da banda externa"),
                "outer_std":    ParamDef(type="float", default=4.0, min=2.0, max=6.0, description="Desvio padrão da banda externa"),
                "inner_length": ParamDef(type="int",   default=20,  min=10,  max=50, description="Período da banda interna"),
                "inner_std":    ParamDef(type="float", default=2.0, min=1.0, max=3.0, description="Desvio padrão da banda interna"),
                "rr_ratio":     ParamDef(type="float", default=3.0, min=1.5, max=5.0, description="Relação Risco:Recompensa alvo"),
                "zone_lookback":ParamDef(type="int",   default=30,  min=20,  max=50, description="Lookback para zonas de suporte/resistência"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.outer_length  = p["outer_length"].default
        self.outer_std     = p["outer_std"].default
        self.inner_length  = p["inner_length"].default
        self.inner_std     = p["inner_std"].default
        self.rr_ratio      = p["rr_ratio"].default
        self.zone_lookback = p["zone_lookback"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        if len(candles) < self.zone_lookback + 2:
            return None

        df = pd.DataFrame([{
            "open": c.open, "high": c.high, "low": c.low, "close": c.close
        } for c in candles])

        # ── Cálculo das Bandas ───────────────────────────────────────────────
        # Banda Externa (Source: Open)
        o_bb = ta.bbands(df["open"], length=self.outer_length, std=self.outer_std)
        # Banda Interna (Source: Close)
        i_bb = ta.bbands(df["close"], length=self.inner_length, std=self.inner_std)

        if o_bb is None or i_bb is None or len(o_bb) == 0 or len(i_bb) == 0:
            return None

        # Mapeamento de nomes (pandas_ta usa sufixos)
        o_upper = o_bb.iloc[:, 2] # Upper
        o_lower = o_bb.iloc[:, 0] # Lower
        i_upper = i_bb.iloc[:, 2] # Upper
        i_lower = i_bb.iloc[:, 0] # Lower
        i_mid   = i_bb.iloc[:, 1] # SMA20

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        signal_type = None
        hold_reason = "Aguardando toque em bandas externas ou rompimento de zona"
        metadata    = {}

        # ── 1. Cenário A: Reversão à Média (Mean Reversion) ─────────────────
        
        # LONG: Mínima anterior furou ambas, atual fechou dentro com rejeição
        signal_long_rev = (prev["low"] < o_lower.iloc[-2]) and (prev["low"] < i_lower.iloc[-2])
        conf_long_rev   = (curr["low"] < i_lower.iloc[-1]) and (curr["close"] > i_lower.iloc[-1])

        if signal_long_rev and conf_long_rev:
            entry = curr["close"]
            stop  = min(curr["low"], o_lower.iloc[-1]) * 0.9998
            risk  = entry - stop
            if risk > 0:
                signal_type = Signal.BUY
                hold_reason = "Reversão Double B (Long)"
                metadata = {"sl_price": round(stop, 2), "tp1_price": round(entry + (risk * self.rr_ratio), 2)}

        # SHORT: Máxima anterior furou ambas, atual fechou dentro com rejeição
        signal_short_rev = (prev["high"] > o_upper.iloc[-2]) and (prev["high"] > i_upper.iloc[-2])
        conf_short_rev   = (curr["high"] > i_upper.iloc[-1]) and (curr["close"] < i_upper.iloc[-1])

        if signal_short_rev and conf_short_rev:
            entry = curr["close"]
            stop  = max(curr["high"], o_upper.iloc[-1]) * 1.0002
            risk  = stop - entry
            if risk > 0:
                signal_type = Signal.SELL
                hold_reason = "Reversão Double B (Short)"
                metadata = {"sl_price": round(stop, 2), "tp1_price": round(entry - (risk * self.rr_ratio), 2)}

        # ── 2. Cenário B: Rompimento (Breakout) ──────────────────────────────
        
        # Filtro de zona (Máxima/Mínima dos últimos N candles)
        recent_high = df["high"].iloc[-(self.zone_lookback+1):-1].max()
        recent_low  = df["low"].iloc[-(self.zone_lookback+1):-1].min()

        if signal_type is None:
            # LONG: Fechamento acima de ambas as bandas E acima da resistência recente
            if curr["close"] > o_upper.iloc[-1] and curr["close"] > i_upper.iloc[-1]:
                if curr["close"] > recent_high:
                    entry = curr["close"]
                    stop  = i_lower.iloc[-1]
                    risk  = entry - stop
                    if risk > 0:
                        signal_type = Signal.BUY
                        hold_reason = "Rompimento Double B (Long)"
                        metadata = {"sl_price": round(stop, 2), "tp1_price": round(entry + (risk * 2.0), 2)}
                else:
                    hold_reason = f"Rompimento Pendente: Preço ({curr['close']:.2f}) abaixo da resistência ({recent_high:.2f})"

            # SHORT: Fechamento abaixo de ambas as bandas E abaixo do suporte recente
            elif curr["close"] < o_lower.iloc[-1] and curr["close"] < i_lower.iloc[-1]:
                if curr["close"] < recent_low:
                    entry = curr["close"]
                    stop  = i_upper.iloc[-1]
                    risk  = stop - entry
                    if risk > 0:
                        signal_type = Signal.SELL
                        hold_reason = "Rompimento Double B (Short)"
                        metadata = {"sl_price": round(stop, 2), "tp1_price": round(entry - (risk * 2.0), 2)}
                else:
                    hold_reason = f"Rompimento Pendente: Preço ({curr['close']:.2f}) acima do suporte ({recent_low:.2f})"

        # Fallback para HOLD ou Reversão processada
        if signal_type is None:
            if hold_reason == "Aguardando toque em bandas externas ou rompimento de zona":
                hold_reason = f"Monitorando Zonas: R {recent_high:.2f} | S {recent_low:.2f}"

            return StrategyResult(
                signal=Signal.HOLD,
                criteria_met=0,
                criteria_total=3,
                hold_reason=hold_reason,
                indicators=self._inds(curr, o_upper.iloc[-1], o_lower.iloc[-1], i_upper.iloc[-1], i_lower.iloc[-1], recent_high, recent_low)
            )

        # Se chegou aqui com sinal (Buy/Sell)
        return StrategyResult(
            signal=signal_type,
            criteria_met=3 if signal_type in (Signal.BUY, Signal.SELL) else 0,
            criteria_total=3,
            hold_reason=hold_reason,
            indicators=self._inds(curr, o_upper.iloc[-1], o_lower.iloc[-1], i_upper.iloc[-1], i_lower.iloc[-1], recent_high, recent_low),
            metadata=metadata if 'metadata' in locals() else {}
        )

    def _inds(self, curr, ou, ol, iu, il, rh, rl) -> dict:
        return {
            "close": round(curr["close"], 2),
            "outer_u": round(ou, 2), "outer_l": round(ol, 2),
            "inner_u": round(iu, 2), "inner_l": round(il, 2),
            "resistencia": round(rh, 2),
            "suporte": round(rl, 2)
        }
