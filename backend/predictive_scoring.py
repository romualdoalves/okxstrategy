"""
predictive_scoring.py — Score preditivo explicável para o Scanner BT.

Esta primeira fase não usa ML treinado. Ela mede se o contexto recente do
mercado parece favorável para a família da estratégia, usando apenas candles
disponíveis até o momento da varredura.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _safe_div(num: float, den: float, default: float = 0.0) -> float:
    return float(num) / float(den) if den else default


def _ema(values: list[float], length: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (length + 1.0)
    out = [float(values[0])]
    for value in values[1:]:
        out.append(alpha * float(value) + (1.0 - alpha) * out[-1])
    return out


def _rsi(closes: list[float], length: int = 14) -> float:
    if len(closes) <= length:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    window = closes[-(length + 1):]
    for prev, cur in zip(window, window[1:]):
        diff = cur - prev
        gains.append(max(diff, 0.0))
        losses.append(abs(min(diff, 0.0)))
    avg_gain = sum(gains) / length
    avg_loss = sum(losses) / length
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr_pct(highs: list[float], lows: list[float], closes: list[float], length: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    trs: list[float] = []
    start = max(1, len(closes) - length)
    for i in range(start, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    atr = sum(trs) / len(trs) if trs else 0.0
    return _safe_div(atr, closes[-1])


def _ret(closes: list[float], periods: int) -> float:
    if len(closes) <= periods or closes[-periods - 1] == 0:
        return 0.0
    return closes[-1] / closes[-periods - 1] - 1.0


def _category(strategy_id: str) -> str:
    sid = (strategy_id or "").upper()
    for prefix in ("TF", "MR", "PA", "SC", "RG", "IF", "NW"):
        if sid.startswith(prefix):
            return prefix
    return "GEN"


@dataclass
class PredictiveScore:
    predictive_score: float
    confidence: float
    regime: str
    features: dict[str, Any]
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "predictive_score": self.predictive_score,
            "confidence": self.confidence,
            "regime": self.regime,
            "features": self.features,
            "reasons": self.reasons,
            "model": "rule_based_v1",
        }


class PredictiveScoringService:
    """Calcula oportunidade prospectiva recente para uma estratégia."""

    def score(self, strategy_id: str, symbol: str, timeframe: str, candles: list, backtest_result: dict | None = None) -> dict:
        if not candles or len(candles) < 80:
            return PredictiveScore(
                predictive_score=0.0,
                confidence=0.0,
                regime="insufficient_data",
                features={"candles": len(candles or [])},
                reasons=["Dados recentes insuficientes para score preditivo."],
            ).to_dict()

        closes = [float(getattr(c, "close", 0.0) or 0.0) for c in candles if float(getattr(c, "close", 0.0) or 0.0) > 0]
        highs = [float(getattr(c, "high", 0.0) or 0.0) for c in candles[-len(closes):]]
        lows = [float(getattr(c, "low", 0.0) or 0.0) for c in candles[-len(closes):]]
        volumes = [float(getattr(c, "volume", 0.0) or 0.0) for c in candles[-len(closes):]]
        if len(closes) < 80:
            return PredictiveScore(0.0, 0.0, "insufficient_data", {"candles": len(closes)}, ["Candles válidos insuficientes."]).to_dict()

        ema21 = _ema(closes, 21)
        ema55 = _ema(closes, 55)
        ema200 = _ema(closes, 200)
        last = closes[-1]
        atr_pct = _atr_pct(highs, lows, closes)
        ret6 = _ret(closes, 6)
        ret24 = _ret(closes, 24)
        ret72 = _ret(closes, 72)
        rsi = _rsi(closes)
        trend_strength = _safe_div(abs(ema21[-1] - ema55[-1]), last)
        ema21_slope = _safe_div(ema21[-1] - ema21[-12], last) if len(ema21) > 12 else 0.0
        dist_ema21 = _safe_div(last - ema21[-1], last)
        dist_ema200 = _safe_div(last - ema200[-1], last) if ema200 else 0.0
        vol_recent = volumes[-1] if volumes else 0.0
        vol_base = statistics.mean(volumes[-50:]) if len(volumes) >= 50 else statistics.mean(volumes)
        volume_rel = _safe_div(vol_recent, vol_base, 1.0)
        returns = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1] > 0]
        realized_vol = statistics.pstdev(returns[-50:]) if len(returns) >= 10 else 0.0

        trend_score = _clip((trend_strength / max(atr_pct, 0.002)) / 1.8)
        momentum_score = _clip((abs(ret24) / max(atr_pct * 2.5, 0.006)))
        volatility_score = _clip(1.0 - abs(atr_pct - 0.018) / 0.025)
        liquidity_score = _clip(math.log1p(max(volume_rel, 0.0)) / math.log(3.5))
        mean_reversion_stretch = _clip(abs(dist_ema21) / max(atr_pct * 1.8, 0.008))
        range_score = _clip(1.0 - trend_score)
        rsi_extreme = _clip(abs(rsi - 50.0) / 35.0)

        if trend_score > 0.62 and momentum_score > 0.45:
            regime = "trend"
        elif realized_vol > max(atr_pct, 0.01) * 1.7:
            regime = "volatile"
        elif range_score > 0.58:
            regime = "range"
        else:
            regime = "transition"

        cat = _category(strategy_id)
        if cat == "TF":
            raw = 0.35 * trend_score + 0.25 * momentum_score + 0.20 * volatility_score + 0.20 * liquidity_score
            reasons = self._trend_reasons(trend_score, momentum_score, volatility_score, liquidity_score)
        elif cat == "MR":
            raw = 0.30 * range_score + 0.30 * mean_reversion_stretch + 0.20 * rsi_extreme + 0.20 * liquidity_score
            reasons = self._mr_reasons(range_score, mean_reversion_stretch, rsi, liquidity_score)
        elif cat == "SC":
            raw = 0.35 * liquidity_score + 0.30 * volatility_score + 0.20 * momentum_score + 0.15 * range_score
            reasons = self._scalp_reasons(liquidity_score, volatility_score, momentum_score)
        elif cat == "RG":
            raw = 0.40 * max(trend_score, range_score) + 0.25 * volatility_score + 0.20 * liquidity_score + 0.15 * (1.0 if regime != "transition" else 0.35)
            reasons = self._regime_reasons(regime, trend_score, range_score, volatility_score)
        elif cat in ("PA", "IF", "NW"):
            raw = 0.25 * max(trend_score, range_score) + 0.25 * momentum_score + 0.25 * volatility_score + 0.25 * liquidity_score
            reasons = self._general_reasons(regime, momentum_score, volatility_score, liquidity_score)
        else:
            raw = 0.25 * trend_score + 0.25 * momentum_score + 0.25 * volatility_score + 0.25 * liquidity_score
            reasons = self._general_reasons(regime, momentum_score, volatility_score, liquidity_score)

        confidence = _clip(0.45 + min(len(closes), 500) / 500 * 0.35 + min(max(volume_rel, 0.0), 2.0) / 2.0 * 0.20)
        bt = backtest_result or {}
        if (bt.get("trades_count") or 0) <= 1:
            confidence = round(confidence * 0.82, 2)
            reasons.append("Confiança reduzida: backtest recente teve pouca amostra de trades.")

        features = {
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": len(closes),
            "atr_pct": round(atr_pct * 100, 3),
            "realized_vol_pct": round(realized_vol * 100, 3),
            "ret_6": round(ret6 * 100, 3),
            "ret_24": round(ret24 * 100, 3),
            "ret_72": round(ret72 * 100, 3),
            "rsi": round(rsi, 2),
            "trend_strength": round(trend_strength * 100, 3),
            "ema21_slope": round(ema21_slope * 100, 3),
            "dist_ema21": round(dist_ema21 * 100, 3),
            "dist_ema200": round(dist_ema200 * 100, 3),
            "volume_rel": round(volume_rel, 3),
            "component_scores": {
                "trend": round(trend_score * 10, 2),
                "momentum": round(momentum_score * 10, 2),
                "volatility_fit": round(volatility_score * 10, 2),
                "liquidity": round(liquidity_score * 10, 2),
                "range": round(range_score * 10, 2),
                "mean_reversion_stretch": round(mean_reversion_stretch * 10, 2),
            },
        }
        return PredictiveScore(
            predictive_score=round(_clip(raw) * 10.0, 2),
            confidence=round(confidence, 2),
            regime=regime,
            features=features,
            reasons=reasons[:5],
        ).to_dict()

    @staticmethod
    def _trend_reasons(trend: float, momentum: float, volatility: float, liquidity: float) -> list[str]:
        reasons = []
        reasons.append("Tendência recente favorável para trend following." if trend >= 0.55 else "Tendência recente ainda fraca para trend following.")
        reasons.append("Momentum confirma deslocamento de preço." if momentum >= 0.50 else "Momentum recente sem deslocamento forte.")
        reasons.append("Volatilidade compatível com execução." if volatility >= 0.45 else "Volatilidade fora da faixa ideal.")
        reasons.append("Volume relativo sustenta melhor qualidade de sinal." if liquidity >= 0.50 else "Volume relativo baixo reduz confiança.")
        return reasons

    @staticmethod
    def _mr_reasons(range_score: float, stretch: float, rsi: float, liquidity: float) -> list[str]:
        reasons = []
        reasons.append("Mercado recente mais lateral favorece reversão à média." if range_score >= 0.55 else "Mercado com tendência pode prejudicar reversão à média.")
        reasons.append("Preço está esticado contra a média curta." if stretch >= 0.45 else "Preço pouco esticado; reversão tem menor assimetria.")
        reasons.append("RSI está em zona extrema." if rsi <= 35 or rsi >= 65 else "RSI ainda em zona neutra.")
        reasons.append("Liquidez recente adequada." if liquidity >= 0.50 else "Liquidez recente baixa.")
        return reasons

    @staticmethod
    def _scalp_reasons(liquidity: float, volatility: float, momentum: float) -> list[str]:
        return [
            "Volume relativo adequado para scalping." if liquidity >= 0.55 else "Volume relativo baixo para scalping.",
            "Volatilidade recente oferece amplitude operável." if volatility >= 0.45 else "Amplitude recente pouco favorável.",
            "Impulso de curto prazo presente." if momentum >= 0.45 else "Impulso curto ainda morno.",
        ]

    @staticmethod
    def _regime_reasons(regime: str, trend: float, range_score: float, volatility: float) -> list[str]:
        return [
            f"Regime detectado: {regime}.",
            "Classificação de regime está mais nítida." if max(trend, range_score) >= 0.55 else "Regime ainda pouco nítido.",
            "Volatilidade recente está em faixa útil." if volatility >= 0.45 else "Volatilidade recente reduz qualidade do regime.",
        ]

    @staticmethod
    def _general_reasons(regime: str, momentum: float, volatility: float, liquidity: float) -> list[str]:
        return [
            f"Contexto recente classificado como {regime}.",
            "Momentum contribui para oportunidade atual." if momentum >= 0.45 else "Momentum recente não sustenta forte oportunidade.",
            "Volatilidade está próxima da faixa desejada." if volatility >= 0.45 else "Volatilidade fora da faixa ideal.",
            "Volume relativo apoia execução." if liquidity >= 0.50 else "Volume relativo baixo reduz confiança.",
        ]
