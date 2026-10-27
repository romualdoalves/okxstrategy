import types
import unittest
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import risk
from backend.database import BotModel, HistoricCandleModel, SettingsModel, TradeModel

T0 = datetime(2026, 1, 1)


def inst(symbol, bot_id, open_=True):
    return types.SimpleNamespace(
        _direction=1 if open_ else 0, _entry_inflight=False,
        config=types.SimpleNamespace(id=bot_id, symbol=symbol, stake_usd=100.0, leverage=1))


def walk(seed, n=300, vol=0.01):
    rng = np.random.default_rng(seed)
    return 100 * np.exp(np.cumsum(rng.normal(0, vol, n)))


class Base(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        for model in (BotModel, TradeModel, SettingsModel, HistoricCandleModel):
            model.__table__.create(engine)
        self.Session = sessionmaker(bind=engine)
        self._orig = risk.SessionLocal
        risk.SessionLocal = self.Session
        self.guard = risk.RiskGuard()
        self.db = self.Session()

    def tearDown(self):
        risk.SessionLocal = self._orig
        self.db.close()

    def seed(self, symbol, closes):
        for i, c in enumerate(closes):
            self.db.add(HistoricCandleModel(symbol=symbol, timeframe="1h", epoch=T0 + timedelta(hours=i),
                                            open=c, high=c, low=c, close=float(c), volume=1.0))
        self.db.commit()

    def set_limits(self, **kw):
        limits = risk.load_limits(self.db)
        for k, v in kw.items():
            setattr(limits, k, v)
        risk.save_limits(self.db, limits)


class TestCorrelation(Base):
    def setUp(self):
        super().setUp()
        base = walk(1)
        self.seed("BTC-USDT", base)
        self.seed("ETH-USDT", base * (1 + np.random.default_rng(2).normal(0, 0.001, 300)))   # anda junto
        self.seed("DOGE-USDT", walk(99))                                                      # independente

    def test_pair_correlation_separates_coupled_from_independent_assets(self):
        self.assertGreater(risk.pair_correlation(self.db, "BTC-USDT", "ETH-USDT"), 0.9)
        self.assertLess(abs(risk.pair_correlation(self.db, "BTC-USDT", "DOGE-USDT")), 0.3)

    def test_too_little_history_gives_none(self):
        self.seed("NEW-USDT", walk(5, n=40))
        self.assertIsNone(risk.pair_correlation(self.db, "BTC-USDT", "NEW-USDT"))
        self.assertIsNone(risk.pair_correlation(self.db, "BTC-USDT", "NOPE-USDT"))

    def test_second_position_in_a_correlated_asset_is_blocked_at_the_cluster_limit(self):
        self.set_limits(max_correlated_positions=1)
        others = {1: inst("BTC-USDT", 1)}
        d = self.guard.check_entry(others, 2, 100, 1, symbol="ETH-USDT")
        self.assertEqual((d.allowed, d.kind), (False, "correlation"))
        self.assertIn("BTC-USDT", d.reason)
        self.assertIn("correlação", d.reason)

    def test_an_uncorrelated_asset_is_allowed(self):
        self.set_limits(max_correlated_positions=1)
        d = self.guard.check_entry({1: inst("BTC-USDT", 1)}, 2, 100, 1, symbol="DOGE-USDT")
        self.assertTrue(d.allowed)

    def test_default_allows_up_to_three_in_the_same_cluster(self):
        self.seed("ETH2-USDT", walk(1) * 1.01)
        self.seed("ETH3-USDT", walk(1) * 0.99)
        others = {1: inst("BTC-USDT", 1), 2: inst("ETH-USDT", 2)}
        self.assertTrue(self.guard.check_entry(others, 3, 100, 1, symbol="ETH2-USDT").allowed)   # 2 + 1 = 3
        others[3] = inst("ETH2-USDT", 3)
        d = self.guard.check_entry(others, 4, 100, 1, symbol="ETH3-USDT")                         # 3 + 1 = 4
        self.assertEqual((d.allowed, d.kind), (False, "correlation"))

    def test_asset_without_history_is_assumed_correlated(self):
        self.set_limits(max_correlated_positions=1)
        d = self.guard.check_entry({1: inst("BTC-USDT", 1)}, 2, 100, 1, symbol="SEM-HISTORICO-USDT")
        self.assertEqual((d.allowed, d.kind), (False, "correlation"))
        self.assertIn("sem histórico", d.reason)

    def test_without_other_positions_correlation_never_blocks(self):
        self.set_limits(max_correlated_positions=1)
        self.assertTrue(self.guard.check_entry({}, 1, 100, 1, symbol="ETH-USDT").allowed)

    def test_snapshot_reports_the_largest_correlated_group(self):
        active = {1: inst("BTC-USDT", 1), 2: inst("ETH-USDT", 2), 3: inst("DOGE-USDT", 3)}
        snap = self.guard.snapshot(self.db, active)
        self.assertEqual(snap["usage"]["correlated_group"], 2)          # BTC e ETH juntos; DOGE à parte
        self.assertNotIn("correlation", snap["breaches"])               # 2 < limite padrão de 3
        self.set_limits(max_correlated_positions=2)
        self.assertIn("correlation", self.guard.snapshot(self.db, active)["breaches"])


class TestExposureUsesRealNotional(Base):
    def test_open_position_counts_its_real_notional_not_the_bot_stake(self):
        bot = inst("BTC-USDT", 1)
        bot._sz, bot._entry_price = 0.005, 50_000.0                      # US$250 reais, stake do bot é 100
        self.assertAlmostEqual(risk._exposure_usd([bot]), 250.0)

    def test_inflight_entry_counts_the_planned_stake(self):
        bot = inst("BTC-USDT", 1, open_=False)
        bot._entry_inflight, bot._planned_stake_usd = True, 250.0
        self.assertAlmostEqual(risk._exposure_usd([bot]), 250.0)


class TestComputeStake(unittest.TestCase):
    def limits(self, **kw):
        return risk.RiskLimits(sizing_mode="risk", capital_base_usd=2000, risk_per_trade_pct=0.5,
                               min_stake_usd=25, max_stake_usd=250, **kw)

    def test_fixed_mode_keeps_the_bot_stake(self):
        r = risk.compute_stake(risk.RiskLimits(), 100.0, 50_000, 49_000)
        self.assertEqual((r["stake"], r["mode"], r["blocked"]), (100.0, "fixed", None))

    def test_risk_mode_sizes_by_stop_distance(self):
        r = risk.compute_stake(self.limits(), 100.0, 50_000, 45_000)       # SL a 10% -> $10 / 0,10 = $100
        self.assertAlmostEqual(r["stake"], 100.0)
        self.assertAlmostEqual(r["risk_usd"], 10.0)
        self.assertIsNone(r["limited_by"])

    def test_tight_stop_is_capped_by_max_stake(self):
        r = risk.compute_stake(self.limits(), 100.0, 50_000, 49_000)       # SL a 2% -> $500 -> teto $250
        self.assertEqual((r["stake"], r["limited_by"]), (250.0, "max_stake"))

    def test_wide_stop_is_raised_to_min_stake(self):
        r = risk.compute_stake(self.limits(), 100.0, 50_000, 25_000)       # SL a 50% -> $20 -> piso $25
        self.assertEqual((r["stake"], r["limited_by"]), (25.0, "min_stake"))

    def test_stake_is_limited_by_available_balance(self):
        r = risk.compute_stake(self.limits(), 100.0, 50_000, 49_000, balance=100.0)
        self.assertEqual((r["stake"], r["limited_by"]), (95.0, "balance"))

    def test_balance_below_the_minimum_blocks_the_entry(self):
        r = risk.compute_stake(self.limits(), 100.0, 50_000, 49_000, balance=20.0)
        self.assertIn("Saldo insuficiente", r["blocked"])

    def test_invalid_stop_falls_back_to_the_fixed_stake(self):
        r = risk.compute_stake(self.limits(), 100.0, 50_000, 50_000)
        self.assertEqual((r["stake"], r["mode"]), (100.0, "fixed"))

    def test_limits_validation(self):
        for bad in (dict(sizing_mode="kelly"), dict(risk_per_trade_pct=0), dict(risk_per_trade_pct=9),
                    dict(min_stake_usd=300, max_stake_usd=250), dict(correlation_threshold=1.0),
                    dict(max_correlated_positions=0)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                risk.RiskLimits(**bad).validate()


if __name__ == "__main__":
    unittest.main()
