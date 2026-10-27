"""OKXPrivateStream: avisos `notice` antes do login, e migração 8443 -> 443 com fallback."""
import asyncio
import json
import os
import unittest

import websockets

from backend.exchanges import okx


def _fill():
    return {"data": [{"state": "filled", "instId": "BTC-USDT", "side": "sell",
                      "ordId": "1", "avgPx": "100", "accFillSz": "0.1"}]}


class _Base(unittest.TestCase):
    def setUp(self):
        self._orig = okx._get_credentials
        okx._get_credentials = lambda: ("k", "s", "p", True)
        okx.OKXPrivateStream._last_good.clear()
        os.environ.pop("OKX_WS_PRIVATE_URL", None)

    def tearDown(self):
        okx._get_credentials = self._orig
        okx.OKXPrivateStream._last_good.clear()
        os.environ.pop("OKX_WS_PRIVATE_URL", None)


class TestPrivateStreamNotice(_Base):
    def _run(self, first_messages):
        async def handler(ws):
            await ws.recv()                                    # op=login
            for m in first_messages:
                await ws.send(json.dumps(m))
            await ws.recv()                                    # op=subscribe
            await ws.send(json.dumps({"event": "subscribe"}))
            await ws.send(json.dumps({"event": "notice", "code": "64008", "msg": "reconnect soon"}))
            await ws.send(json.dumps(_fill()))

        async def main():
            async with websockets.serve(handler, "127.0.0.1", 0) as server:
                stream = okx.OKXPrivateStream(None)
                stream._ws_urls = [f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"]
                async for fill in stream.iter():
                    return fill

        return asyncio.run(asyncio.wait_for(main(), 20))

    def test_notice_before_login_is_ignored(self):
        fill = self._run([{"event": "notice", "code": "64010", "msg": "port 8443 will be retired"},
                          {"event": "login", "code": "0"}])
        self.assertEqual((fill["instId"], fill["side"], fill["state"]), ("BTC-USDT", "sell", "filled"))

    def test_login_error_still_raises(self):
        with self.assertRaises(RuntimeError):
            self._run([{"event": "error", "code": "60009", "msg": "Login failed"}])


class TestPrivateStreamUrls(_Base):
    def test_default_tries_443_then_8443(self):
        self.assertEqual(okx.OKXPrivateStream(None)._ws_urls,
                         ["wss://wspap.okx.com/ws/v5/private", "wss://wspap.okx.com:8443/ws/v5/private"])

    def test_real_account_uses_ws_host(self):
        okx._get_credentials = lambda: ("k", "s", "p", False)
        self.assertEqual(okx.OKXPrivateStream(None)._ws_urls[0], "wss://ws.okx.com/ws/v5/private")

    def test_env_override_has_no_fallback(self):
        os.environ["OKX_WS_PRIVATE_URL"] = "wss://ws.okx.com:8443/ws/v5/private"
        self.assertEqual(okx.OKXPrivateStream(None)._ws_urls, ["wss://ws.okx.com:8443/ws/v5/private"])

    def test_remembers_last_working_url(self):
        okx.OKXPrivateStream._last_good[True] = "wss://wspap.okx.com:8443/ws/v5/private"
        self.assertEqual(okx.OKXPrivateStream(None)._ws_urls[0], "wss://wspap.okx.com:8443/ws/v5/private")

    def test_falls_back_when_first_url_fails_and_remembers_second(self):
        async def good(ws):
            await ws.recv()
            await ws.send(json.dumps({"event": "login", "code": "0"}))
            await ws.recv()
            await ws.send(json.dumps(_fill()))

        async def bad(ws):                                      # recusa o login como a OKX faz
            await ws.recv()
            await ws.close(4001, "Login failed")

        async def main():
            async with websockets.serve(bad, "127.0.0.1", 0) as s_bad, websockets.serve(good, "127.0.0.1", 0) as s_good:
                url_bad = f"ws://127.0.0.1:{s_bad.sockets[0].getsockname()[1]}"
                url_good = f"ws://127.0.0.1:{s_good.sockets[0].getsockname()[1]}"
                stream = okx.OKXPrivateStream(None)
                stream._ws_urls = [url_bad, url_good]
                async for fill in stream.iter():
                    return fill, url_good

        fill, url_good = asyncio.run(asyncio.wait_for(main(), 20))
        self.assertEqual(fill["instId"], "BTC-USDT")
        self.assertEqual(okx.OKXPrivateStream._last_good[True], url_good)

    def test_all_urls_failing_raises_last_error(self):
        async def bad(ws):
            await ws.recv()
            await ws.close(4001, "Login failed")

        async def main():
            async with websockets.serve(bad, "127.0.0.1", 0) as s:
                stream = okx.OKXPrivateStream(None)
                stream._ws_urls = [f"ws://127.0.0.1:{s.sockets[0].getsockname()[1]}"] * 2
                async for _ in stream.iter():
                    pass

        with self.assertRaises(Exception):
            asyncio.run(asyncio.wait_for(main(), 20))


if __name__ == "__main__":
    unittest.main()
