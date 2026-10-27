"""OKXStream: `closed=True` só para candles fechados, uma vez cada; o candle em formação sai como False."""
import asyncio

from backend.exchanges import okx
from backend.exchanges.base import CandleBar

H = 3_600_000
T0 = 1_600_000_000_000 - (1_600_000_000_000 % H)


def _bar(i):
    return CandleBar(epoch=T0 + i * H, open=1, high=2, low=0.5, close=1.5, volume=10)


def _run_stream(polls, now_by_poll, monkeypatch):
    """Executa o stream por len(polls) consultas e devolve o que ele entregou em cada uma."""
    state = {"i": 0}

    class FakeEx:
        async def fetch_candles(self, symbol, tf, limit=3):
            return polls[state["i"]]

    async def fake_sleep(_):
        state["i"] += 1
        if state["i"] >= len(polls):
            raise asyncio.CancelledError

    monkeypatch.setattr(okx, "OKXExchange", FakeEx)
    monkeypatch.setattr(okx.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(okx.time, "time", lambda: now_by_poll[state["i"]] / 1000)

    out = []

    async def go():
        try:
            async for item in okx.OKXStream(channel="1H", symbol="BTC-USDT").iter():
                out.append((state["i"], item[0].epoch, item[1]))
        except asyncio.CancelledError:
            pass

    asyncio.run(go())
    return out


def test_only_closed_candles_are_reported_closed_and_each_only_once(monkeypatch):
    # poll 0: candles 0,1 fechados e 2 em formação; poll 1: igual; poll 2: o 2 fechou e o 3 abriu
    polls = [[_bar(0), _bar(1), _bar(2)], [_bar(0), _bar(1), _bar(2)], [_bar(1), _bar(2), _bar(3)]]
    now = [T0 + 2 * H + 600_000, T0 + 2 * H + 1_200_000, T0 + 3 * H + 600_000]
    got = _run_stream(polls, now, monkeypatch)

    closed = [e for (_, e, c) in got if c]
    assert closed == [T0 + 0 * H, T0 + 1 * H, T0 + 2 * H], "cada candle fechado exatamente uma vez, em ordem"
    forming = [(i, e) for (i, e, c) in got if not c]
    assert (0, T0 + 2 * H) in forming and (2, T0 + 3 * H) in forming     # só o candle em formação é False
    assert all(e != T0 + 2 * H for (i, e, c) in got if c and i < 2), "candle 2 não pode ser 'fechado' antes de fechar"


def test_grace_period_after_nominal_close(monkeypatch):
    polls = [[_bar(0), _bar(1)]]
    # 1 s depois do fechamento nominal do candle 1: ainda dentro da folga -> não é fechado
    got = _run_stream(polls, [T0 + 2 * H + 1_000], monkeypatch)
    assert [e for (_, e, c) in got if c] == [T0]
    assert (0, T0 + H, False) in got
