"""Versão exibida na tela e universo de ativos: a aba sabe com qual versão foi carregada; ativos vêm do top 5 da OKX."""
import asyncio
from types import SimpleNamespace as NS

from backend import version as version_mod
from backend.routers import market_data, spa, system


def test_index_receives_the_server_version_as_meta():
    html = "<html><head><title>x</title></head><body></body></html>"
    out = spa.render_index(html, "d309b73")
    assert '<meta name="app-version" content="d309b73"></head>' in out
    assert out.count("app-version") == 1


def test_index_version_is_escaped():
    assert 'content="a&quot;b"' in spa.render_index("<head></head>", 'a"b')


def test_health_reports_the_same_version_the_index_carries(monkeypatch):
    monkeypatch.setattr(version_mod, "_cache", {"version": "abc1234", "timestamp": 1793390000})
    assert system.health_check() == {"ok": True, "version": "abc1234", "timestamp": 1793390000}
    assert 'content="abc1234"' in spa.render_index("<head></head>", version_mod.get_commit_info()["version"])


def test_commit_info_never_raises_without_git(monkeypatch, tmp_path):
    monkeypatch.setattr(version_mod, "_cache", None)
    monkeypatch.chdir(tmp_path)                                   # sem .git
    assert version_mod.get_commit_info() == {"version": "unknown", "timestamp": None}


class FakeDb:
    def __init__(self, tracked):
        self.tracked = tracked

    def query(self, *_):
        return NS(all=lambda: [(s,) for s in self.tracked])


def test_market_data_symbols_come_from_the_top_five(monkeypatch):
    async def top(n=5):
        return [{"symbol": s} for s in ("BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "ZEC-USDT")]
    monkeypatch.setattr(market_data, "fetch_top_spot_assets", top)
    out = asyncio.run(market_data.get_available_symbols(FakeDb(tracked=[])))
    symbols = [o["symbol"] if isinstance(o, dict) else o for o in out]
    assert symbols[:5] == ["BTC-USDT", "ETH-USDT", "SOL-USDT", "HYPE-USDT", "ZEC-USDT"]
    assert "DOGE-USDT" not in symbols and "BNB-USDT" not in symbols      # a lista antiga saiu


def test_market_data_keeps_already_tracked_symbols(monkeypatch):
    async def top(n=5):
        return [{"symbol": s} for s in ("BTC-USDT", "ETH-USDT", "SOL-USDT")]
    monkeypatch.setattr(market_data, "fetch_top_spot_assets", top)
    out = asyncio.run(market_data.get_available_symbols(FakeDb(tracked=["LINK-USDT"])))
    symbols = [o["symbol"] if isinstance(o, dict) else o for o in out]
    assert "LINK-USDT" in symbols                                         # dados já rastreados não se perdem
