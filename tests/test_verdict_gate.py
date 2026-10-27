import unittest

from backend.routers.backtest import _compose_predictive_recommendation
from backend.verdict_gate import SCORE_CAP_WITHOUT_ROBUSTNESS, min_trades_for, robustness_gate


def robust(total=40, oos_trades=8, oos_profit=12.0, oos_pf=1.6, conclusive=True, positive=True):
    return {
        "all": {"trades": total}, "out_of_sample": {"trades": oos_trades, "profit": oos_profit, "profit_factor": oos_pf},
        "oos_conclusive": conclusive, "oos_positive": positive, "min_trades_met": total >= 30,
    }


def scan_result(score, robust_block, trades=40):
    """Resultado de um backtest estrito com score histórico alto (o que o Scanner chamaria de INICIAR)."""
    return {
        "trades_count": trades, "total_profit": 50.0, "ignored_sell_signals": 0,
        "coverage": {"level": "full"}, "robust": robust_block,
        "recommendation": {"verdict": "INICIAR", "level": "success", "score": score, "reasons": ["histórico forte"]},
    }


PREDICTIVE = {"predictive_score": 9.0, "confidence": 0.9, "regime": "trend", "reasons": ["momentum"]}


class TestRobustnessGate(unittest.TestCase):
    def test_passes_with_enough_trades_and_positive_out_of_sample(self):
        g = robustness_gate(robust())
        self.assertEqual((g["applied"], g["passed"], g["failed"]), (True, True, []))

    def test_fails_with_few_trades(self):
        g = robustness_gate(robust(total=10))
        self.assertFalse(g["passed"])
        self.assertIn("10 trades", g["failed"][0])

    def test_fails_when_out_of_sample_is_inconclusive_or_negative(self):
        inconclusive = robustness_gate(robust(oos_trades=2, conclusive=False, positive=False))
        negative = robustness_gate(robust(oos_profit=-8.0, oos_pf=0.7, positive=False))
        self.assertFalse(inconclusive["passed"])
        self.assertIn("inconclusivo", inconclusive["failed"][0])
        self.assertFalse(negative["passed"])
        self.assertIn("negativo", negative["failed"][0])

    def test_not_applied_without_robust_block(self):
        g = robustness_gate(None)
        self.assertEqual((g["applied"], g["passed"], g["failed"]), (False, True, []))
        self.assertEqual(robustness_gate({})["applied"], False)


class TestMinTradesByTimeframe(unittest.TestCase):
    """Estratégias lentas fazem poucos trades: o mínimo cai com o timeframe, mas exige mais evidência."""

    def test_minimums_per_timeframe(self):
        self.assertEqual([min_trades_for(tf) for tf in ("1m", "15m", "1h", "4H", "1D", "1w")], [30, 30, 30, 20, 15, 15])
        self.assertEqual(min_trades_for(None), 30)
        self.assertEqual(min_trades_for("estranho"), 30)

    def test_daily_strategy_with_16_trades_passes_when_the_bootstrap_is_strong(self):
        r = robust(total=16)
        r["bootstrap"] = {"prob_pf_gt_1": 0.97}
        g = robustness_gate(r, "1D")
        self.assertTrue(g["passed"], g["failed"])
        self.assertEqual((g["min_trades"], g["timeframe"]), (15, "1D"))

    def test_the_same_16_trades_fail_on_a_short_timeframe(self):
        r = robust(total=16)
        r["bootstrap"] = {"prob_pf_gt_1": 0.99}
        g = robustness_gate(r, "15m")
        self.assertFalse(g["passed"])
        self.assertIn("mínimo de 30 para este timeframe", g["failed"][0])

    def test_lower_minimum_requires_stronger_bootstrap_evidence(self):
        r = robust(total=16)
        r["bootstrap"] = {"prob_pf_gt_1": 0.80}
        g = robustness_gate(r, "1D")
        self.assertFalse(g["passed"])
        self.assertIn("80%", g["failed"][0])

    def test_four_hour_needs_20_trades(self):
        r = robust(total=19)
        r["bootstrap"] = {"prob_pf_gt_1": 0.99}
        self.assertFalse(robustness_gate(r, "4h")["passed"])
        r = robust(total=20)
        r["bootstrap"] = {"prob_pf_gt_1": 0.99}
        self.assertTrue(robustness_gate(r, "4h")["passed"])

    def test_default_30_does_not_need_the_bootstrap_condition(self):
        """Sem timeframe conhecido ou em timeframes curtos nada muda em relação à regra anterior."""
        self.assertTrue(robustness_gate(robust(total=40))["passed"])
        self.assertTrue(robustness_gate(robust(total=40), "15m")["passed"])

    def test_out_of_sample_still_applies_on_slow_timeframes(self):
        r = robust(total=30, oos_profit=-5.0, oos_pf=0.8, positive=False)
        r["bootstrap"] = {"prob_pf_gt_1": 0.99}
        self.assertFalse(robustness_gate(r, "1D")["passed"])


class TestVerdictBrake(unittest.TestCase):
    def test_high_score_with_a_lucky_small_sample_is_capped_to_cuidado(self):
        rec = _compose_predictive_recommendation(scan_result(9.4, robust(total=11, oos_trades=2, conclusive=False, positive=False), trades=11), PREDICTIVE)
        self.assertEqual(rec["verdict"], "CUIDADO")
        self.assertLessEqual(rec["score"], SCORE_CAP_WITHOUT_ROBUSTNESS)
        self.assertTrue(rec["reasons"][0].startswith("Veredito limitado a CUIDADO"))
        self.assertFalse(rec["robust_gate"]["passed"])

    def test_robust_result_keeps_iniciar(self):
        rec = _compose_predictive_recommendation(scan_result(9.4, robust()), PREDICTIVE)
        self.assertEqual(rec["verdict"], "INICIAR")
        self.assertGreater(rec["score"], SCORE_CAP_WITHOUT_ROBUSTNESS)
        self.assertTrue(rec["robust_gate"]["passed"])

    def test_cuidado_and_nao_iniciar_are_not_touched(self):
        rec = _compose_predictive_recommendation(scan_result(2.0, robust(total=5, conclusive=False, positive=False), trades=5), {"predictive_score": 1.0, "confidence": 0.2})
        self.assertIn(rec["verdict"], ("NÃO INICIAR", "CUIDADO"))
        self.assertTrue(rec["robust_gate"]["applied"])

    def test_results_without_robust_block_are_unchanged(self):
        rec = _compose_predictive_recommendation(scan_result(9.4, None), PREDICTIVE)
        self.assertEqual(rec["verdict"], "INICIAR")
        self.assertFalse(rec["robust_gate"]["applied"])


if __name__ == "__main__":
    unittest.main()
