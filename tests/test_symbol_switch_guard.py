"""Trocar o ativo de um bot só é permitido com o bot flat (sem posição aberta)."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.bot_manager import manager
from backend.database import BotModel, TradeModel
from backend.routers import bots as bots_router
from backend.routers.bots import BotSymbolSwitch, BotUpdate, switch_bot_symbol, update_bot


async def _tradable(*_a, **_k):
    return None


class TestSymbolChangeGuard(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        for model in (BotModel, TradeModel):
            model.__table__.create(engine)
        self.db = sessionmaker(bind=engine)()
        patch = mock.patch.object(bots_router, "_assert_symbol_tradable", _tradable)   # sem rede
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.db.close)

    def add_bot(self, bot_id=1, symbol="BTC-USDT", active=False):
        bot = BotModel(id=bot_id, name=f"bot{bot_id}", strategy_id="TF001", symbol=symbol, active=active)
        self.db.add(bot)
        self.db.commit()
        return bot

    def add_trade(self, bot_id=1, symbol="BTC-USDT", closed=False):
        self.db.add(TradeModel(bot_id=bot_id, symbol=symbol, type="entry", direction="LONG", size=0.002,
                               entry_price=50_000.0, exit_price=51_000.0 if closed else None))
        self.db.commit()

    def switch(self, bot_id, symbol):
        return asyncio.run(switch_bot_symbol(bot_id, BotSymbolSwitch(symbol=symbol), self.db))

    def patch(self, bot_id, symbol):
        return asyncio.run(update_bot(bot_id, BotUpdate(symbol=symbol), self.db))

    def test_flat_bot_can_switch_symbol(self):
        self.add_bot()
        self.add_trade(closed=True)                                   # histórico fechado não bloqueia
        result = self.switch(1, "eth-usdt")
        self.assertEqual(result["symbol"], "ETH-USDT")
        self.assertEqual(self.db.get(BotModel, 1).symbol, "ETH-USDT")

    def test_open_trade_in_the_database_blocks_the_switch(self):
        self.add_bot()
        self.add_trade(closed=False)
        with self.assertRaises(HTTPException) as ctx:
            self.switch(1, "ETH-USDT")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("posição aberta", ctx.exception.detail)
        self.assertEqual(self.db.get(BotModel, 1).symbol, "BTC-USDT")        # nada mudou

    def test_open_position_in_memory_blocks_the_switch(self):
        self.add_bot(active=False)
        live = SimpleNamespace(_direction=1)
        with mock.patch.object(manager, "_instances", {1: live}):
            with self.assertRaises(HTTPException) as ctx:
                self.switch(1, "ETH-USDT")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(self.db.get(BotModel, 1).symbol, "BTC-USDT")

    def test_flat_instance_in_memory_does_not_block(self):
        self.add_bot()
        with mock.patch.object(manager, "_instances", {1: SimpleNamespace(_direction=0)}):
            self.assertEqual(self.switch(1, "ETH-USDT")["symbol"], "ETH-USDT")

    def test_patch_with_a_new_symbol_is_also_blocked_while_a_position_is_open(self):
        self.add_bot()
        self.add_trade(closed=False)
        with self.assertRaises(HTTPException) as ctx:
            self.patch(1, "ETH-USDT")
        self.assertIn("posição aberta", ctx.exception.detail)

    def test_patch_without_changing_the_symbol_is_not_blocked(self):
        """Editar outros campos (ou reenviar o mesmo ativo) com posição aberta não é uma troca de ativo."""
        self.add_bot()
        self.add_trade(closed=False)
        self.assertEqual(self.patch(1, "BTC-USDT"), {"ok": True})

    def test_symbol_owned_by_another_bot_is_still_refused(self):
        self.add_bot(1, "BTC-USDT")
        self.add_bot(2, "ETH-USDT")
        with self.assertRaises(HTTPException) as ctx:
            self.switch(1, "ETH-USDT")
        self.assertIn("Já existe um bot", ctx.exception.detail)

    def test_the_guard_only_looks_at_the_bot_being_switched(self):
        self.add_bot(1, "BTC-USDT")
        self.add_bot(2, "SOL-USDT")
        self.add_trade(bot_id=2, symbol="SOL-USDT", closed=False)             # o outro bot tem posição aberta
        self.assertEqual(self.switch(1, "ETH-USDT")["symbol"], "ETH-USDT")


if __name__ == "__main__":
    unittest.main()
