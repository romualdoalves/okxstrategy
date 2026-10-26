"""OKXPrivateStream: avisos `notice` antes do login não podem derrubar a conexão."""
import asyncio
import json
import unittest

import websockets

from backend.exchanges import okx


class TestPrivateStreamNotice(unittest.TestCase):
    def _run(self, first_messages):
        sent = []

        async def server_handler(ws):
            await ws.recv()                                    # op=login
            for m in first_messages:
                await ws.send(json.dumps(m))
            await ws.recv()                                    # op=subscribe
            await ws.send(json.dumps({"event": "subscribe"}))
            await ws.send(json.dumps({"event": "notice", "code": "64008", "msg": "reconnect soon"}))
            await ws.send(json.dumps({"data": [{"state": "filled", "instId": "BTC-USDT", "side": "sell",
                                                "ordId": "1", "avgPx": "100", "accFillSz": "0.1"}]}))
            sent.append(True)

        async def main():
            async with websockets.serve(server_handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                orig = okx._get_credentials
                okx._get_credentials = lambda: ("k", "s", "p", True)
                try:
                    stream = okx.OKXPrivateStream(None)
                    stream._ws_url = f"ws://127.0.0.1:{port}"
                    async for fill in stream.iter():
                        return fill
                finally:
                    okx._get_credentials = orig
        return asyncio.run(asyncio.wait_for(main(), 20))

    def test_notice_before_login_is_ignored(self):
        fill = self._run([{"event": "notice", "code": "64010", "msg": "port 8443 will be retired"},
                          {"event": "login", "code": "0"}])
        self.assertEqual((fill["instId"], fill["side"], fill["state"]), ("BTC-USDT", "sell", "filled"))

    def test_login_error_still_raises(self):
        with self.assertRaises(RuntimeError):
            self._run([{"event": "error", "code": "60009", "msg": "Login failed"}])

    def test_url_override_from_env(self):
        import os
        orig = okx._get_credentials
        okx._get_credentials = lambda: ("k", "s", "p", True)
        os.environ["OKX_WS_PRIVATE_URL"] = "wss://ws.okx.com/ws/v5/private"
        try:
            self.assertEqual(okx.OKXPrivateStream(None)._ws_url, "wss://ws.okx.com/ws/v5/private")
        finally:
            del os.environ["OKX_WS_PRIVATE_URL"]
            okx._get_credentials = orig


if __name__ == "__main__":
    unittest.main()
