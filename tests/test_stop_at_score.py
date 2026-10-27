import asyncio
import unittest

from pydantic import ValidationError

from backend.routers.backtest import CategoryBacktestRequest, gather_until_score


def make_task(sid, score, delay, log):
    async def run():
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            log.append(("cancelled", sid))
            raise
        log.append(("done", sid))
        return {"strategy_id": sid, "recommendation": {"score": score}}
    return run()


class TestGatherUntilScore(unittest.TestCase):
    def run_it(self, specs, stop_at):
        log = []

        async def main():
            tasks = [make_task(sid, score, delay, log) for sid, score, delay in specs]
            return await gather_until_score(tasks, stop_at)

        return asyncio.run(main()), log

    def test_without_threshold_waits_for_everything_like_before(self):
        (results, stopped_by, skipped), log = self.run_it([("A", 9.9, 0.02), ("B", 1.0, 0.01)], None)
        self.assertEqual([r["strategy_id"] for r in results], ["A", "B"])    # ordem de submissão (gather)
        self.assertEqual((stopped_by, skipped), (None, 0))
        self.assertFalse([e for e in log if e[0] == "cancelled"])

    def test_stops_at_the_first_result_that_reaches_the_score_and_cancels_the_rest(self):
        specs = [("A", 5.0, 0.02), ("B", 9.4, 0.05), ("C", 9.9, 5.0), ("D", 9.9, 5.0)]
        (results, stopped_by, skipped), log = self.run_it(specs, 9.0)

        self.assertEqual([r["strategy_id"] for r in results], ["A", "B"])
        self.assertEqual((stopped_by, skipped), ("B", 2))
        self.assertEqual(sorted(e[1] for e in log if e[0] == "cancelled"), ["C", "D"])   # não ficam rodando

    def test_threshold_is_inclusive(self):
        (results, stopped_by, _), _ = self.run_it([("A", 9.0, 0.01), ("B", 1.0, 5.0)], 9.0)
        self.assertEqual((stopped_by, [r["strategy_id"] for r in results]), ("A", ["A"]))

    def test_returns_everything_when_nobody_reaches_the_score(self):
        (results, stopped_by, skipped), _ = self.run_it([("A", 8.9, 0.01), ("B", 3.0, 0.02), ("C", 0.0, 0.03)], 9.0)
        self.assertEqual(sorted(r["strategy_id"] for r in results), ["A", "B", "C"])
        self.assertEqual((stopped_by, skipped), (None, 0))

    def test_results_without_a_score_never_trigger_the_stop(self):
        (results, stopped_by, _), _ = self.run_it([("A", None, 0.01), ("B", None, 0.02)], 9.0)
        self.assertEqual((stopped_by, len(results)), (None, 2))


class TestRequestValidation(unittest.TestCase):
    def test_stop_at_score_is_optional_and_bounded(self):
        self.assertIsNone(CategoryBacktestRequest(category="TF", symbol="BTC-USDT").stop_at_score)
        self.assertEqual(CategoryBacktestRequest(category="TF", symbol="BTC-USDT", stop_at_score=9.0).stop_at_score, 9.0)
        for bad in (-1, 10.5):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                CategoryBacktestRequest(category="TF", symbol="BTC-USDT", stop_at_score=bad)


if __name__ == "__main__":
    unittest.main()
