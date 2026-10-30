"""
Regressão: map_timeframe_for_history() devolve o código da OKX ("1H", "4H") e o cliente da OKX recebia esse
código em uma função que só conhecia "1h"/"4h" e caía em "15m" em silêncio. Resultado: 1h/2h/4h/6h/12h
traziam candles de 15m (pré-carga dos bots, timeframes extras, Scanner e cache de mercado).
"""
import time
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import candle_store as cs
from backend.database import HistoricCandleModel
from backend.exchanges.base import CandleBar
from backend.exchanges.factory import map_timeframe_for_history
from backend.exchanges.okx import _tf_to_okx

INTERNAL = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1D", "1W"]
EXPECTED_BAR = {"1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "2h": "2H",
                "4h": "4H", "6h": "6H", "12h": "12H", "1D": "1D", "1W": "1W"}


class TestTimeframeToOkx(unittest.TestCase):
    def test_internal_names_map_to_okx_codes(self):
        for tf in INTERNAL:
            with self.subTest(tf=tf):
                self.assertEqual(_tf_to_okx(tf), EXPECTED_BAR[tf])

    def test_okx_codes_are_accepted_as_is(self):
        """O caminho que quebrava: o chamador passa o resultado de map_timeframe_for_history()."""
        for tf in INTERNAL:
            with self.subTest(tf=tf):
                self.assertEqual(_tf_to_okx(map_timeframe_for_history(tf)), EXPECTED_BAR[tf])

    def test_the_exact_bug_1h_and_4h_codes_no_longer_become_15m(self):
        self.assertEqual(_tf_to_okx("1H"), "1H")
        self.assertEqual(_tf_to_okx("4H"), "4H")
        self.assertEqual(_tf_to_okx("12H"), "12H")

    def test_case_variants_are_accepted(self):
        self.assertEqual([_tf_to_okx(x) for x in ("4h", "4H", "1d", "1D", "1w", "15M")], ["4H", "4H", "1D", "1D", "1W", "15m"])

    def test_unknown_timeframes_fail_loudly_instead_of_returning_15m(self):
        for bad in ("7x", "", "3h", None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                _tf_to_okx(bad)


def candles_every(minutes, n=60):
    end = (int(time.time() * 1000) // 60_000) * 60_000
    step = minutes * 60_000
    return [CandleBar(epoch=end - (n - 1 - i) * step, open=1, high=1, low=1, close=1, volume=1) for i in range(n)]


class TestSpacingGuards(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        HistoricCandleModel.__table__.create(engine)
        self.db = sessionmaker(bind=engine)()
        self.addCleanup(self.db.close)

    def test_fifteen_minute_candles_labelled_1h_are_refused(self):
        """O caso real: candles de 15m gravados como 1h ou 4h."""
        with self.assertRaises(ValueError) as ctx:
            cs.upsert_candles(self.db, "BTC-USDT", "1h", candles_every(15))
        self.assertIn("espaçamento de 15 min", str(ctx.exception))
        with self.assertRaises(ValueError):
            cs.upsert_candles(self.db, "BTC-USDT", "4h", candles_every(15))
        self.assertEqual(cs.coverage(self.db, "BTC-USDT", "1h")["count"], 0)       # nada foi gravado

    def test_correctly_spaced_candles_are_accepted_for_every_timeframe(self):
        steps = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "12h": 720, "1D": 1440, "1W": 10080}
        for tf, minutes in steps.items():
            with self.subTest(tf=tf):
                self.assertEqual(cs.upsert_candles(self.db, "ETH-USDT", tf, candles_every(minutes, n=30)), 30)

    def test_tiny_batches_are_not_judged(self):
        """Com 1-2 candles não dá para medir espaçamento: aceita."""
        self.assertEqual(cs.upsert_candles(self.db, "SOL-USDT", "1h", candles_every(15, n=2)), 2)

    def test_purge_removes_only_the_contaminated_series(self):
        # contaminação antiga: grava direto, sem passar pela proteção
        bad = candles_every(15, n=80)
        for c in bad:
            self.db.add(HistoricCandleModel(symbol="BTC-USDT", timeframe="4h", epoch=cs._to_dt(c.epoch),
                                            open=1, high=1, low=1, close=1, volume=1))
        self.db.commit()
        cs.upsert_candles(self.db, "BTC-USDT", "15m", candles_every(15, n=80))       # série correta
        cs.upsert_candles(self.db, "BTC-USDT", "1D", candles_every(1440, n=40))      # série correta

        purged = cs.purge_misaligned_series(self.db)
        self.assertEqual([(s, t) for s, t, _ in purged], [("BTC-USDT", "4h")])
        self.assertEqual(purged[0][2], 15)                                           # observou 15 min
        self.assertEqual(cs.coverage(self.db, "BTC-USDT", "4h")["count"], 0)
        self.assertEqual(cs.coverage(self.db, "BTC-USDT", "15m")["count"], 80)
        self.assertEqual(cs.coverage(self.db, "BTC-USDT", "1D")["count"], 40)

    def test_purge_is_a_noop_on_a_healthy_database(self):
        cs.upsert_candles(self.db, "BTC-USDT", "1h", candles_every(60, n=120))
        self.assertEqual(cs.purge_misaligned_series(self.db), [])
        self.assertEqual(cs.coverage(self.db, "BTC-USDT", "1h")["count"], 120)


if __name__ == "__main__":
    unittest.main()


def test_unknown_timeframe_fails_instead_of_becoming_15m():
    import pytest
    from backend.exchanges.factory import map_timeframe_for_history
    for tf in ("2m", "7h", "", "abc"):
        with pytest.raises(ValueError):
            map_timeframe_for_history(tf)
    assert map_timeframe_for_history("1h") == "1H" and map_timeframe_for_history("1D") == "1D"
