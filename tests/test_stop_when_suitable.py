import asyncio
import unittest

from backend.routers.backtest import CategoryBacktestRequest, gather_until_suitable
from backend.verdict_gate import is_suitable_to_start


def result(sid, verdict="NÃO INICIAR", score=2.0, gate_applied=True, gate_passed=False):
    return {
        "strategy_id": sid,
        "recommendation": {
            "verdict": verdict, "score": score,
            "robust_gate": {"applied": gate_applied, "passed": gate_passed, "failed": []},
        },
    }


SUITABLE = dict(verdict="INICIAR", score=7.5, gate_applied=True, gate_passed=True)


def make_task(res, delay, log):
    async def run():
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            log.append(("cancelled", res["strategy_id"]))
            raise
        log.append(("done", res["strategy_id"]))
        return res
    return run()


class TestIsSuitable(unittest.TestCase):
    def test_requires_iniciar_and_an_approved_robustness_gate(self):
        self.assertTrue(is_suitable_to_start(result("A", **SUITABLE)))

    def test_high_score_with_a_failed_gate_is_not_suitable(self):
        self.assertFalse(is_suitable_to_start(result("A", verdict="CUIDADO", score=6.6, gate_applied=True, gate_passed=False)))

    def test_iniciar_without_robust_data_is_not_suitable(self):
        """Backtest clássico (sem bloco de robustez): nunca conta como adequada."""
        self.assertFalse(is_suitable_to_start(result("A", verdict="INICIAR", score=9.5, gate_applied=False, gate_passed=True)))

    def test_missing_fields_are_not_suitable(self):
        self.assertFalse(is_suitable_to_start({}))
        self.assertFalse(is_suitable_to_start({"recommendation": {"verdict": "INICIAR"}}))


class TestGatherUntilSuitable(unittest.TestCase):
    def run_it(self, specs, stop):
        log = []

        async def main():
            return await gather_until_suitable([make_task(r, d, log) for r, d in specs], stop)

        return asyncio.run(main()), log

    def test_disabled_waits_for_everything_like_before(self):
        (results, stopped_by, skipped), log = self.run_it(
            [(result("A", **SUITABLE), 0.02), (result("B"), 0.01)], False)
        self.assertEqual([r["strategy_id"] for r in results], ["A", "B"])    # ordem de submissão (gather)
        self.assertEqual((stopped_by, skipped), (None, 0))
        self.assertFalse([e for e in log if e[0] == "cancelled"])

    def test_stops_at_the_first_suitable_strategy_and_cancels_the_rest(self):
        specs = [(result("A"), 0.02), (result("B", **SUITABLE), 0.05), (result("C", **SUITABLE), 5.0), (result("D"), 5.0)]
        (results, stopped_by, skipped), log = self.run_it(specs, True)

        self.assertEqual([r["strategy_id"] for r in results], ["A", "B"])
        self.assertEqual((stopped_by, skipped), ("B", 2))
        self.assertEqual(sorted(e[1] for e in log if e[0] == "cancelled"), ["C", "D"])   # não ficam rodando

    def test_runs_everything_when_no_strategy_is_suitable(self):
        specs = [(result("A", score=8.9), 0.01), (result("B", verdict="CUIDADO", score=6.6), 0.02), (result("C"), 0.03)]
        (results, stopped_by, skipped), _ = self.run_it(specs, True)
        self.assertEqual(sorted(r["strategy_id"] for r in results), ["A", "B", "C"])
        self.assertEqual((stopped_by, skipped), (None, 0))

    def test_a_very_high_score_alone_does_not_stop_the_scan(self):
        (results, stopped_by, _), _ = self.run_it(
            [(result("A", verdict="INICIAR", score=9.9, gate_applied=False, gate_passed=True), 0.01), (result("B"), 0.02)], True)
        self.assertEqual((stopped_by, len(results)), (None, 2))


class TestRequestDefault(unittest.TestCase):
    def test_stopping_is_off_by_default(self):
        self.assertFalse(CategoryBacktestRequest(category="TF", symbol="BTC-USDT").stop_when_suitable)
        self.assertTrue(CategoryBacktestRequest(category="TF", symbol="BTC-USDT", stop_when_suitable=True).stop_when_suitable)


if __name__ == "__main__":
    unittest.main()
