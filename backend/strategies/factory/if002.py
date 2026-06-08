"""
strategies/dex_arbitrage_sentinel.py — DEX Arbitrage Sentinel

Estrategia de arbitragem de liquidez cross-DEX/CEX.
Monitora preços de múltiplas fontes (CoinGecko, DexScreener, OKX)
e emite sinais BUY/SELL quando detecta spreads líquidos favoráveis.

Nota: Como opera na OKX (CEX), a estrategia emite sinais direcionais
e nao executa arbitragem real cross-DEX. Os spreads
são usados como indicador de momentum/desequilíbrio de mercado.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult


class DexArbitrageSentinelStrategy(BaseStrategy):
    """
    DEX Arbitrage Sentinel — detecta discrepancias de preço entre
    DEXs e a OKX, emitindo sinais direcionais quando o spread
    líquido (após fees) excede o threshold configurado.
    """

    # Flag para o BotManager saber que precisa de contexto DEX
    needs_dex_context = True

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="IF002",
            name="IF002 - DEX Spread Momentum",
            description=(
                "Usa spreads de preço entre DEXs (CoinGecko, DexScreener) e a "
                "OKX como indicador de momentum/desequilíbrio de liquidez. "
                "NAO executa arbitragem real — emite sinais direcionais BUY/SELL "
                "na OKX quando detecta que o preço local está descontado ou "
                "premium em relação ao mercado mais amplo. Funciona como um "
                "indicador antecipado baseado em discrepâncias cross-exchange."
            ),
            tags=["arbitrage", "dex", "spread", "multi_source", "advanced"],
            recommended_timeframe="5m",
            criteria=[
                {"id": "c1_spread", "label": "C1 Spread", "description": "Spread bruto entre DEX/CEX supera o limiar mínimo."},
                {"id": "c2_net_spread", "label": "C2 Net Spread", "description": "Spread líquido permanece positivo após custos estimados."},
                {"id": "c3_liquidity", "label": "C3 Liquidez", "description": "Pool/fonte possui liquidez mínima confiável."},
                {"id": "c4_data_fresh", "label": "C4 Dados", "description": "Dados DEX dentro da idade máxima permitida."},
            ],
            params={
                "min_spread_pct": ParamDef(
                    type="float", default=0.3, min=0.1, max=2.0, step=0.05,
                    description="Spread mínimo para considerar oportunidade (%).",
                ),
                "slippage_estimate_pct": ParamDef(
                    type="float", default=0.1, min=0.05, max=0.5, step=0.01,
                    description="Slippage estimado por operação (%).",
                ),
                "gas_cost_usd": ParamDef(
                    type="float", default=5.0, min=1.0, max=50.0, step=0.5,
                    description="Custo estimado de gas em USD.",
                ),
                "min_liquidity_usd": ParamDef(
                    type="float", default=100_000.0, min=10_000.0, max=1_000_000.0, step=10_000.0,
                    description="Liquidez mínima do pool para confiar no preço (USD).",
                ),
                "use_okx_only": ParamDef(
                    type="bool", default=True,
                    description="Só sinaliza se a OKX está envolvida no melhor spread.",
                ),
                "max_data_age_seconds": ParamDef(
                    type="int", default=60, min=10, max=300, step=10,
                    description="Idade máxima dos dados DEX em segundos.",
                ),
            },
        )

    def __init__(self):
        p = self.info().params
        self.min_spread_pct = p["min_spread_pct"].default
        self.slippage_estimate_pct = p["slippage_estimate_pct"].default
        self.gas_cost_usd = p["gas_cost_usd"].default
        self.min_liquidity_usd = p["min_liquidity_usd"].default
        self.use_okx_only = p["use_okx_only"].default
        self.max_data_age_seconds = p["max_data_age_seconds"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        """
        compute() simples — sem contexto DEX retorna HOLD.
        O BotManager chama compute_with_context() que tem os preços DEX.
        """
        if not candles:
            return None
        last = candles[-1]
        return StrategyResult(
            signal=Signal.HOLD,
            hold_reason="Aguardando contexto DEX...",
            indicators=self._base_indicators(last),
            criteria_met=0,
            criteria_total=4,
        )

    def compute_with_context(
        self,
        candles: list,
        context: dict | None = None,
    ) -> Optional[StrategyResult]:
        if not candles:
            return None

        last = candles[-1]
        base_inds = self._base_indicators(last)

        # Sem contexto DEX → HOLD
        if not context or "dex_prices" not in context:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Contexto DEX não disponível.",
                indicators=base_inds,
                criteria_met=0,
                criteria_total=4,
            )

        dex_data = context["dex_prices"]
        if not dex_data:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Dados DEX vazios.",
                indicators=base_inds,
                criteria_met=0,
                criteria_total=4,
            )

        # Verifica idade dos dados
        ts = dex_data.get("timestamp", 0)
        from time import time
        if time() - ts > self.max_data_age_seconds:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Dados DEX desatualizados.",
                indicators={**base_inds, "data_age_sec": int(time() - ts)},
                criteria_met=0,
                criteria_total=4,
            )

        # Calcula spreads
        from ..feeds.dex_price_feed import DexPriceFeed
        feed = DexPriceFeed(
            min_liquidity_usd=self.min_liquidity_usd,
            slippage_pct=self.slippage_estimate_pct,
            gas_cost_usd=self.gas_cost_usd,
        )

        opportunities = feed.calculate_spreads(
            dex_data,
            min_spread_pct=self.min_spread_pct,
            stake_usd=100.0,
        )

        # Preço OKX para indicadores
        okx_data = dex_data.get("okx")
        okx_price = 0.0
        if okx_data:
            okx_price = okx_data.get("last") or okx_data.get("ask") or okx_data.get("bid") or 0.0

        # Melhor preço DEX (excluindo OKX)
        best_dex_price = 0.0
        for src in dex_data.get("sources", []):
            if src.source == "okx":
                continue
            if best_dex_price == 0 or src.price < best_dex_price:
                best_dex_price = src.price

        # Indicadores comuns
        indicators = {
            **base_inds,
            "okx_price": round(okx_price, 2) if okx_price else 0,
            "best_dex_price": round(best_dex_price, 2) if best_dex_price else 0,
            "spread_pct": 0.0,
            "net_spread_pct": 0.0,
            "liquidity_ok": 0,
            "opportunities": len(opportunities),
            "data_age_sec": int(time() - ts),
        }

        if not opportunities:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Nenhum spread favorável detectado.",
                indicators=indicators,
                criteria_met=1 if indicators.get("data_age_sec", 999999) <= self.max_data_age_seconds else 0,
                criteria_total=4,
            )

        best = opportunities[0]

        # Atualiza indicadores com o melhor spread
        indicators["spread_pct"] = best.spread_pct
        indicators["net_spread_pct"] = best.net_spread_pct
        indicators["liquidity_ok"] = 1

        # Se use_okx_only=True, só sinaliza se OKX está no spread
        if self.use_okx_only:
            okx_in_spread = (
                best.buy_source.source == "okx" or
                best.sell_source.source == "okx"
            )
            if not okx_in_spread:
                return StrategyResult(
                    signal=Signal.HOLD,
                    hold_reason="Melhor spread não envolve a OKX.",
                    indicators=indicators,
                    criteria_met=3,
                    criteria_total=4,
                    metadata={
                        "best_spread_pct": best.spread_pct,
                        "best_net_spread_pct": best.net_spread_pct,
                        "buy_source": best.buy_source.source,
                        "sell_source": best.sell_source.source,
                        "opportunities_count": len(opportunities),
                    },
                )

        # Determina direção do sinal
        # Se OKX é a fonte de COMPRA (mais barata) → BUY na OKX
        # Se OKX é a fonte de VENDA (mais cara) → SELL na OKX
        # Se use_okx_only=False e OKX não está no spread:
        #   → Sinaliza na direção do desequilíbrio (BUY se DEX mais caro, SELL se DEX mais barato)
        if best.buy_source.source == "okx":
            signal = Signal.BUY
            hold_reason = (
                f"Arbitragem: OKX ({best.buy_source.price:.2f}) → "
                f"{best.sell_source.source} ({best.sell_source.price:.2f}) | "
                f"Spread líquido: +{best.net_spread_pct:.2f}%"
            )
        elif best.sell_source.source == "okx":
            signal = Signal.SELL
            hold_reason = (
                f"Arbitragem: {best.buy_source.source} ({best.buy_source.price:.2f}) → "
                f"OKX ({best.sell_source.price:.2f}) | "
                f"Spread líquido: +{best.net_spread_pct:.2f}%"
            )
        elif not self.use_okx_only:
            # Sem OKX no spread mas use_okx_only=False
            # Direção: se o preço de venda > preço de compra, o mercado está "caro"
            # na fonte de venda → sinalizamos SELL (short) na OKX
            # Se o preço de compra < preço de venda, o mercado está "barato"
            # na fonte de compra → sinalizamos BUY (long) na OKX
            # Usamos o preço OKX como referência para determinar a direção
            if okx_price > 0:
                if best.buy_source.price < okx_price:
                    # DEX mais barato que OKX → OKX está "cara" → SELL
                    signal = Signal.SELL
                else:
                    # DEX mais caro que OKX → OKX está "barata" → BUY
                    signal = Signal.BUY
            else:
                # Sem preço OKX, assume BUY como padrão
                signal = Signal.BUY
            hold_reason = (
                f"Arbitragem cross-DEX: {best.buy_source.source} ({best.buy_source.price:.2f}) → "
                f"{best.sell_source.source} ({best.sell_source.price:.2f}) | "
                f"Spread líquido: +{best.net_spread_pct:.2f}%"
            )
        else:
            # Não deveria chegar aqui se use_okx_only=True
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Spread detectado mas OKX não está no par.",
                indicators=indicators,
                criteria_met=3,
                criteria_total=4,
            )

        metadata = {
            "best_spread_pct": best.spread_pct,
            "best_net_spread_pct": best.net_spread_pct,
            "buy_source": best.buy_source.source,
            "sell_source": best.sell_source.source,
            "buy_price": round(best.buy_source.price, 2),
            "sell_price": round(best.sell_source.price, 2),
            "okx_price": round(okx_price, 2),
            "liquidity_buy": best.buy_source.liquidity_usd,
            "liquidity_sell": best.sell_source.liquidity_usd,
            "gas_estimate_usd": self.gas_cost_usd,
            "opportunities_count": len(opportunities),
            "estimated_profit_usd": best.estimated_profit_usd,
        }

        return StrategyResult(
            signal=signal,
            hold_reason=hold_reason,
            indicators=indicators,
            metadata=metadata,
            criteria_met=4,
            criteria_total=4,
        )

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _base_indicators(self, candle) -> dict[str, Any]:
        return {
            "close": round(candle.close, 2),
            "high": round(candle.high, 2),
            "low": round(candle.low, 2),
            "volume": round(candle.volume, 2) if hasattr(candle, "volume") else 0,
        }
