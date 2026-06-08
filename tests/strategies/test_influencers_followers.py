import unittest

from backend.strategies.base import Signal
from backend.strategies.influencers_followers import InfluencersFollowersStrategy


class DummyCandle:
    def __init__(self, open_, high, low, close, volume=1, epoch=0):
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.epoch = epoch


class TestInfluencersFollowersStrategy(unittest.TestCase):
    def setUp(self):
        self.strat = InfluencersFollowersStrategy()
        self.strat.engine.influencer = "BTC-USDT"
        self.strat.engine.follower = "ETH-USDT"
        self.strat._last_price_matrix = {
            "BTC-USDT": [100.0] * 20 + [112.0],
            "ETH-USDT": [
                100.0, 99.9, 100.1, 100.0, 99.8, 100.0, 100.2,
                100.0, 99.9, 100.1, 100.0, 99.8, 100.0, 100.2,
                100.0, 99.9, 100.1, 100.0, 99.8, 100.0, 100.1,
            ],
        }

    def _candles(self):
        return [DummyCandle(100, 101, 99, 100, epoch=i) for i in range(30)]

    def test_buy_signal_when_graph_follower_matches_bot_symbol(self):
        result = self.strat.compute_with_context(
            self._candles(),
            {"symbol": "ETH-USDT"},
        )

        self.assertEqual(result.signal, Signal.BUY)
        self.assertEqual(result.criteria_met, 5)
        self.assertEqual(result.criteria_total, 5)
        self.assertEqual(result.indicators["target_is_follower"], 1)
        self.assertIn("sl_price", result.metadata)
        self.assertIn("tp1_price", result.metadata)

    def test_hold_when_graph_follower_does_not_match_bot_symbol(self):
        result = self.strat.compute_with_context(
            self._candles(),
            {"symbol": "DOGE-USDT"},
        )

        self.assertEqual(result.signal, Signal.HOLD)
        self.assertEqual(result.criteria_total, 5)
        self.assertEqual(result.indicators["target_is_follower"], 0)
        self.assertIn("Grafo quer operar ETH-USDT", result.hold_reason)


if __name__ == "__main__":
    unittest.main()
