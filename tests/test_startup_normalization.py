import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import BotModel
from backend.lifecycle import normalize_bots


class TestNormalizeBots(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        BotModel.__table__.create(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def add(self, **kw):
        base = dict(name="b", strategy_id="TF001", symbol="BTC-USDT", timeframe="15m", stake_usd=100.0, leverage=1)
        bot = BotModel(**{**base, **kw})
        self.db.add(bot)
        self.db.commit()
        return bot.id

    def reload(self, bot_id):
        self.db.expire_all()
        return self.db.get(BotModel, bot_id)

    def test_timeframe_chosen_by_the_user_survives_a_restart(self):
        """Antes, um mapa de 'timeframes recomendados' desfazia a escolha a cada startup."""
        a = self.add(strategy_id="TF001", timeframe="4h", symbol="BTC-USDT")     # recomendado seria 15m
        b = self.add(strategy_id="MR001", timeframe="5m", symbol="ETH-USDT")     # recomendado seria 1h
        normalize_bots(self.db)
        self.assertEqual(self.reload(a).timeframe, "4h")
        self.assertEqual(self.reload(b).timeframe, "5m")

    def test_stake_and_leverage_stay_fixed(self):
        a = self.add(stake_usd=500.0, leverage=5, symbol="SOL-USDT")
        normalize_bots(self.db)
        bot = self.reload(a)
        self.assertEqual((bot.stake_usd, bot.leverage), (100.0, 1))

    def test_legacy_ids_and_derivative_symbols_are_normalized(self):
        a = self.add(strategy_id="F001", symbol="XRP-USDT")
        b = self.add(strategy_id="TF002", symbol="DOGE-USDT-SWAP")
        c = self.add(strategy_id="F002", symbol="ADA-USDT-FUTURES")
        normalize_bots(self.db)
        self.assertEqual(self.reload(a).strategy_id, "TF014")
        self.assertEqual(self.reload(c).strategy_id, "PA007")
        self.assertEqual(self.reload(b).symbol, "DOGE-USDT")
        self.assertEqual(self.reload(c).symbol, "ADA-USDT")

    def test_is_idempotent(self):
        a = self.add(timeframe="1h", symbol="LINK-USDT")
        normalize_bots(self.db)
        normalize_bots(self.db)
        self.assertEqual(self.reload(a).timeframe, "1h")


if __name__ == "__main__":
    unittest.main()
