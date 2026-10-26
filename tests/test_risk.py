import types
import unittest
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import risk
from backend.database import BotModel, SettingsModel, TradeModel


def inst(bot_id, open_=True, inflight=False, stake=100.0):
    return types.SimpleNamespace(
        _direction=1 if open_ else 0, _entry_inflight=inflight,
        config=types.SimpleNamespace(id=bot_id, stake_usd=stake, leverage=1))


class TestRiskGuard(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        for model in (BotModel, TradeModel, SettingsModel):
            model.__table__.create(engine)
        self.Session = sessionmaker(bind=engine)
        self._orig = risk.SessionLocal
        risk.SessionLocal = self.Session
        self.guard = risk.RiskGuard()
        self.db = self.Session()

    def tearDown(self):
        risk.SessionLocal = self._orig
        self.db.close()

    def _exit(self, pnl, fee=None, when=None):
        self.db.add(TradeModel(bot_id=1, type="exit", pnl=pnl, fee=fee,
                               timestamp=when or risk._utcnow()))
        self.db.commit()

    def test_allows_entry_when_idle(self):
        self.assertTrue(self.guard.check_entry({}, 1, 100).allowed)

    def test_blocks_on_max_open_positions(self):
        others = {i: inst(i) for i in range(2, 7)}            # 5 abertas
        d = self.guard.check_entry({**others, 1: inst(1, open_=False, inflight=True)}, 1, 100)
        self.assertFalse(d.allowed)
        self.assertEqual(d.kind, "positions")

    def test_own_inflight_entry_is_not_double_counted(self):
        others = {i: inst(i) for i in range(2, 6)}            # 4 abertas + a própria = 5
        d = self.guard.check_entry({**others, 1: inst(1, open_=False, inflight=True)}, 1, 100)
        self.assertTrue(d.allowed)

    def test_blocks_on_exposure(self):
        others = {i: inst(i, stake=150.0) for i in range(2, 5)}  # 450 + 100 > 500
        d = self.guard.check_entry(others, 1, 100)
        self.assertEqual((d.allowed, d.kind), (False, "exposure"))

    def test_daily_loss_uses_net_pnl_and_ignores_old_days(self):
        self._exit(-90.0, fee=15.0)                                         # -105 líquido hoje
        self._exit(-20.0, when=risk.current_day_start_utc() - timedelta(hours=1))   # ontem: não conta no dia
        d = self.guard.check_entry({}, 1, 100)
        self.assertEqual((d.allowed, d.kind), (False, "daily_loss"))

    def test_drawdown_halts_until_manual_release(self):
        old = risk.current_day_start_utc() - timedelta(days=2)
        self._exit(+100.0, when=old)
        self._exit(-60.0, when=old + timedelta(hours=1))
        self._exit(-160.0, when=old + timedelta(hours=2))                   # pico 100 -> -120 = $220 (11% de 2000)
        d = self.guard.check_entry({}, 1, 100)
        self.assertEqual((d.allowed, d.kind), (False, "drawdown"))
        self.assertTrue(d.first_time)
        self.assertFalse(self.guard.check_entry({}, 1, 100).first_time)      # só avisa uma vez

        self.guard.release_drawdown(self.db)
        self.assertTrue(self.guard.check_entry({}, 1, 100).allowed)         # pico zerado na liberação

    def test_disabled_never_blocks_and_fail_closed_on_error(self):
        limits = risk.load_limits(self.db)
        limits.enabled = False
        risk.save_limits(self.db, limits)
        self.assertTrue(self.guard.check_entry({i: inst(i) for i in range(2, 12)}, 1, 100).allowed)

        limits.enabled = True
        risk.save_limits(self.db, limits)
        risk.SessionLocal = lambda: (_ for _ in ()).throw(RuntimeError("db fora"))
        d = self.guard.check_entry({}, 1, 100)
        self.assertEqual((d.allowed, d.kind), (False, "error"))

    def test_auto_scan_cap_and_validation(self):
        ok, _ = self.guard.auto_scan_allowed(self.db, {}, active_auto_bots=3)
        self.assertFalse(ok)
        self.assertTrue(self.guard.auto_scan_allowed(self.db, {}, active_auto_bots=2)[0])
        with self.assertRaises(ValueError):
            risk.save_limits(self.db, risk.RiskLimits(max_drawdown_pct=0))


if __name__ == "__main__":
    unittest.main()
