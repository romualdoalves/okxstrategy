"""
Caminho de ordens do BotInstance: entrada, Stop Loss, saída, circuit breaker, risco global e
recuperação após restart. A exchange é falsa (registra cada chamada) e o banco é SQLite em memória.
"""
import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import bot_manager, risk
from backend.bot import accounting, criteria, entry, instance, position, streams
from backend.bot import manager as manager_module
from backend.bot_manager import BotInstance
from backend.database import (
    BotModel, OrderRejectionModel, SettingsModel, SignalLogModel, TradeModel,
)
from backend.exchanges.base import BaseExchange, Position
from backend.strategies.base import Signal, StrategyResult

SYMBOL = "BTC-USDT"
PRICE = 50_000.0
SIZE = 0.002            # stake de US$100 a 50k


class FakeExchange(BaseExchange):
    """Simula a OKX spot. Cada comportamento pode ser forçado pelo teste."""

    def __init__(self):
        self.calls = []                        # (nome, args)
        self.position = None                   # Position | None
        self.entry_rejected = False
        self.sl_accepts = lambda price: True   # decide se o SL nesse preço é aceito
        self.reduce_only_works = True
        self.balance = 1000.0
        self.last_order_error = None
        self._n = 0

    # contrato de tamanho (OKXExchange.num_contracts / get_contract_size)
    def num_contracts(self, symbol, price, stake_usd, leverage):
        return round(stake_usd * leverage / price, 8)

    def get_contract_size(self, symbol):
        return 1.0

    async def fetch_candles(self, symbol, timeframe, limit=100): return []
    async def get_ticker(self, symbol): return {"last": PRICE}
    async def get_balance(self, currency="USDT"): return self.balance

    async def get_position(self, symbol):
        self.calls.append(("get_position", symbol))
        return self.position

    async def set_leverage(self, symbol, leverage):
        self.calls.append(("set_leverage", leverage))

    async def market_order(self, symbol, side, size, reduce_only=False):
        self.calls.append(("market_order", side, size, reduce_only))
        if reduce_only:
            if not self.reduce_only_works:
                self.last_order_error = {"kind": "order_rejected", "message": "reduce-only recusado"}
                return None
            self.position = None
            return "close-1"
        if self.entry_rejected:
            self.last_order_error = {"kind": "insufficient_balance", "message": "Saldo insuficiente"}
            return None
        self.position = Position(symbol=symbol, side="long", size=size, avg_price=PRICE, unrealized_pnl=0.0)
        return "ord-1"

    async def place_stop_loss(self, symbol, side, size, trigger_price):
        self.calls.append(("place_stop_loss", side, size, trigger_price))
        if self.sl_accepts(trigger_price):
            self._n += 1
            return f"sl-{self._n}"
        self.last_order_error = {"kind": "sl_rejected", "message": "Stop Loss recusado"}
        return None

    async def place_trailing_stop(self, *a, **k): return None
    async def cancel_algo(self, symbol, algo_id): self.calls.append(("cancel_algo", algo_id))
    async def get_order(self, order_id):
        return {"status": "filled", "filled_avg_price": PRICE, "filled_size": SIZE}

    async def get_clock(self): return {"is_open": True}
    async def get_open_orders(self, symbol=None): return []
    async def find_protective_algo(self, symbol, side=None): return None
    async def get_all_positions(self): return []

    def names(self, name):
        return [c for c in self.calls if c[0] == name]


class FakeNotifier:
    enabled = False

    def __init__(self): self.sent = []
    async def send(self, msg): self.sent.append(msg)


def make_result(signal=Signal.BUY, sl=49_000.0, tp1=52_000.0, atr=300.0):
    return StrategyResult(
        signal=signal, indicators={"atr": atr}, metadata={"sl_price": sl, "tp1_price": tp1, "ts_pct": 0.01},
        criteria_met=3, criteria_total=3,
    )


class OrderPathBase(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        for model in (BotModel, TradeModel, SignalLogModel, OrderRejectionModel, SettingsModel):
            model.__table__.create(engine)
        self.Session = sessionmaker(bind=engine)
        # SessionLocal é importado por valor em cada módulo do bot: troca em todos os que o usam.
        bot_modules = [m for m in (accounting, criteria, entry, instance, position, streams, manager_module)
                       if hasattr(m, "SessionLocal")]
        self._patches = [mock.patch.object(m, "SessionLocal", self.Session) for m in bot_modules] + [
            mock.patch.object(risk, "SessionLocal", self.Session),
            mock.patch("asyncio.sleep", new=self._fast_sleep),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(self._patches)])
        self.broadcasts = []
        self.ex = FakeExchange()

    @staticmethod
    async def _fast_sleep(*_a, **_k):
        return None

    def make_bot(self, bot_id=1, symbol=SYMBOL, stop_loss=-50.0):
        cfg = SimpleNamespace(
            id=bot_id, name=f"bot{bot_id}", strategy_id="TF002", exchange="okx", symbol=symbol,
            timeframe="15m", demo=True, stake_usd=100.0, leverage=1, stop_loss_usd=stop_loss,
            strategy_params={}, active=True, baseline_balance=0.0,
        )

        async def broadcast(msg):
            self.broadcasts.append(msg)

        bot = BotInstance(cfg, broadcast)
        bot._notifier = FakeNotifier()
        bot.status = "running"
        bot._confirm_fill_price = self._noop_async         # polling de fill em background: fora de escopo
        bot._candles = []
        return bot

    @staticmethod
    async def _noop_async(*_a, **_k):
        return None

    def run_async(self, coro):
        async def runner():
            result = await coro
            await asyncio.sleep(0)                          # deixa tasks criadas (Telegram) terminarem
            return result
        return asyncio.run(runner())

    def enter(self, bot, direction="long", result=None):
        return self.run_async(bot._enter(direction, PRICE, result or make_result(), self.ex))

    def db(self):
        return self.Session()


class TestEntry(OrderPathBase):
    def test_happy_path_places_order_then_protective_stop_and_persists(self):
        bot = self.make_bot()
        self.enter(bot)

        orders = self.ex.names("market_order")
        self.assertEqual([(o[1], o[3]) for o in orders], [("buy", False)])
        self.assertAlmostEqual(orders[0][2], SIZE)
        sl = self.ex.names("place_stop_loss")
        self.assertEqual(len(sl), 1)
        self.assertEqual(sl[0][1], "sell")                  # o SL de uma compra é uma venda
        self.assertAlmostEqual(sl[0][3], 49_000.0)

        self.assertEqual((bot._direction, bot._halted, bot._entry_inflight), (1, False, False))
        self.assertTrue(bot._sl_algo_id)
        self.assertAlmostEqual(bot._sz, SIZE)

        with self.db() as db:
            trades = db.query(TradeModel).all()
        self.assertEqual([(t.type, t.direction) for t in trades], [("entry", "LONG")])
        self.assertAlmostEqual(trades[0].sl_price, 49_000.0)
        self.assertTrue(any(m.get("event") == "entry" for m in self.broadcasts))

    def test_order_rejected_leaves_bot_flat_and_records_rejection(self):
        self.ex.entry_rejected = True
        bot = self.make_bot()
        self.enter(bot)

        self.assertEqual((bot._direction, bot._entry_inflight), (0, False))
        self.assertEqual(self.ex.names("place_stop_loss"), [])
        with self.db() as db:
            self.assertEqual(db.query(TradeModel).count(), 0)
            rej = db.query(OrderRejectionModel).all()
        self.assertEqual([(r.order_type, r.status) for r in rej], [("market", "rejected")])
        self.assertTrue(any(m.get("type") == "order_error" for m in self.broadcasts))

    def test_stop_loss_retry_widens_a_too_tight_trigger_until_accepted(self):
        """Gatilho colado ao preço (a OKX recusa): tenta 0,5%, 1% e 1,5% abaixo do preço de referência."""
        bot = self.make_bot()
        accepted = {"n": 0}

        def sl_accepts(price):
            accepted["n"] += 1
            return accepted["n"] >= 2                        # a 1ª tentativa é recusada, a 2ª passa
        self.ex.sl_accepts = sl_accepts
        tight = PRICE * 0.998                                # 0,2% abaixo do preço
        sl_id, px = self.run_async(bot._place_stop_loss_with_retry(self.ex, "sell", SIZE, tight, PRICE))

        self.assertTrue(sl_id)
        tried = [c[3] for c in self.ex.names("place_stop_loss")]
        self.assertEqual(len(tried), 2)
        self.assertAlmostEqual(tried[0], tight)
        self.assertAlmostEqual(tried[1], PRICE * 0.995)      # afastou o stop (lado venda: mais baixo)
        self.assertAlmostEqual(px, tried[1])

    def test_stop_loss_retry_gives_up_after_all_candidates_fail(self):
        bot = self.make_bot()
        self.ex.sl_accepts = lambda price: False
        sl_id, _ = self.run_async(bot._place_stop_loss_with_retry(self.ex, "sell", SIZE, PRICE * 0.998, PRICE))

        self.assertIsNone(sl_id)
        self.assertEqual(len(self.ex.names("place_stop_loss")), 4)   # gatilho original + 3 afastamentos

    def test_unprotectable_entry_is_closed_at_market(self):
        """OKX recusa todo SL: a posição não pode ficar sem proteção, então é encerrada na hora."""
        self.ex.sl_accepts = lambda price: False
        bot = self.make_bot()
        self.enter(bot)

        closes = [o for o in self.ex.names("market_order") if o[3]]
        self.assertEqual([(c[1]) for c in closes], ["sell"])  # reduce-only no lado oposto
        self.assertIsNone(self.ex.position)
        self.assertEqual((bot._direction, bot._halted), (0, False))
        with self.db() as db:
            self.assertEqual(db.query(TradeModel).count(), 0)
            kinds = [(r.order_type, r.status) for r in db.query(OrderRejectionModel).all()]
        self.assertIn(("stop_loss", "critical"), kinds)

    def test_unprotectable_entry_that_cannot_be_closed_halts_the_bot(self):
        self.ex.sl_accepts = lambda price: False
        self.ex.reduce_only_works = False
        bot = self.make_bot()
        self.enter(bot)

        self.assertEqual(bot._direction, 1)                  # a posição existe e o app sabe disso
        self.assertTrue(bot._halted)
        self.assertEqual(bot._halt_kind, "critical")         # não se libera sozinho na virada do dia
        self.assertTrue(bot._hold_reason.startswith("CRÍTICO"))
        self.assertIsNone(bot._sl_algo_id)

    def test_exchange_already_holding_a_position_blocks_a_second_entry(self):
        self.ex.position = Position(symbol=SYMBOL, side="long", size=SIZE, avg_price=PRICE, unrealized_pnl=0.0)
        bot = self.make_bot()
        self.enter(bot)

        self.assertEqual([o for o in self.ex.names("market_order")], [])
        self.assertEqual(bot._direction, 0)

    def test_spot_never_opens_a_short(self):
        bot = self.make_bot()
        self.enter(bot, direction="short", result=make_result(Signal.SELL, sl=51_000.0, tp1=48_000.0))

        self.assertEqual(self.ex.names("market_order"), [])
        self.assertEqual(bot._direction, 0)

    def test_halted_bot_does_not_enter(self):
        bot = self.make_bot()
        bot._halted = True
        bot._halt_kind = "daily_loss"
        self.enter(bot)

        self.assertEqual(self.ex.names("market_order"), [])


class TestGlobalRiskGate(OrderPathBase):
    def test_entry_is_blocked_when_the_account_is_at_the_position_limit(self):
        with self.db() as db:
            limits = risk.load_limits(db)
            limits.max_open_positions = 1
            risk.save_limits(db, limits)
        other = self.make_bot(bot_id=2, symbol="ETH-USDT")
        other._direction = 1                                  # já há uma posição aberta na conta
        bot = self.make_bot(bot_id=1)
        with mock.patch.object(bot_manager.manager, "_instances", {1: bot, 2: other}):
            self.enter(bot)

        self.assertEqual(self.ex.names("market_order"), [])
        self.assertIn("Risco global", bot._hold_reason)
        self.assertFalse(bot._entry_inflight)

    def test_entry_passes_when_the_account_has_room(self):
        bot = self.make_bot()
        with mock.patch.object(bot_manager.manager, "_instances", {1: bot}):
            self.enter(bot)
        self.assertEqual(len(self.ex.names("market_order")), 1)


class TestRiskBasedSizing(OrderPathBase):
    def use_risk_sizing(self):
        with self.db() as db:
            limits = risk.load_limits(db)
            limits.sizing_mode = "risk"                      # US$10 de risco, teto de US$250 por ordem
            risk.save_limits(db, limits)

    def test_default_mode_keeps_the_fixed_stake(self):
        bot = self.make_bot()
        self.enter(bot)
        self.assertAlmostEqual(self.ex.names("market_order")[0][2], SIZE)          # US$100 / 50k

    def test_risk_mode_sizes_the_order_by_the_stop_distance(self):
        self.use_risk_sizing()
        bot = self.make_bot()
        self.enter(bot)                                      # SL a 2%: $10 / 0,02 = $500 -> teto $250

        self.assertAlmostEqual(self.ex.names("market_order")[0][2], 0.005)           # US$250 / 50k
        self.assertAlmostEqual(bot._sz, 0.005)
        self.assertAlmostEqual(bot._planned_stake_usd, 250.0)

    def test_risk_mode_never_spends_more_than_the_balance(self):
        self.use_risk_sizing()
        self.ex.balance = 100.0
        bot = self.make_bot()
        self.enter(bot)
        self.assertAlmostEqual(self.ex.names("market_order")[0][2], 0.0019)          # 95% de US$100 / 50k

    def test_risk_mode_blocks_when_the_balance_is_below_the_minimum_stake(self):
        self.use_risk_sizing()
        self.ex.balance = 10.0
        bot = self.make_bot()
        self.enter(bot)

        self.assertEqual(self.ex.names("market_order"), [])
        self.assertIn("Saldo insuficiente", bot._hold_reason)
        self.assertFalse(bot._entry_inflight)


class TestExitAndCircuitBreaker(OrderPathBase):
    def open_position(self, bot):
        self.enter(bot)
        self.assertEqual(bot._direction, 1)
        return bot._sl_algo_id

    def sl_fill(self, bot, sl_id, px=49_000.0):
        self.ex.position = None                               # a OKX já está flat quando o fill chega
        # O canal de ordens da OKX sempre traz ordId (a ordem gerada pelo algo) além do algoId.
        event = {"state": "filled", "side": "sell", "ordId": "triggered-1", "algoId": sl_id, "instId": SYMBOL,
                 "fillPx": str(px), "fillSz": str(SIZE), "source": "13"}
        self.run_async(bot._handle_order_event(event, self.ex))

    def test_stop_loss_fill_closes_the_trade_and_books_the_loss(self):
        bot = self.make_bot()
        sl_id = self.open_position(bot)
        self.sl_fill(bot, sl_id)

        self.assertEqual(bot._direction, 0)
        self.assertAlmostEqual(bot._daily_pnl, SIZE * (49_000.0 - PRICE))     # -2,00
        self.assertEqual((bot._wins, bot._losses), (0, 1))
        with self.db() as db:
            rows = {t.type: t for t in db.query(TradeModel).all()}
        self.assertAlmostEqual(rows["entry"].exit_price, 49_000.0)            # a entrada é fechada
        self.assertAlmostEqual(rows["exit"].pnl, SIZE * (49_000.0 - PRICE))

    def test_fill_on_the_exchange_is_not_closed_locally_while_a_position_remains(self):
        """Se a OKX ainda mostra posição, o app não pode se marcar como flat (evita divergência)."""
        bot = self.make_bot()
        sl_id = self.open_position(bot)
        event = {"state": "filled", "side": "sell", "ordId": "triggered-1", "algoId": sl_id, "instId": SYMBOL,
                 "fillPx": "49000", "fillSz": str(SIZE / 2), "source": "13"}
        self.run_async(bot._handle_order_event(event, self.ex))   # self.ex.position segue aberta

        self.assertEqual(bot._direction, 1)
        with self.db() as db:
            self.assertEqual(db.query(TradeModel).filter_by(type="exit").count(), 0)

    def test_event_from_another_order_is_ignored(self):
        bot = self.make_bot()
        self.open_position(bot)
        event = {"state": "filled", "side": "sell", "ordId": "alheia", "algoId": "outro", "instId": SYMBOL,
                 "fillPx": "49000", "fillSz": str(SIZE), "source": "1"}
        self.ex.position = None
        self.run_async(bot._handle_order_event(event, self.ex))

        self.assertEqual(bot._direction, 1)

    def test_daily_loss_limit_halts_the_bot_and_blocks_the_next_entry(self):
        bot = self.make_bot(stop_loss=-1.5)                    # limite menor que a perda de US$2
        sl_id = self.open_position(bot)
        self.sl_fill(bot, sl_id)

        self.assertTrue(bot._halted)
        self.assertEqual(bot._halt_kind, "daily_loss")
        self.ex.calls.clear()
        self.enter(bot)
        self.assertEqual(self.ex.names("market_order"), [])

    def test_daily_loss_halt_releases_on_the_next_day_but_critical_does_not(self):
        bot = self.make_bot(stop_loss=-1.5)
        sl_id = self.open_position(bot)
        self.sl_fill(bot, sl_id)
        self.assertTrue(bot._halted)

        bot._pnl_day -= timedelta(days=1)                      # virou o dia
        bot._roll_day()
        self.assertFalse(bot._halted)
        self.assertEqual(bot._daily_pnl, 0.0)

        bot._halted, bot._halt_kind = True, "critical"
        bot._pnl_day -= timedelta(days=1)
        bot._roll_day()
        self.assertTrue(bot._halted)


class TestRestartRecovery(OrderPathBase):
    def test_open_position_is_restored_from_the_database(self):
        with self.db() as db:
            db.add(TradeModel(bot_id=1, symbol=SYMBOL, type="entry", direction="LONG", size=SIZE,
                              entry_price=PRICE, sl_price=49_000.0, tp1_price=52_000.0, tp1_done=True))
            db.commit()
        bot = self.make_bot()
        bot._recover_state()

        self.assertEqual(bot._direction, 1)
        self.assertAlmostEqual(bot._sz, SIZE)
        self.assertAlmostEqual(bot._entry_price, PRICE)
        self.assertAlmostEqual(bot._sl_price, 49_000.0)
        self.assertAlmostEqual(bot._tp1_price, 52_000.0)
        self.assertTrue(bot._tp1_done)
        self.assertIsNotNone(bot._current_trade_id)

    def test_closed_trade_is_not_restored_as_open(self):
        with self.db() as db:
            db.add(TradeModel(bot_id=1, symbol=SYMBOL, type="entry", direction="LONG", size=SIZE,
                              entry_price=PRICE, exit_price=51_000.0, pnl=2.0))
            db.commit()
        bot = self.make_bot()
        bot._recover_state()
        self.assertEqual(bot._direction, 0)

    def test_restart_does_not_rearm_a_cleared_circuit_breaker_but_keeps_a_live_one(self):
        old = risk.current_day_start_utc() - timedelta(days=3)
        with self.db() as db:
            db.add(TradeModel(bot_id=1, symbol=SYMBOL, type="exit", pnl=-80.0, timestamp=old))   # dia antigo
            db.commit()
        bot = self.make_bot(stop_loss=-50.0)
        bot._recover_state()
        self.assertFalse(bot._halted)                          # o prejuízo antigo não pausa o bot hoje
        self.assertEqual((bot._wins, bot._losses), (0, 1))     # mas o histórico é mantido

        with self.db() as db:
            db.add(TradeModel(bot_id=2, symbol="ETH-USDT", type="exit", pnl=-80.0, timestamp=risk._utcnow()))
            db.commit()
        today_bot = self.make_bot(bot_id=2, symbol="ETH-USDT", stop_loss=-50.0)
        today_bot._recover_state()
        self.assertTrue(today_bot._halted)                     # perda de hoje continua valendo após restart
        self.assertEqual(today_bot._halt_kind, "daily_loss")


if __name__ == "__main__":
    unittest.main()
