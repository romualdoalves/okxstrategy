import unittest
from datetime import datetime

import pandas as pd

from backend.strategies.price_action_liquidity_sweep import LiquiditySweepStrategy
from backend.strategies.base import Signal


class DummyCandle:
    def __init__(self, open_, high, low, close):
        self.open = open_
        self.high = high
        self.low = low
        self.close = close

class TestLiquiditySweepStrategy(unittest.TestCase):
    def setUp(self):
        self.strat = LiquiditySweepStrategy()

    def _run(self, candles):
        return self.strat.compute(candles)

    def test_no_signal_when_not_enough_candles(self):
        candles = [DummyCandle(1, 1, 1, 1) for _ in range(5)]
        result = self._run(candles)
        self.assertIsNotNone(result)
        self.assertEqual(result.signal, Signal.HOLD)

    def test_detect_simple_sweep_and_choch(self):
        # Build a synthetic price series with a clear support, a sweep, and a breakout.
        # 0-19: flat price 100
        # 20-22: slight dip to create support at 95
        # 23: sweep candle breaking support to 90 with long lower wick
        # 24: confirmation candle closing above channel high (101)
        prices = [100] * 20 + [95, 96, 95] + [90] + [101]
        candles = []
        for i, p in enumerate(prices):
            # use small random high/low around close for simplicity
            high = p + 0.5
            low = p - 0.5 if i != 23 else p - 5  # big wick on sweep candle
            open_ = p - 0.2
            close = p
            candles.append(DummyCandle(open_, high, low, close))
        result = self._run(candles)
        self.assertEqual(result.signal, Signal.BUY)
        self.assertIn('entry', result.metadata)
        self.assertIn('stop', result.metadata)
        self.assertIn('tp1', result.metadata)

if __name__ == '__main__':
    unittest.main()
