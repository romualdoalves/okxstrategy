"""Modo "Meta 1% Hoje!" com o BotInstance REAL, exchange falsa e banco SQLite: a ligação entre o fechamento da posição
(`_on_position_closed`) e o encerramento do bot — vendas, stop loss, trailing, 24 h sem ordem e posição aberta após 24 h."""
import datetime as dt
from unittest import mock

from backend.bot import goal as goal_module
from backend.database import BotModel, TradeModel
from tests.test_order_path import PRICE, SIZE, SYMBOL, OrderPathBase


class GoalIntegrationBase(OrderPathBase):
    def setUp(self):
        super().setUp()
        patch = mock.patch.object(goal_module, "SessionLocal", self.Session)     # o mixin da meta também usa o banco de teste
        patch.start()
        self.addCleanup(patch.stop)

    def goal_bot(self, started_hours_ago=1.0, window=24.0):
        bot = self.make_bot()
        started = dt.datetime.utcnow() - dt.timedelta(hours=started_hours_ago)
        bot.config.goal_pct, bot.config.goal_window_hours = 1.0, window
        bot.config.goal_started_at, bot.config.goal_outcome = started, None
        with self.db() as db:
            db.add(BotModel(id=1, name="bot1", strategy_id="TF001", symbol=SYMBOL, active=True,
                            goal_pct=1.0, goal_window_hours=window, goal_started_at=started))
            db.commit()
        return bot

    def bot_row(self):
        with self.db() as db:
            return db.get(BotModel, 1)

    def fill_exit_at(self, price):
        async def get_order(order_id):
            return {"status": "filled", "filled_avg_price": price, "filled_size": SIZE}
        self.ex.get_order = get_order

    def algo_fill(self, bot, sl_id, px):
        self.ex.position = None
        event = {"state": "filled", "side": "sell", "ordId": "triggered-1", "algoId": sl_id, "instId": SYMBOL,
                 "fillPx": str(px), "fillSz": str(SIZE), "source": "13"}
        self.run_async(bot._handle_order_event(event, self.ex))


class TestGoalMode(GoalIntegrationBase):
    def test_goal_reached_sells_at_market_then_stops_the_bot_and_records_it(self):
        bot = self.goal_bot()
        self.enter(bot)
        self.assertEqual(bot._direction, 1)
        self.fill_exit_at(51_500.0)                                  # +3% bruto, ≈ +2,8% líquido: cobre a meta de 1%
        self.run_async(bot._goal_tick(51_500.0, self.ex))

        self.assertEqual(bot._direction, 0)                          # vendeu e fechou
        self.assertEqual(len(self.ex.names("market_order")), 2)      # entrada + UMA venda
        self.assertTrue(self.ex.names("market_order")[-1][3])        # a venda é reduce-only (só a posição do bot)
        self.assertTrue(bot._goal_done and bot._goal_outcome == "reached")
        self.assertEqual(bot.status, "stopped")
        row = self.bot_row()
        self.assertFalse(row.active)
        self.assertEqual(row.goal_outcome, "reached")
        self.assertIsNotNone(row.goal_ended_at)
        self.assertTrue(any(m.get("type") == "goal" and m["outcome"] == "reached" for m in self.broadcasts))
        self.assertTrue(any("META BRUTA ATINGIDA" in m.upper() for m in bot._notifier.sent))
        self.assertGreater(row.goal_result_gross_pct, 2.5)                        # +3% bruto
        self.assertGreater(row.goal_result_net_pct, 2.0)                          # e ainda sobra bem mais que zero depois das taxas
        self.assertLess(row.goal_result_net_pct, row.goal_result_gross_pct)       # líquido sempre abaixo do bruto

    def test_stop_loss_ends_the_session_and_stops_the_bot(self):
        bot = self.goal_bot()
        self.enter(bot)
        self.algo_fill(bot, bot._sl_algo_id, 49_000.0)               # stop loss de -2%
        self.assertEqual(bot._direction, 0)
        self.assertTrue(bot._goal_done and bot._goal_outcome == "stop_loss")
        self.assertEqual(bot.status, "stopped")
        row = self.bot_row()
        self.assertFalse(row.active)
        self.assertEqual(row.goal_outcome, "stop_loss")

    def test_a_small_trailing_gain_does_not_end_the_session(self):
        bot = self.goal_bot()
        self.enter(bot)
        self.algo_fill(bot, bot._sl_algo_id, 50_400.0)               # +0,8% bruto ≈ +0,6% líquido: abaixo da meta, ainda há tempo
        self.assertEqual(bot._direction, 0)
        self.assertFalse(bot._goal_done)
        self.assertEqual(bot.status, "running")
        self.assertTrue(self.bot_row().active)
        self.assertIsNone(self.bot_row().goal_outcome)
        self.assertGreater(bot._goal_realized_gross_pct, 0.6)                    # +0,8% bruto: abaixo da meta bruta de 1%
        self.assertLess(bot._goal_realized_gross_pct, 1.0)
        self.assertLess(bot._goal_realized_net_pct, bot._goal_realized_gross_pct)  # o líquido já descontou as taxas

    def test_two_partial_gains_add_up_to_the_goal(self):
        bot = self.goal_bot()
        self.enter(bot)
        self.algo_fill(bot, bot._sl_algo_id, 50_400.0)               # ≈ +0,6% líquido
        self.assertFalse(bot._goal_done)
        self.enter(bot)
        self.fill_exit_at(50_700.0)
        self.run_async(bot._goal_tick(50_700.0, self.ex))            # a 2ª posição só precisa cobrir o que falta (~0,4%)
        self.assertTrue(bot._goal_done and bot._goal_outcome == "reached")

    def test_no_order_in_24h_stops_the_bot_without_touching_the_exchange(self):
        bot = self.goal_bot(started_hours_ago=25)
        self.run_async(bot._goal_tick(PRICE, self.ex))
        self.assertTrue(bot._goal_done and bot._goal_outcome == "no_order")
        self.assertEqual(self.ex.names("market_order"), [])
        self.assertEqual(self.bot_row().goal_outcome, "no_order")
        self.assertFalse(self.bot_row().active)

    def test_an_open_position_is_left_alone_after_24h(self):
        bot = self.goal_bot(started_hours_ago=30)
        self.enter(bot)
        before = len(self.ex.calls)
        self.run_async(bot._goal_tick(PRICE + 20, self.ex))          # +0,04%: não cobre a meta
        self.assertEqual(bot._direction, 1)
        self.assertFalse(bot._goal_done)
        self.assertEqual(bot.status, "running")
        self.assertEqual([c for c in self.ex.calls[before:] if c[0] == "market_order"], [])

    def test_gross_goal_hit_through_many_tiny_trades_is_flagged_as_an_illusion(self):
        """O bruto chega a +1% por muitas operações pequenas, mas as taxas consomem o ganho: o bot para (a meta é bruta),
        registra bruto E líquido e marca a ilusão."""
        bot = self.goal_bot()
        for _ in range(6):                                           # 6 x +0,18% bruto; cada ida e volta custa ~0,20% de taxas
            self.enter(bot)
            self.algo_fill(bot, bot._sl_algo_id, 50_090.0)
        self.assertTrue(bot._goal_done and bot._goal_outcome == "reached")
        self.assertGreaterEqual(bot._goal_realized_gross_pct, 1.0)
        self.assertLess(bot._goal_realized_net_pct, 0.0)             # as taxas comeram o ganho
        row = self.bot_row()
        self.assertEqual(row.goal_outcome, "reached")
        self.assertLess(row.goal_result_net_pct, 0.0)
        self.assertTrue(bot._goal_view(50_090.0)["illusory"])
        self.assertTrue(any("taxas consumiram o ganho" in m for m in bot._notifier.sent))

    def test_a_bot_without_goal_ignores_everything(self):
        bot = self.make_bot()
        self.enter(bot)
        self.run_async(bot._goal_tick(PRICE * 1.2, self.ex))
        self.assertEqual(bot._direction, 1)
        self.assertEqual(len(self.ex.names("market_order")), 1)      # só a entrada


class TestGoalEntryGate(GoalIntegrationBase):
    def test_after_the_goal_ended_no_new_entry_is_made(self):
        bot = self.goal_bot()
        bot._goal_done = True                     # meta encerrada, mas o processo do bot ainda não terminou de parar
        self.enter(bot)
        self.assertEqual(bot._direction, 0)
        self.assertEqual(self.ex.names("market_order"), [])

    def test_the_same_bot_enters_normally_while_the_goal_is_active(self):
        bot = self.goal_bot()
        self.enter(bot)
        self.assertEqual(bot._direction, 1)
        self.assertEqual(len(self.ex.names("market_order")), 1)
