"""Meta 1% Hoje!: métrica de janelas de 24 h, critérios do Top 3, regras do bot (parar na meta / sem ordem / stop loss /
trailing até a meta) e a regra permanente "só ativos do top 5" (dinâmico, conferido a cada criação)."""
import asyncio
import pathlib
import time
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from backend import goal as G
from backend import goal_service as svc
from backend.bot.goal import GoalMixin
from backend.common import _assert_top5_symbol
from backend.exchanges import okx_universe
from backend.exchanges.base import CandleBar
from backend.routers import bots as bots_router
from backend.routers import market as market_router
from backend.routers import market_data

H = 3_600_000


def run(coro):
    return asyncio.run(coro)


def hourly_candles(n=300):
    return [CandleBar(epoch=1_700_000_000_000 + i * H, open=100, high=101, low=99, close=100, volume=1) for i in range(n)]


def trade(entry_idx, exit_idx, net_pct, fee_pct=0.0):
    """Operação fechada do motor: `profit_pct` é o LÍQUIDO; o bruto é o líquido mais as taxas (`fee_pct`)."""
    return {"entry_time": entry_idx, "exit_time": exit_idx, "profit_pct": net_pct, "gross_profit_pct": net_pct + fee_pct}


# ── métrica: janelas móveis de 24 h ─────────────────────────────────────────────────────────────────────────────
def test_window_hits_counts_only_windows_that_contain_the_whole_winning_trade():
    cs = hourly_candles(300)
    res = G.window_hits([trade(150, 155, 1.2, fee_pct=0.2)], cs)          # bruto 1,4%, líquido 1,2%
    assert res["windows"] == 176 and res["independent"] == pytest.approx(199 / 24)
    assert res["real"] == 20 and res["illusory"] == 0     # inícios de 131 a 150: entrada >= início e saída <= início + 24 h


def test_two_small_trades_add_up_only_inside_the_same_24h_window():
    cs = hourly_candles(300)
    assert G.window_hits([trade(150, 152, 0.6), trade(160, 162, 0.6)], cs)["real"] > 0       # 12 h entre elas: soma 1,2%
    assert G.window_hits([trade(150, 152, 0.6), trade(200, 202, 0.6)], cs)["real"] == 0      # 48 h entre elas: nunca na mesma janela


def test_a_trade_that_started_before_the_window_does_not_count():
    cs = hourly_candles(300)
    hits = G.window_hits([trade(120, 130, 1.5)], cs)["real"]
    assert hits == 15                                            # só inícios de 106 a 120: antes disso a saída não cabe, depois a entrada ficou para trás
    assert G.window_hits([trade(120, 130, 0.9)], cs)["real"] == 0     # e abaixo de +1% bruto nunca é acerto


def full_trade(entry, exit_, gross, net, peak):
    return {"entry_time": entry, "exit_time": exit_, "gross_profit_pct": gross, "profit_pct": net, "max_gross_pct": peak}


def test_the_bot_sells_when_the_peak_covers_the_goal_even_if_the_trade_closes_lower():
    cs = hourly_candles(300)
    # a operação chegou a +1,3% bruto e fechou em −0,6% (stop): o bot teria vendido na meta, a +1% bruto
    res = G.window_hits([full_trade(150, 155, -0.6, -0.8, 1.3)], cs)
    assert res["real"] == 20 and res["stop_loss"] == 0 and res["illusory"] == 0
    # sem o pico cobrindo a meta, a mesma operação é um stop loss
    res = G.window_hits([full_trade(150, 155, -0.6, -0.8, 0.4)], cs)
    assert res["real"] == 0 and res["stop_loss"] == 20


def test_every_window_ends_in_exactly_one_outcome_and_the_average_net_counts_the_losing_sessions():
    cs = hourly_candles(300)
    loss = G.window_hits([full_trade(150, 155, -0.4, -0.6, 0.3)], cs)             # 20 sessões acabam em stop loss
    assert loss["stop_loss"] == 20 and loss["no_order"] == loss["windows"] - 20
    assert loss["expected_net_pct"] == pytest.approx(-0.6 * 20 / loss["windows"])
    small = G.window_hits([full_trade(150, 155, 0.5, 0.3, 0.5)], cs)               # operou, ganhou pouco, não chegou à meta
    assert small["expired"] == 20 and small["real"] == 0 and small["stop_loss"] == 0
    assert small["expected_net_pct"] == pytest.approx(0.3 * 20 / small["windows"])
    for res in (loss, small, G.window_hits([], cs), G.window_hits([full_trade(150, 155, 1.4, 1.2, 1.4)], cs)):
        assert res["real"] + res["illusory"] + res["stop_loss"] + res["expired"] + res["no_order"] == res["windows"]


def test_hits_alone_do_not_make_a_good_strategy_when_stop_losses_outweigh_them():
    """O caso real (SC004 no PEPE): muitas sessões batem a meta, mas as que terminam em stop loss custam mais."""
    cs = hourly_candles(1000)
    trades = []
    for i in range(30):                                                     # 30 operações: 2 de cada 3 batem +1% bruto (≈ +0,8% líquido)...
        e = 110 + 28 * i
        if i % 3 != 2:
            trades.append(full_trade(e, e + 2, 0.5, 0.3, 1.2))
        else:                                                               # ...e 1 de cada 3 perde 2,5% (stop loss)
            trades.append(full_trade(e, e + 2, -2.3, -2.5, 0.2))
    res = G.window_hits(trades, cs)
    assert res["real"] / res["windows"] > 0.4                              # a "chance" parece ótima
    assert res["stop_loss"] > 0
    ok, unmet = G.qualification({"trades_count": 30, "total_profit": 1.0, "profit_factor": 1.5,
                                 "chance": res["real"] / res["windows"], "independent_windows": res["independent"],
                                 "expected_net_pct": res["expected_net_pct"]})
    assert res["expected_net_pct"] < 0                                     # 2/3 x (+0,8%) + 1/3 x (−2,5%) ≈ −0,3% por sessão com operação
    assert not ok and any("Resultado líquido médio por sessão" in m for m in unmet)    # o critério líquido barra, não a chance


def test_gross_target_hit_whose_net_is_eaten_by_fees_is_illusory_not_real():
    cs = hourly_candles(300)
    # 6 operações de +0,2% bruto com 0,25% de taxas cada: o bruto chega a +1% (ilusão), mas o líquido fica negativo
    scalper = [trade(150 + 3 * i, 151 + 3 * i, -0.05, fee_pct=0.25) for i in range(6)]
    res = G.window_hits(scalper, cs)
    assert res["real"] == 0 and res["illusory"] > 0
    # uma operação só: +1,0% bruto = +0,8% líquido, acerto REAL
    one = G.window_hits([trade(150, 155, 0.8, fee_pct=0.2)], cs)
    assert one["real"] == 20 and one["illusory"] == 0
    # bruto na meta e líquido exatamente zero também é ilusório: o líquido precisa ser POSITIVO
    zero = G.window_hits([trade(150, 155, 0.0, fee_pct=1.0)], cs)
    assert zero["real"] == 0 and zero["illusory"] == 20


def test_the_gross_target_has_a_smaller_net_equivalent():
    assert G.net_equivalent_pct(1.0, fee=0.001) == pytest.approx(0.799)       # +1% bruto ≈ +0,80% líquido
    assert G.net_equivalent_pct(1.0, fee=0.0) == 1.0


def test_wilson_lower_bound_punishes_small_samples():
    assert G.wilson_lower_bound(0.0, 30) == 0.0
    assert G.wilson_lower_bound(0.5, 100) == pytest.approx(0.404, abs=0.005)
    assert G.wilson_lower_bound(0.2, 10) < G.wilson_lower_bound(0.2, 100) < 0.2


def test_qualification_explains_every_unmet_criterion():
    ok, unmet = G.qualification({"trades_count": 20, "total_profit": 15.0, "profit_factor": 1.6, "chance": 0.30,
                                 "independent_windows": 30.0, "expected_net_pct": 0.3})
    assert ok and unmet == []
    ok, unmet = G.qualification({"trades_count": 4, "total_profit": -3.0, "profit_factor": 0.4, "chance": 0.05,
                                 "independent_windows": 2.0, "expected_net_pct": -0.4})
    assert not ok and len(unmet) == 6
    assert any("Histórico curto" in m for m in unmet)
    assert any("Poucas operações" in m for m in unmet) and any("negativo" in m for m in unmet)
    assert any("Profit Factor" in m for m in unmet) and any("Chance" in m for m in unmet)
    # chance altíssima medida em 2 dias de histórico não qualifica
    ok, unmet = G.qualification({"trades_count": 40, "total_profit": 9.0, "profit_factor": 2.0, "chance": 0.9,
                                 "independent_windows": 2.0, "expected_net_pct": 0.2})
    assert not ok and len(unmet) == 1 and "Histórico curto" in unmet[0]
    # a ilusão do caso real (SC004 no PEPE): bate a meta em 55% das janelas, mas as sessões que terminam em stop loss
    # fazem o resultado líquido médio por sessão ficar negativo: não qualifica
    ok, unmet = G.qualification({"trades_count": 42, "total_profit": 5.0, "profit_factor": 1.5, "chance": 0.55,
                                 "independent_windows": 30.0, "expected_net_pct": -0.15})
    assert not ok and len(unmet) == 1 and "Resultado líquido médio por sessão" in unmet[0]


# ── regras do bot (funções puras) ────────────────────────────────────────────────────────────────────────────────
T0 = datetime(2026, 10, 31, 12, 0, 0)
BASE = dict(started_at=T0, window_hours=24.0, goal_pct=1.0)


def tick(**kw):
    args = dict(BASE, now=T0 + timedelta(hours=3), realized_gross_pct=0.0, in_position=False, open_gross_pct=0.0, had_order=False)
    args.update(kw)
    return G.decide_tick(**args)


def test_position_is_sold_when_open_net_profit_covers_what_is_missing():
    assert tick(in_position=True, open_gross_pct=0.5) == ("continue", "")
    assert tick(in_position=True, open_gross_pct=1.2) == ("take_profit", "reached")
    # já realizou +0,6%: só faltam 0,4% (+ folga de slippage)
    assert tick(in_position=True, realized_gross_pct=0.6, open_gross_pct=0.3) == ("continue", "")
    assert tick(in_position=True, realized_gross_pct=0.6, open_gross_pct=0.5) == ("take_profit", "reached")


def test_with_an_open_position_the_bot_never_stops_because_of_time():
    late = T0 + timedelta(hours=40)
    assert tick(now=late, in_position=True, open_gross_pct=0.2, had_order=True) == ("continue", "")


def test_flat_bot_stops_after_24h_without_any_order_or_without_the_goal():
    assert tick(now=T0 + timedelta(hours=23)) == ("continue", "")
    assert tick(now=T0 + timedelta(hours=24), had_order=False) == ("stop", "no_order")
    assert tick(now=T0 + timedelta(hours=24), had_order=True) == ("stop", "expired")
    assert tick(realized_gross_pct=1.05) == ("stop", "reached")


def test_after_a_close_the_session_ends_on_goal_or_loss_but_keeps_going_after_a_small_trailing_gain():
    def after(**kw):
        args = dict(BASE, now=T0 + timedelta(hours=5), realized_gross_pct=0.3, closed_gross_pct=0.3)
        args.update(kw)
        return G.decide_after_close(**args)
    assert after() == ("continue", "")                                        # trailing saiu com +0,3%: ainda há tempo
    assert after(realized_gross_pct=1.1, closed_gross_pct=1.1) == ("stop", "reached")
    assert after(realized_gross_pct=-0.8, closed_gross_pct=-0.8) == ("stop", "stop_loss")
    assert after(now=T0 + timedelta(hours=25)) == ("stop", "expired")


# ── cola com o bot ───────────────────────────────────────────────────────────────────────────────────────────────
def set_realized(bot, gross, net):
    bot._goal_realized_gross_pct, bot._goal_realized_net_pct = gross, net


class GoalBot(GoalMixin):
    def __init__(self, started=T0, direction=0, entry=100.0, sz=1.0):
        self.config = NS(id=7, name="bot", symbol="SOL-USDT", stake_usd=100.0, goal_pct=1.0, goal_window_hours=24.0,
                         goal_started_at=started, goal_outcome=None)
        self._direction, self._entry_price, self._sz = direction, entry, sz
        self._goal_loaded, self._goal_done, self._goal_closing = True, False, False
        self._goal_realized_gross_pct = self._goal_realized_net_pct = 0.0
        self._goal_had_order, self._goal_outcome, self._goal_retry_at = False, None, 0.0
        self._hold_reason, self.log = "", []
        self.now = T0 + timedelta(hours=3)
        self._notifier = NS(send=self._send)

    def _utcnow(self):
        return self.now

    async def _send(self, msg):
        self.log.append(("telegram", msg))

    async def _broadcast(self, msg):
        self.log.append(("broadcast", msg["outcome"]))

    def _goal_persist(self, outcome):
        self.log.append(("persist", outcome))

    def stop(self):
        self.log.append(("stop", None))

    async def manual_liquidate(self, exchange):
        self.log.append(("liquidate", None))
        return True


def test_bot_sells_at_market_when_the_net_profit_covers_the_goal():
    bot = GoalBot(direction=1, entry=100.0)
    run(bot._goal_tick(100.3, None))                 # +0,3% bruto: líquido negativo
    assert ("liquidate", None) not in bot.log
    run(bot._goal_tick(101.5, None))                 # +1,5% bruto ≈ +1,3% líquido
    assert ("liquidate", None) in bot.log
    assert not bot._goal_done                        # só encerra quando o fechamento é confirmado (_goal_after_close)


def test_bot_does_not_hammer_the_exchange_after_a_failed_sale():
    bot = GoalBot(direction=1, entry=100.0)

    async def fail(exchange):
        bot.log.append(("liquidate", None))
        return False
    bot.manual_liquidate = fail
    run(bot._goal_tick(102.0, None))
    run(bot._goal_tick(102.0, None))
    assert bot.log.count(("liquidate", None)) == 1


def test_flat_bot_stops_and_records_the_outcome_when_no_order_was_sent_in_24h():
    bot = GoalBot()
    bot.now = T0 + timedelta(hours=24, minutes=1)
    run(bot._goal_tick(100.0, None))
    assert bot._goal_done and bot._goal_outcome == "no_order"
    assert ("persist", "no_order") in bot.log and ("stop", None) in bot.log
    assert any(k == "telegram" and "NENHUMA ORDEM" in m.upper() for k, m in bot.log)


def test_open_position_after_24h_is_left_alone():
    bot = GoalBot(direction=1, entry=100.0)
    bot.now = T0 + timedelta(hours=30)
    run(bot._goal_tick(100.2, None))
    assert not bot._goal_done and ("stop", None) not in bot.log and ("liquidate", None) not in bot.log


def test_after_close_stops_on_goal_and_on_a_losing_exit_and_is_idempotent():
    reached = GoalBot()
    reached._goal_load = lambda: set_realized(reached, 1.1, 0.9)
    run(reached._goal_after_close(1.1))
    assert reached._goal_outcome == "reached"
    run(reached._goal_finish("expired"))                       # segunda chamada não muda nada
    assert reached._goal_outcome == "reached" and reached.log.count(("stop", None)) == 1

    loss = GoalBot()
    loss._goal_load = lambda: set_realized(loss, -0.7, -0.9)
    run(loss._goal_after_close(-0.7))
    assert loss._goal_outcome == "stop_loss"


def test_bot_without_goal_is_untouched():
    bot = GoalBot()
    bot.config.goal_pct = None
    bot.now = T0 + timedelta(days=9)
    run(bot._goal_tick(100.0, None))
    assert not bot._goal_done and bot.log == [] and bot._goal_view(100.0) is None


def test_goal_view_reports_time_left_and_session_result():
    bot = GoalBot(direction=1, entry=100.0)
    set_realized(bot, 0.4, 0.2)
    v = bot._goal_view(101.0)
    assert v["target_pct"] == 1.0 and v["in_position"] and v["done"] is False
    assert v["seconds_left"] == 21 * 3600 and v["deadline"].endswith("Z")
    assert v["realized_gross_pct"] == 0.4 and v["session_gross_pct"] == pytest.approx(0.4 + v["open_gross_pct"])
    assert v["realized_net_pct"] == 0.2 and v["session_net_pct"] == pytest.approx(0.2 + v["open_net_pct"])
    assert v["open_gross_pct"] == pytest.approx(1.0) and v["open_net_pct"] < v["open_gross_pct"]      # líquido < bruto
    assert v["net_equivalent_pct"] == pytest.approx(0.8, abs=0.01)


# ── busca e ranking ──────────────────────────────────────────────────────────────────────────────────────────────
def test_candidates_are_only_intraday_backtestable_strategies():
    cands = svc.candidate_strategies()
    assert cands and all(tf.lower() in G.INTRADAY_TIMEFRAMES for _, tf in cands)
    ids = {sid for sid, _ in cands}
    assert "TF008" not in ids and "IF001" not in ids          # 1D / contexto externo ficam de fora


def result_with(trades_pct, total_profit, pf, n_candles=1200):
    cs = hourly_candles(n_candles)
    trades = [trade(110 + 20 * i, 112 + 20 * i, p, fee_pct=0.2) for i, p in enumerate(trades_pct)]
    return {"closed_trades": trades, "trades_count": len(trades), "total_profit": total_profit,
            "profit_factor": pf, "max_drawdown": 3.0, "win_rate": 60.0}, cs


def test_build_row_and_rank_keep_non_qualifying_strategies_out_of_the_top():
    good, cs = result_with([1.3] * 20, 26.0, 2.4, n_candles=500)       # operações espalhadas por todo o período
    weak, _ = result_with([-0.5, 0.2] * 10, -4.0, 0.5, n_candles=500)
    row_good = svc.build_row("TF002", "SOL-USDT", "15m", good, cs)
    row_weak = svc.build_row("TF002", "SOL-USDT", "15m", weak, cs)
    assert row_good["qualifies"] is True and row_good["unmet"] == [] and row_good["chance_pct"] == 100.0
    assert row_good["gross_chance_pct"] == 100.0 and row_good["windows_illusory"] == 0
    assert row_weak["qualifies"] is False and row_weak["unmet"] and row_weak["chance_pct"] == 0.0
    top, closest = svc.rank([row_good, row_weak, dict(row_good, strategy_id="X", qualifies=False, unmet=["x"], chance_pct=1.0)])
    assert [r["strategy_id"] for r in top] == ["TF002"]
    assert all(not r["qualifies"] for r in closest)


def test_build_row_returns_none_without_enough_data_or_on_engine_errors():
    cs = hourly_candles(50)
    assert svc.build_row("TF002", "SOL-USDT", "15m", {"closed_trades": [], "trades_count": 0}, cs) is None
    assert svc.build_row("TF002", "SOL-USDT", "15m", {"error": "x"}, hourly_candles(500)) is None


# ── top 5 dinâmico ───────────────────────────────────────────────────────────────────────────────────────────────
def fake_top(symbols):
    async def fetch(n=5):
        return [{"symbol": s, "volume_usd_24h": 1e9, "change_24h_pct": 0.0, "last": 1.0} for s in symbols]
    return fetch


def test_live_status_flags_candidates_whose_asset_left_the_top_five(monkeypatch):
    monkeypatch.setattr(svc, "fetch_top_spot_assets", fake_top(["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "ZEC-USDT"]))
    monkeypatch.setattr(svc, "_state", dict(svc._fresh_state(), state="done", top=[{"symbol": "SOL-USDT"}, {"symbol": "PEPE-USDT"}], closest=[]))
    out = run(svc.live_status())
    assert out["top5_available"] and out["current_top5"][0] == "BTC-USDT"
    assert [r["in_top5_now"] for r in out["top"]] == [True, False]


def test_cached_scan_is_discarded_when_the_top_five_changed(monkeypatch):
    started = []

    async def fake_run():
        started.append(1)
    monkeypatch.setattr(svc, "_run", fake_run)
    done = dict(svc._fresh_state(), state="done", finished_ts=time.time(),
                assets={"top5": [{"symbol": s} for s in ("BTC-USDT", "ETH-USDT")], "used": [], "free": []})
    monkeypatch.setattr(svc, "_state", done)
    monkeypatch.setattr(svc, "fetch_top_spot_assets", fake_top(["BTC-USDT", "ETH-USDT"]))
    run(svc.start_scan())
    assert started == []                                           # mesmo top 5: usa o cache
    monkeypatch.setattr(svc, "_state", dict(done))
    monkeypatch.setattr(svc, "fetch_top_spot_assets", fake_top(["BTC-USDT", "HYPE-USDT"]))
    async def go():
        await svc.start_scan()
        await asyncio.sleep(0)
    run(go())
    assert started == [1]                                          # top 5 mudou: refaz a busca


# ── regra permanente: só ativos do top 5 ─────────────────────────────────────────────────────────────────────────
def test_creating_a_bot_outside_the_current_top_five_is_refused(monkeypatch):
    monkeypatch.setattr(okx_universe, "fetch_top_spot_assets", fake_top(["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "ZEC-USDT"]))
    run(_assert_top5_symbol("sol/usdt"))                           # normaliza e aceita
    with pytest.raises(HTTPException) as e:
        run(_assert_top5_symbol("DOGE-USDT"))
    assert e.value.status_code == 400 and "fora do top 5" in e.value.detail and "ZEC-USDT" in e.value.detail


def test_without_the_ranking_nothing_is_assumed(monkeypatch):
    monkeypatch.setattr(okx_universe, "fetch_top_spot_assets", fake_top([]))
    with pytest.raises(HTTPException) as e:
        run(_assert_top5_symbol("BTC-USDT"))
    assert e.value.status_code == 503


def test_bot_create_update_and_switch_all_enforce_the_top_five():
    text = pathlib.Path(bots_router.__file__).read_text(encoding="utf-8")
    assert text.count("await _assert_top5_symbol(") == 3


def test_rank_assets_has_no_fallback_list(monkeypatch):
    monkeypatch.setattr(market_router, "fetch_top_spot_assets", fake_top([]))
    with pytest.raises(HTTPException) as e:
        run(market_router.rank_assets("graph_regime", "15m"))
    assert e.value.status_code == 503


def test_market_data_offers_and_tracks_only_the_top_five(monkeypatch):
    top = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "ZEC-USDT"]
    monkeypatch.setattr(okx_universe, "fetch_top_spot_assets", fake_top(top))
    assert run(market_data.get_available_symbols()) == top          # sem ativos rastreados e sem lista reserva
    with pytest.raises(HTTPException) as e:
        run(market_data.add_tracked_symbol(NS(symbol="DOGE-USDT", timeframes=["1h"]), None, None, None))
    assert e.value.status_code == 400
    monkeypatch.setattr(okx_universe, "fetch_top_spot_assets", fake_top([]))
    assert run(market_data.get_available_symbols()) == []


def test_no_static_asset_list_is_left_in_the_code():
    root = pathlib.Path(bots_router.__file__).resolve().parents[2]
    offenders = []
    for path in list((root / "backend").rglob("*.py")) + list((root / "frontend" / "src").rglob("*.jsx")):
        text = path.read_text(encoding="utf-8")
        if "FALLBACK_SYMBOLS" in text or "get_ranked_assets_universe" in text or "_DEFAULT_OKX_SPOT_SYMBOLS" in text:
            offenders.append(path.name)
    assert offenders == []


# ── a meta também vem da linha do banco (bot parado/encerrado, sem runtime) ──────────────────────────────────────
def test_view_from_row_for_stopped_and_finished_bots():
    now = T0 + timedelta(hours=6)
    v = G.view_from_row(pct=1.0, window_hours=24, started_at=T0, outcome=None, now=now)
    assert v["seconds_left"] == 18 * 3600 and v["done"] is False and v["started_at"].endswith("Z")
    never = G.view_from_row(pct=1.0, window_hours=None, started_at=None, outcome=None)
    assert never["window_hours"] == 24.0 and never["started_at"] is None and never["seconds_left"] is None
    ended = G.view_from_row(pct=1.0, window_hours=24, started_at=T0, outcome="reached", now=now)
    assert ended["done"] is True and ended["outcome_label"] == "Meta bruta atingida"
    assert ended["net_equivalent_pct"] == pytest.approx(0.8, abs=0.01) and ended["illusory"] is False
    ill = G.view_from_row(pct=1.0, window_hours=24, started_at=T0, outcome="reached", now=now,
                          result_gross_pct=1.1, result_net_pct=-0.1)
    assert ill["illusory"] is True and ill["result_net_pct"] == -0.1      # meta bruta batida, líquido negativo
    assert G.view_from_row(pct=None, window_hours=None, started_at=T0, outcome=None) is None


def test_runtime_view_and_row_view_have_the_same_shape():
    bot = GoalBot(direction=1, entry=100.0)
    row = G.view_from_row(pct=1.0, window_hours=24, started_at=T0, outcome=None, now=bot.now)
    assert set(bot._goal_view(101.0)) == set(row)


def test_bots_list_returns_the_goal_even_without_a_running_bot():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend.database import BotModel, TradeModel
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (BotModel, TradeModel):
        model.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    db.add(BotModel(id=1, name="META 1% · TF002", strategy_id="TF002", symbol="SOL-USDT", goal_pct=1.0,
                    goal_window_hours=24.0, goal_started_at=T0, goal_ended_at=T0 + timedelta(hours=2), goal_outcome="stop_loss"))
    db.add(BotModel(id=2, name="comum", strategy_id="TF002", symbol="XRP-USDT"))
    db.commit()
    by_id = {b["id"]: b for b in bots_router.list_bots(db)}
    assert by_id[1]["goal"]["outcome"] == "stop_loss" and by_id[1]["goal"]["done"] is True
    assert by_id[2]["goal"] is None


# ── a busca carrega só o necessário, só ativos livres do top 5 ─────────────────────────────────────────────────────
def test_load_candles_for_asks_only_for_the_requested_limits(monkeypatch):
    from backend import backtest_service as bs
    from backend.candle_store import history_target
    seen = {}

    def fake_load(db, symbol, tf, limit):
        seen[tf] = limit
        return [object()]
    monkeypatch.setattr(bs, "load_candles", fake_load)
    out = run(bs.load_candles_for("SOL-USDT", {"15m", "1h", "1D"}, {"1h": 4400, "15m": 3000}))
    assert seen == {"1h": 4400, "15m": 3000, "1D": history_target("1D")}      # sem limite informado vale o histórico-alvo
    assert set(out) == {"15m", "1h", "1D"}


def test_full_scan_uses_only_free_top5_assets_loads_only_what_it_needs_and_ranks(monkeypatch):
    top = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "ZEC-USDT"]
    monkeypatch.setattr(svc, "fetch_top_spot_assets", fake_top(top))
    monkeypatch.setattr(svc, "_used_symbols", lambda: {"BTC-USDT", "ETH-USDT"})
    monkeypatch.setattr(svc, "candidate_strategies", lambda: [("TF002", "15m"), ("TF005", "15m"), ("TF008", "1D")])
    loads = []

    async def fake_load(symbol, tfs, limits=None):
        loads.append((symbol, set(tfs), dict(limits)))
        return {tf: hourly_candles(700) for tf in tfs}
    monkeypatch.setattr(svc, "load_candles_for", fake_load)
    good, _ = result_with([1.3] * 20, 26.0, 2.4, n_candles=500)
    weak, _ = result_with([-0.5, 0.2] * 10, -4.0, 0.5, n_candles=500)

    async def fake_run(sid, candles, extra, params):
        return good if sid == "TF002" else weak
    monkeypatch.setattr(svc, "run_strict_async", fake_run)
    monkeypatch.setattr(svc, "_state", svc._fresh_state())

    run(svc._run())
    st = svc.status()
    free = ["SOL-USDT", "XRP-USDT", "ZEC-USDT"]
    assert st["state"] == "done" and st["assets"]["free"] == free and st["assets"]["used"] == ["BTC-USDT", "ETH-USDT"]
    assert [sym for sym, _, _ in loads] == free                      # nunca carrega ativo com bot nem fora do top 5
    assert loads[0][2]["15m"] == 3000 and loads[0][2]["1W"] == svc.EXTRA_CAP    # só as velas necessárias
    assert st["progress"]["done"] == st["progress"]["total"] == 9
    assert [r["strategy_id"] for r in st["top"]] == ["TF002"] * 3 and all(r["symbol"] in free for r in st["top"])
    assert all(r["strategy_id"] != "TF002" for r in st["closest"]) and len(st["closest"]) == 3
    assert st["qualified"] == 3 and st["evaluated"] == 9


def test_diagnostic_strategies_are_never_candidates():
    ids = {sid for sid, _ in svc.candidate_strategies()}
    assert "T000" not in ids                      # estratégia de teste da plataforma (compra a cada candle)


def test_closest_list_prefers_rows_with_enough_history_over_a_flashy_short_sample():
    base = {"qualifies": False, "unmet": ["x"], "total_profit": -5.0}
    flashy = dict(base, strategy_id="FLASHY", chance_pct=76.0, chance_lb_pct=20.4, independent_windows=1.9)
    solid = dict(base, strategy_id="SOLID", chance_pct=9.0, chance_lb_pct=3.0, independent_windows=30.0)
    _, closest = svc.rank([flashy, solid])
    assert [r["strategy_id"] for r in closest] == ["SOLID", "FLASHY"]


def test_goal_candidates_skip_strategies_retired_by_the_study():
    from backend.validation.reports import study_summary
    ids = [sid for sid, _ in svc.candidate_strategies()]
    assert ids and all(study_summary(sid)["study_verdict"] != "retired" for sid in ids)
