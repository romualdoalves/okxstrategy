"""
Testes unitários para a estratégia DEX Arbitrage Sentinel (A006).
"""

import unittest

from backend.strategies.dex_arbitrage_sentinel import DexArbitrageSentinelStrategy
from backend.strategies.base import Signal
from backend.feeds.dex_price_feed import PriceSource


class DummyCandle:
    def __init__(self, open_, high, low, close, volume=1):
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


class TestDexArbitrageSentinel(unittest.TestCase):
    def setUp(self):
        self.strat = DexArbitrageSentinelStrategy()

    def _make_candles(self, n=30, price=100.0):
        return [DummyCandle(price, price + 1, price - 1, price) for _ in range(n)]

    # ── Testes básicos ───────────────────────────────────────────────────────

    def test_no_signal_without_dex_context(self):
        """Sem contexto DEX, deve retornar HOLD."""
        candles = self._make_candles(30)
        result = self.strat.compute_with_context(candles, None)
        self.assertIsNotNone(result)
        self.assertEqual(result.signal, Signal.HOLD)
        self.assertIn("Contexto DEX", result.hold_reason)

    def test_no_signal_with_empty_context(self):
        """Contexto vazio → HOLD."""
        candles = self._make_candles(30)
        result = self.strat.compute_with_context(candles, {})
        self.assertEqual(result.signal, Signal.HOLD)

    def test_no_signal_with_empty_dex_data(self):
        """Dados DEX vazios → HOLD."""
        candles = self._make_candles(30)
        ctx = {"dex_prices": {"sources": [], "okx": None, "timestamp": 0}}
        result = self.strat.compute_with_context(candles, ctx)
        self.assertEqual(result.signal, Signal.HOLD)

    # ── Testes de sinal BUY ──────────────────────────────────────────────────

    def test_buy_signal_when_okx_cheaper(self):
        """OKX mais barata que DEX → sinal BUY."""
        self.strat.min_spread_pct = 0.1
        self.strat.gas_cost_usd = 0.5  # reduz gas para permitir spread menor
        candles = self._make_candles(30, price=100.0)
        ctx = {
            "dex_prices": {
                "sources": [
                    PriceSource(
                        source="dexscreener:uniswap_v3",
                        price=100.5,
                        liquidity_usd=500_000.0,
                        timestamp=9999999999,
                    )
                ],
                "okx": {"bid": 100.0, "ask": 100.0, "last": 100.0},
                "timestamp": 9999999999,
            }
        }
        result = self.strat.compute_with_context(candles, ctx)
        self.assertEqual(result.signal, Signal.BUY)
        self.assertIn("spread_pct", result.indicators)
        self.assertIn("buy_source", result.metadata)
        self.assertEqual(result.metadata["buy_source"], "okx")

    def test_hold_when_spread_too_small(self):
        """Spread abaixo do threshold → HOLD."""
        self.strat.min_spread_pct = 0.3  # threshold alto
        self.strat.gas_cost_usd = 0.5
        candles = self._make_candles(30, price=100.0)
        ctx = {
            "dex_prices": {
                "sources": [
                    PriceSource(
                        source="dexscreener:uniswap_v3",
                        price=100.05,
                        liquidity_usd=500_000.0,
                        timestamp=9999999999,
                    )
                ],
                "okx": {"bid": 100.0, "ask": 100.0, "last": 100.0},
                "timestamp": 9999999999,
            }
        }
        result = self.strat.compute_with_context(candles, ctx)
        self.assertEqual(result.signal, Signal.HOLD)
        self.assertIn("Nenhum spread favorável", result.hold_reason)

    # ── Testes de sinal SELL ─────────────────────────────────────────────────

    def test_sell_signal_when_okx_expensive(self):
        """OKX mais cara que DEX → sinal SELL."""
        self.strat.min_spread_pct = 0.1
        self.strat.gas_cost_usd = 0.5
        candles = self._make_candles(30, price=100.0)
        ctx = {
            "dex_prices": {
                "sources": [
                    PriceSource(
                        source="dexscreener:uniswap_v3",
                        price=100.0,
                        liquidity_usd=500_000.0,
                        timestamp=9999999999,
                    )
                ],
                "okx": {"bid": 100.5, "ask": 100.5, "last": 100.5},
                "timestamp": 9999999999,
            }
        }
        result = self.strat.compute_with_context(candles, ctx)
        self.assertEqual(result.signal, Signal.SELL)
        self.assertIn("sell_source", result.metadata)
        self.assertEqual(result.metadata["sell_source"], "okx")

    # ── Testes de filtro use_okx_only ────────────────────────────────────────

    def test_hold_when_okx_not_in_spread_and_filter_on(self):
        """use_okx_only=True e OKX fora do spread → HOLD."""
        self.strat.min_spread_pct = 0.1
        self.strat.gas_cost_usd = 0.5
        candles = self._make_candles(30, price=100.0)
        ctx = {
            "dex_prices": {
                "sources": [
                    PriceSource(
                        source="dexscreener:uniswap_v3",
                        price=100.0,
                        liquidity_usd=500_000.0,
                        timestamp=9999999999,
                    ),
                    PriceSource(
                        source="coingecko",
                        price=100.5,
                        liquidity_usd=999_999_999.0,
                        timestamp=9999999999,
                    ),
                ],
                "okx": {"bid": 100.25, "ask": 100.25, "last": 100.25},
                "timestamp": 9999999999,
            }
        }
        result = self.strat.compute_with_context(candles, ctx)
        # O melhor spread é uniswap_v3 → coingecko (0.5%), OKX não está envolvida
        self.assertEqual(result.signal, Signal.HOLD)
        self.assertIn("OKX", result.hold_reason)

    def test_signal_when_okx_not_in_spread_and_filter_off(self):
        """use_okx_only=False permite sinal mesmo sem OKX no spread."""
        self.strat.use_okx_only = False
        self.strat.min_spread_pct = 0.1
        self.strat.gas_cost_usd = 0.1  # gas muito baixo para teste
        self.strat.slippage_estimate_pct = 0.01
        candles = self._make_candles(30, price=100.0)
        ctx = {
            "dex_prices": {
                "sources": [
                    PriceSource(
                        source="dexscreener:uniswap_v3",
                        price=100.0,
                        liquidity_usd=500_000.0,
                        timestamp=9999999999,
                    ),
                    PriceSource(
                        source="coingecko",
                        price=101.0,  # 1% spread
                        liquidity_usd=999_999_999.0,
                        timestamp=9999999999,
                    ),
                ],
                "okx": {"bid": 100.25, "ask": 100.25, "last": 100.25},
                "timestamp": 9999999999,
            }
        }
        result = self.strat.compute_with_context(candles, ctx)
        # DEX buy (100.0) < OKX (100.25), então OKX está "cara" → SELL
        self.assertEqual(result.signal, Signal.SELL)

    # ── Testes de idade dos dados ────────────────────────────────────────────

    def test_hold_when_data_too_old(self):
        """Dados DEX muito antigos → HOLD."""
        candles = self._make_candles(30, price=100.0)
        ctx = {
            "dex_prices": {
                "sources": [
                    PriceSource(
                        source="dexscreener:uniswap_v3",
                        price=100.5,
                        liquidity_usd=500_000.0,
                        timestamp=0,
                    )
                ],
                "okx": {"bid": 100.0, "ask": 100.0, "last": 100.0},
                "timestamp": 0,
            }
        }
        result = self.strat.compute_with_context(candles, ctx)
        self.assertEqual(result.signal, Signal.HOLD)
        self.assertIn("desatualizados", result.hold_reason)

    # ── Testes de parâmetros ─────────────────────────────────────────────────

    def test_set_params(self):
        """set_params deve atualizar os atributos da estratégia."""
        self.strat.set_params({"min_spread_pct": 0.5, "gas_cost_usd": 10.0})
        self.assertEqual(self.strat.min_spread_pct, 0.5)
        self.assertEqual(self.strat.gas_cost_usd, 10.0)

    def test_info_returns_correct_id(self):
        """info() deve retornar ID A006."""
        info = DexArbitrageSentinelStrategy.info()
        self.assertEqual(info.id, "A006")
        self.assertIn("arbitrage", info.tags)


if __name__ == "__main__":
    unittest.main()
