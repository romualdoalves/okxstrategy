import asyncio
import time
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import candle_store as cs
from backend.database import HistoricCandleModel
from backend.exchanges.base import CandleBar

SYMBOL, TF, BAR = "BTC-USDT", "1h", "1H"
STEP = 3_600_000


def series(n, end_ms=None):
    """n candles de 1h terminando em end_ms (ou na última hora cheia), em ordem cronológica."""
    end = end_ms if end_ms is not None else (int(time.time() * 1000) // STEP) * STEP
    return [CandleBar(epoch=end - (n - 1 - i) * STEP, open=100 + i, high=101 + i, low=99 + i, close=100.5 + i, volume=10.0)
            for i in range(n)]


class FakeExchange:
    """Imita a OKX: a série mestre existe; cada chamada devolve uma fatia e é registrada."""

    def __init__(self, master):
        self.master = master                   # ascendente
        self.calls = []

    async def fetch_candles(self, symbol, timeframe, limit=100):
        self.calls.append(("recent", limit, None))
        return self.master[-limit:]

    async def fetch_history_candles(self, symbol, timeframe, limit=3000, before=None):
        self.calls.append(("history", limit, before))
        pool = [c for c in self.master if before is None or c.epoch < before]
        return pool[-limit:]

    def kinds(self):
        return [c[0] for c in self.calls]


class Base(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        HistoricCandleModel.__table__.create(engine)
        self.db = sessionmaker(bind=engine)()
        self.addCleanup(self.db.close)

    def ensure(self, ex, target, exhausted=False):
        return asyncio.run(cs.ensure_history(self.db, ex, SYMBOL, TF, BAR, target=target, exhausted=exhausted))

    def epochs(self):
        return [c.epoch for c in cs.load_candles(self.db, SYMBOL, TF)]


class TestEnsureHistory(Base):
    def test_empty_database_is_filled_to_the_target(self):
        ex = FakeExchange(series(2000))
        r = self.ensure(ex, 500)
        self.assertEqual((r["count"], r["exhausted"]), (500, False))
        self.assertEqual(ex.kinds(), ["history"])

    def test_second_call_only_fetches_recent_candles(self):
        ex = FakeExchange(series(2000))
        self.ensure(ex, 500)
        ex.calls.clear()
        r = self.ensure(ex, 500)
        self.assertEqual(ex.kinds(), ["recent"])                  # nada de histórico: já está no banco
        self.assertEqual(r["count"], 500)

    def test_raising_the_target_downloads_only_the_older_part(self):
        ex = FakeExchange(series(2000))
        self.ensure(ex, 500)
        before_epochs = self.epochs()
        ex.calls.clear()

        r = self.ensure(ex, 800)
        self.assertEqual(r["count"], 800)
        history = [c for c in ex.calls if c[0] == "history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0][1], 300)                       # só os 300 que faltavam
        self.assertEqual(history[0][2], before_epochs[0])          # a partir do mais antigo guardado
        ep = self.epochs()
        self.assertEqual(len(ep), len(set(ep)))                    # sem duplicatas
        self.assertTrue(all(b - a == STEP for a, b in zip(ep, ep[1:])))   # contíguo, sem buracos

    def test_a_series_shorter_than_the_target_is_marked_exhausted_and_not_retried(self):
        ex = FakeExchange(series(600))
        r = self.ensure(ex, 1000)
        self.assertEqual((r["count"], r["exhausted"]), (600, True))

        ex.calls.clear()
        r = self.ensure(ex, 1000, exhausted=True)
        self.assertNotIn("history", ex.kinds())                    # não insiste no histórico que acabou
        self.assertEqual(r["count"], 600)

    def test_small_shortfall_does_not_trigger_a_download(self):
        ex = FakeExchange(series(2000))
        self.ensure(ex, 500)
        # alvo 510: falta menos de 5% -> só atualiza o fim
        ex.calls.clear()
        self.ensure(ex, 510)
        self.assertEqual(ex.kinds(), ["recent"])

    def test_gap_after_downtime_is_filled_contiguously(self):
        """App fora do ar por mais que a janela recente: o buraco até o presente é preenchido."""
        now_end = (int(time.time() * 1000) // STEP) * STEP
        old = series(400, end_ms=now_end - 1000 * STEP)            # armazenado até 1000h atrás
        cs.upsert_candles(self.db, SYMBOL, TF, old)
        ex = FakeExchange(series(2000))
        r = self.ensure(ex, 400)
        self.assertIn("history", ex.kinds())                       # recent(300) não cobriria o buraco
        ep = self.epochs()
        self.assertTrue(all(b - a == STEP for a, b in zip(ep, ep[1:])), "buraco no meio da série")
        self.assertGreaterEqual(r["newest_ms"], now_end - STEP)


class TestStoreBasics(Base):
    def test_upsert_is_idempotent_and_updates_existing_candles(self):
        data = series(50)
        self.assertEqual(cs.upsert_candles(self.db, SYMBOL, TF, data), 50)
        cs.upsert_candles(self.db, SYMBOL, TF, data)
        self.assertEqual(cs.coverage(self.db, SYMBOL, TF)["count"], 50)

        last = data[-1]
        cs.upsert_candles(self.db, SYMBOL, TF, [CandleBar(last.epoch, 1, 2, 0.5, 1.5, 99.0)])   # candle revisado
        again = cs.load_candles(self.db, SYMBOL, TF, limit=1)[0]
        self.assertEqual((again.close, again.volume), (1.5, 99.0))
        self.assertEqual(cs.coverage(self.db, SYMBOL, TF)["count"], 50)

    def test_load_candles_is_chronological_and_limit_takes_the_most_recent(self):
        data = series(100)
        cs.upsert_candles(self.db, SYMBOL, TF, list(reversed(data)))     # gravado fora de ordem
        loaded = cs.load_candles(self.db, SYMBOL, TF, limit=10)
        self.assertEqual([c.epoch for c in loaded], [c.epoch for c in data[-10:]])
        self.assertEqual(len(cs.load_candles(self.db, SYMBOL, TF)), 100)

    def test_series_are_isolated_by_symbol_and_timeframe(self):
        cs.upsert_candles(self.db, "BTC-USDT", "1h", series(10))
        cs.upsert_candles(self.db, "ETH-USDT", "1h", series(7))
        cs.upsert_candles(self.db, "BTC-USDT", "4h", series(5))
        self.assertEqual([cs.coverage(self.db, s, t)["count"] for s, t in
                          (("BTC-USDT", "1h"), ("ETH-USDT", "1h"), ("BTC-USDT", "4h"), ("SOL-USDT", "1h"))], [10, 7, 5, 0])

    def test_large_upsert_is_chunked(self):
        self.assertEqual(cs.upsert_candles(self.db, SYMBOL, TF, series(5000)), 5000)
        self.assertEqual(cs.coverage(self.db, SYMBOL, TF)["count"], 5000)

    def test_targets_by_timeframe(self):
        self.assertEqual([cs.history_target(t) for t in ("1m", "15m", "1D", "1h", "4H", "12h", "1W")],
                         [3000, 3000, 3000, 20000, 8000, 4000, 3000])
        self.assertEqual(cs.timeframe_ms("1D"), 86_400_000)
        self.assertIsNone(cs.timeframe_ms("7x"))


if __name__ == "__main__":
    unittest.main()


class TestBackfillCli(Base):
    def test_backfill_completes_several_series_and_reruns_download_nothing_new(self):
        from backend.backfill_history import backfill, normalize_timeframe

        ex = FakeExchange(series(1500))
        pairs = [("BTC-USDT", "1h"), ("ETH-USDT", "1h")]
        logs = []
        first = asyncio.run(backfill(self.db, ex, pairs, target=400, log=logs.append))
        self.assertEqual([r["count"] for r in first], [400, 400])
        self.assertEqual(len(logs), 2)

        ex.calls.clear()
        second = asyncio.run(backfill(self.db, ex, pairs, target=400, log=lambda *_: None))
        self.assertEqual([r["count"] for r in second], [400, 400])
        self.assertEqual(ex.kinds(), ["recent", "recent"])                 # só atualiza o fim; zero histórico

        self.assertEqual([normalize_timeframe(t) for t in ("1H", "4h", "1d", "1w", "15M")], ["1h", "4h", "1D", "1W", "15m"])
        with self.assertRaises(ValueError):
            normalize_timeframe("7x")
