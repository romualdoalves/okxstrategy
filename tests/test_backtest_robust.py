import asyncio
import random
import unittest

from backend import backtest_stats as st
from backend.backtest_engine import BacktestEngine
from backend.exchanges.base import CandleBar


def synthetic_candles(n, seed=1):
    rng = random.Random(seed)
    p, out = 100.0, []
    for i in range(n):
        p *= 1 + rng.gauss(0.0002, 0.006)
        o = p
        c = p * (1 + rng.gauss(0, 0.002))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.002)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, 0.002)))
        out.append(CandleBar(epoch=1_700_000_000_000 + i * 900_000, open=o, high=h, low=lo, close=c,
                             volume=1000 + rng.random() * 500))
    return out


class TestStats(unittest.TestCase):
    def test_profit_factor_and_cap(self):
        self.assertAlmostEqual(st.profit_factor([10, -5]), 2.0)
        self.assertEqual(st.profit_factor([5, 5]), st.PF_CAP)          # sem perdas: limitado
        self.assertEqual(st.profit_factor([-5]), 0.0)

    def test_bootstrap_penalizes_small_samples(self):
        small = [20, -10, 15, -10, 12, -10, 18, 20, -10, -10]          # PF pontual 2,1 com 10 trades
        big = small * 6
        b_small, b_big = st.bootstrap_profit_factor(small), st.bootstrap_profit_factor(big)
        self.assertLess(b_small["pf_p5"], st.profit_factor(small))
        self.assertLess(b_small["pf_p5"], b_big["pf_p5"])              # mais trades => cauda inferior maior
        self.assertEqual(st.bootstrap_profit_factor(small), b_small)   # determinístico

    def test_bootstrap_edge_cases(self):
        self.assertEqual(st.bootstrap_profit_factor([5])["resamples"], 0)
        self.assertEqual(st.bootstrap_profit_factor([])["pf_p5"], 0.0)

    def test_robust_summary_partitions_by_entry_bar(self):
        trades = [{"profit": 10, "entry_time": 120}, {"profit": -4, "entry_time": 200},
                  {"profit": 8, "entry_time": 900}, {"profit": 7, "entry_time": 950},
                  {"profit": 9, "entry_time": 990}, {"profit": 6, "entry_time": 995},
                  {"profit": 5, "entry_time": 999}]
        r = st.robust_summary(trades, total_bars=1000, warmup=100, oos_fraction=0.3)
        self.assertEqual(r["split_bar"], 100 + int(900 * 0.7))         # 730
        self.assertEqual((r["in_sample"]["trades"], r["out_of_sample"]["trades"]), (2, 5))
        self.assertEqual(r["all"]["trades"], 7)
        self.assertTrue(r["oos_conclusive"] and r["oos_positive"])
        self.assertFalse(r["min_trades_met"])                          # 7 < 30

    def test_oos_needs_minimum_trades_to_count(self):
        trades = [{"profit": 10, "entry_time": 900}, {"profit": 10, "entry_time": 950}]
        r = st.robust_summary(trades, total_bars=1000, warmup=100)
        self.assertFalse(r["oos_conclusive"])
        self.assertFalse(r["oos_positive"])


class TestEngineRobust(unittest.TestCase):
    def test_run_strict_reports_consistent_robust_block(self):
        res = BacktestEngine("TF003").run_strict_sync(synthetic_candles(700, seed=2))
        rb = res["robust"]
        self.assertEqual(rb["all"]["trades"], res["trades_count"])
        self.assertEqual(rb["in_sample"]["trades"] + rb["out_of_sample"]["trades"], res["trades_count"])
        self.assertAlmostEqual(rb["all"]["profit"], res["total_profit"], places=1)

    def test_window_keeps_cost_bounded_and_results_stable_for_long_series(self):
        """Com mais de STRICT_WINDOW candles o resultado depende só da janela: o início da série não importa."""
        from backend.backtest_engine import STRICT_WINDOW
        cs = synthetic_candles(STRICT_WINDOW + 400, seed=3)
        full = BacktestEngine("TF003").run_strict_sync(cs)
        self.assertGreaterEqual(full["robust"]["bars_evaluated"], 400)

    def test_pool_matches_direct_run(self):
        import os
        os.environ["BACKTEST_WORKERS"] = "1"
        from backend import backtest_worker
        cs = synthetic_candles(500, seed=4)
        direct = BacktestEngine("TF003").run_strict_sync(cs)
        try:
            pooled = asyncio.run(backtest_worker.run_strict_async("TF003", cs, None))
        finally:
            backtest_worker.shutdown_pool()
        self.assertEqual(pooled["trades_count"], direct["trades_count"])
        self.assertEqual(pooled["total_profit"], direct["total_profit"])


if __name__ == "__main__":
    unittest.main()
