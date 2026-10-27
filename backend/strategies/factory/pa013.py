"""PA013 - Absorção Institucional
Estratégia de price action que detecta absorção institucional via resiliência do book de ofertas e delta de volume.
Gerado pela Fábrica de Estratégias
"""

from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.indicators import session_vwap
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class AbsorçãoInstitucionalStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="PA013",
            name="PA013 - Absorção Institucional",
            description="Estratégia de price action que detecta absorção institucional via resiliência do book de ofertas e delta de volume. Monitora o perfil de volume (POC, VAH, VAL) e o desequilíbrio do order book para identificar zonas de suporte/resistência onde grandes players absorvem agressão. Entra na direção oposta à agressão quando o preço não rompe o nível absorvido, com stop cirúrgico abaixo do nível.",
            tags=["pattern", "volume", "intraday", "reversal"],
            recommended_timeframe="5m",
            criteria=[
                {"id": "c1_zone", "label": "C1 Zona de Valor", "description": "Preço está próximo de VAL, VAH ou nó de alto volume"},
                {"id": "c2_aggression", "label": "C2 Agressão Anômala", "description": "Volume agressivo acima do limiar no nível de preço"},
                {"id": "c3_absorption", "label": "C3 Absorção", "description": "Preço não rompe o nível apesar da agressão (book imbalance alto)"},
                {"id": "c4_delta_divergence", "label": "C4 Divergência de Delta", "description": "Delta divergente com candle de exaustão direcional (Martelo/Estrela)"},
            ],
            params={
                "atr_period": ParamDef(type="int", default=14, min=5, max=50, step=1, description="Período do ATR para gestão de risco"),
                "sl_mult": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="Relação risco-retorno do TP1"),
                "ts_mult": ParamDef(type="float", default=3.0, min=1.0, max=5.0, step=0.5, description="Multiplicador ATR para Trailing Stop após TP1"),
                "volume_threshold": ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.1, description="Múltiplo da média de volume para considerar agressão"),
                "imbalance_threshold": ParamDef(type="float", default=0.7, min=0.5, max=0.9, step=0.05, description="Limiar de desequilíbrio do book para considerar absorção"),
                "delta_window": ParamDef(type="int", default=2, min=1, max=5, step=1, description="Janela em minutos para cálculo do delta de volume"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.atr_period = p["atr_period"].default
        self.sl_mult = p["sl_mult"].default
        self.tp1_rr = p["tp1_rr"].default
        self.ts_mult = p["ts_mult"].default
        self.volume_threshold = p["volume_threshold"].default
        self.imbalance_threshold = p["imbalance_threshold"].default
        self.delta_window = p["delta_window"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles, context=None):
        min_candles = 100
        if len(candles) < min_candles:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_candles} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "volume": 0.0, "vwap": 0.0, "poc": 0.0, "vah": 0.0, "val": 0.0, "book_imbalance": 0.0, "delta": 0.0, "aggressive_volume": 0.0, "atr": 0.0},
            )

        df = pd.DataFrame([{
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
            "open": c.open,
        } for c in candles])

        close = float(candles[-1].close)
        volume = float(candles[-1].volume)

        # ATR
        atr_s = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        if atr_s is None:
            return None
        atr = float(atr_s.iloc[-1])

        # VWAP (Corrigido)
        vwap = float(session_vwap(candles).iloc[-1])   # ta.vwap exige DatetimeIndex: devolvia None e o VWAP virava o preço

        # Perfil de volume simplificado: POC, VAH, VAL (Últimos 20 candles)
        lookback = min(20, len(df))
        recent_df = df.iloc[-lookback:]
        price_min = float(recent_df["low"].min())
        price_max = float(recent_df["high"].max())
        price_range = price_max - price_min
        
        if price_range < 0.0001:
            poc = close
            vah = close
            val = close
        else:
            bins = 10
            bin_size = price_range / bins
            volume_by_bin = {}
            for i in range(bins):
                lower = price_min + i * bin_size
                upper = lower + bin_size
                mask = (recent_df["close"] >= lower) & (recent_df["close"] < upper)
                vol = float(recent_df.loc[mask, "volume"].sum())
                volume_by_bin[(lower + upper) / 2] = vol

            if not volume_by_bin:
                poc = close
                vah = close
                val = close
            else:
                poc = max(volume_by_bin, key=volume_by_bin.get)
                sorted_prices = sorted(volume_by_bin.keys())
                total_vol = sum(volume_by_bin.values())
                cum_vol = 0.0
                vah_val_candidates = []
                for p in sorted_prices:
                    cum_vol += volume_by_bin[p]
                    vah_val_candidates.append(p)
                    if cum_vol >= total_vol * 0.7:
                        break
                if vah_val_candidates:
                    vah = max(vah_val_candidates)
                    val = min(vah_val_candidates)
                else:
                    vah = poc
                    val = poc

        # ----------------------------------------------------
        # VALIDAÇÃO DO BOOK IMBALANCE (REAL vs SIMULADO)
        # ----------------------------------------------------
        avg_volume = float(df["volume"].rolling(window=20).mean().iloc[-1]) if len(df) >= 20 else float(df["volume"].mean())
        
        if context and "bid_size" in context and "ask_size" in context:
            total_size = context["bid_size"] + context["ask_size"]
            book_imbalance_buy = context["bid_size"] / total_size if total_size > 0 else 0.5
            book_imbalance_sell = context["ask_size"] / total_size if total_size > 0 else 0.5
            current_imbalance = max(book_imbalance_buy, book_imbalance_sell)
        else:
            candle_range = float(candles[-1].high) - float(candles[-1].low)
            if candle_range < 0.0001 or avg_volume < 0.0001:
                current_imbalance = 0.5
            else:
                vol_density = volume / candle_range
                avg_density = avg_volume / (float(df["high"].rolling(window=20).mean().iloc[-1]) - float(df["low"].rolling(window=20).mean().iloc[-1]) + 0.0001)
                current_imbalance = min(1.0, vol_density / (avg_density + 0.0001) * 0.5)
            
            book_imbalance_buy = current_imbalance
            book_imbalance_sell = current_imbalance

        # ----------------------------------------------------
        # DELTA DE VOLUME E DIREÇÃO DE AGRESSÃO
        # ----------------------------------------------------
        delta_window = self.delta_window
        if len(df) >= delta_window + 1:
            delta_vals = []
            for i in range(1, delta_window + 1):
                idx = -i
                if df["close"].iloc[idx] > df["open"].iloc[idx]:
                    delta_vals.append(float(df["volume"].iloc[idx]))
                else:
                    delta_vals.append(-float(df["volume"].iloc[idx]))
            delta = sum(delta_vals) / delta_window
        else:
            delta = 0.0

        aggressive_volume = volume / (avg_volume + 0.0001)

        # Formação de Padrões de Candle (Price Action Anatômico)
        candle_body = abs(float(candles[-1].close) - float(candles[-1].open))
        candle_wick_low = min(float(candles[-1].open), float(candles[-1].close)) - float(candles[-1].low)
        candle_wick_high = float(candles[-1].high) - max(float(candles[-1].open), float(candles[-1].close))
        
        is_hammer = (candle_wick_low > candle_body * 2) and (candle_wick_high < candle_body * 0.5)
        is_shooting_star = (candle_wick_high > candle_body * 2) and (candle_wick_low < candle_body * 0.5)

        # ----------------------------------------------------
        # PROCESSAMENTO DOS CRITÉRIOS DIRECIONAIS
        # ----------------------------------------------------
        zone_proximity = 0.0
        if price_range > 0.0001:
            zone_proximity = min(
                abs(close - val) / price_range,
                abs(close - vah) / price_range,
                abs(close - poc) / price_range,
            )
        else:
            zone_proximity = 0.5

        c1_zone = zone_proximity < 0.15
        c2_aggression = aggressive_volume > self.volume_threshold
        
        c3_absorption_buy = book_imbalance_buy > self.imbalance_threshold
        c4_divergence_buy = (delta < 0) and is_hammer

        c3_absorption_sell = book_imbalance_sell > self.imbalance_threshold
        c4_divergence_sell = (delta > 0) and is_shooting_star

        # Tomada de Decisão
        signal = Signal.HOLD
        hold_reason = ""
        criteria_met = 0

        # Verificação do Gatilho de COMPRA
        if c1_zone and c2_aggression and c3_absorption_buy and c4_divergence_buy:
            if close <= val + (vah - val) * 0.15:
                signal = Signal.BUY
                criteria_met = 4
            else:
                hold_reason = "Gatilho de Compra válido, mas preço acima do limite tolerado da VAL"
        
        # Verificação do Gatilho de VENDA
        elif c1_zone and c2_aggression and c3_absorption_sell and c4_divergence_sell:
            if close >= vah - (vah - val) * 0.15:
                signal = Signal.SELL
                criteria_met = 4
            else:
                hold_reason = "Gatilho de Venda válido, mas preço abaixo do limite tolerado da VAH"
                
        # Tratamento do Estado de Espera (Hold)
        if signal == Signal.HOLD and not hold_reason:
            criteria_met = sum([c1_zone, c2_aggression, (c3_absorption_buy or c3_absorption_sell), (c4_divergence_buy or c4_divergence_sell)])
            if not c1_zone:
                hold_reason = "Preço fora das zonas de valor (POC/VAH/VAL)"
            elif not c2_aggression:
                hold_reason = "Volume de agressão abaixo do limiar institucional"
            elif not (c4_divergence_buy or c4_divergence_sell):
                hold_reason = "Ausência de exaustão/divergência estrutural no candle (Martelo/Estrela)"
            else:
                hold_reason = "Book imbalance indica falta de ordens passivas robustas de suporte"

        # ----------------------------------------------------
        # GERENCIAMENTO DE RISCO E PREÇOS ALVO
        # ----------------------------------------------------
        sl_dist = atr * self.sl_mult
        if signal == Signal.BUY:
            sl_price = round(close - sl_dist, 4)
            tp1_price = round(close + sl_dist * self.tp1_rr, 4)
        elif signal == Signal.SELL:
            sl_price = round(close + sl_dist, 4)
            tp1_price = round(close - sl_dist * self.tp1_rr, 4)
        else:
            sl_price = round(close - sl_dist, 4) if close > 0 else 0.0
            tp1_price = round(close + sl_dist * self.tp1_rr, 4) if close > 0 else 0.0

        return StrategyResult(
            signal=signal,
            hold_reason=hold_reason,
            indicators={
                "close": round(close, 4),
                "volume": round(volume, 2),
                "vwap": round(vwap, 4),
                "poc": round(poc, 4),
                "vah": round(vah, 4),
                "val": round(val, 4),
                "book_imbalance": round(current_imbalance, 4),
                "delta": round(delta, 2),
                "aggressive_volume": round(aggressive_volume, 4),
                "atr": round(atr, 6),
            },
            metadata={
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "sl_pct": round(sl_dist / close, 5) if close > 0 else 0.0,
                "tp1_pct": round(sl_dist * self.tp1_rr / close, 5) if close > 0 else 0.0,
                "ts_pct": round(atr * self.ts_mult / close, 5) if close > 0 else 0.0,
            },
            criteria_met=criteria_met,
            criteria_total=4,
        )