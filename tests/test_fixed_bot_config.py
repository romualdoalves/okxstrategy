"""Configuração FIXA do bot: o usuário não escolhe timeframe, parâmetros nem stop (isso pode prejudicar a execução).
O servidor ignora qualquer valor enviado: timeframe = o validado pelo estudo, parâmetros = padrões, stop diário = constante."""
import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend import goal_service as goal_svc
from backend.common import FIXED_DAILY_STOP_USD, FIXED_STAKE_USD
from backend.database import BotModel, TradeModel
from backend.routers import backtest as scanner_router
from backend.routers import bots as bots_router
from backend.routers import strategies as strategies_router
from backend.routers.bots import BotCreate, BotUpdate, create_bot, update_bot
from backend.validation.reports import canonical_timeframe, validated_timeframe


def run(coro):
    return asyncio.run(coro)


async def _ok(*_a, **_k):
    return None


class FakeExchange:
    async def get_position(self, symbol):
        return None


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for model in (BotModel, TradeModel):
        model.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    for name in ("_assert_top5_symbol", "_assert_symbol_tradable"):          # sem rede
        monkeypatch.setattr(bots_router, name, _ok)
    monkeypatch.setattr(bots_router, "build_exchange", lambda *a, **k: FakeExchange())
    yield session
    session.close()


# ── o timeframe validado é a fonte única ─────────────────────────────────────────────────────────────────────────
def test_canonical_timeframe_has_one_spelling():
    assert [canonical_timeframe(x) for x in ("15m", "1h", "1H", "4h", "1d", "1D", "1w", "5M")] == ["15m", "1H", "1H", "4H", "1D", "1D", "1W", "5m"]


def test_candidates_are_offered_in_the_timeframe_that_was_validated_not_the_registry_default():
    # TF009 é candidata SÓ em 1D; o registro sugere 15m, onde ela perde em todos os ativos.
    assert validated_timeframe("TF009-1D", "15m") == "1D"
    assert validated_timeframe("TF008-1D", "1D") == "1D"
    assert validated_timeframe("RG005-1D", "1d") == "1D"
    assert validated_timeframe("PA005-1H", "1h") == "1H"
    assert validated_timeframe("MR002", "15m") == "15m"
    assert validated_timeframe("IF001", "15m") == "15m"          # sem timeframe de estudo: vale o do registro


def test_strategies_api_only_advertises_the_validated_timeframe():
    by_id = {s["id"]: s for s in strategies_router.get_strategies()}
    assert by_id["TF009-1D"]["recommended_timeframe"] == "1D"
    assert all(s["recommended_timeframe"] == validated_timeframe(s["id"], s["recommended_timeframe"]) for s in by_id.values())


# ── o servidor ignora o que o cliente tentar configurar ─────────────────────────────────────────────────────────
def test_create_bot_ignores_timeframe_parameters_and_stop_sent_by_the_client(db):
    payload = BotCreate(name="hostil", strategy_id="TF008-1D", symbol="SOL-USDT", timeframe="1m",
                        strategy_params={"ema_period": 3, "sl_mult": 0.1}, stop_loss_usd=-1.0, leverage=10,
                        stake_usd=5000.0)
    run(create_bot(payload, db))
    bot = db.query(BotModel).one()
    assert bot.timeframe == "1D"                                  # o validado pelo estudo da TF008
    assert bot.strategy_params == {}                              # padrões da estratégia
    assert bot.stop_loss_usd == FIXED_DAILY_STOP_USD == -50.0
    assert bot.stake_usd == FIXED_STAKE_USD and bot.leverage == 1


def test_a_candidate_bot_is_created_in_its_validated_timeframe_even_if_the_client_asks_for_15m(db):
    run(create_bot(BotCreate(name="x", strategy_id="TF009-1D", symbol="XRP-USDT", timeframe="15m"), db))
    assert db.query(BotModel).one().timeframe == "1D"


def test_update_bot_only_changes_name_and_symbol(db):
    db.add(BotModel(id=1, name="antigo", strategy_id="TF008-1D", symbol="SOL-USDT", timeframe="1D", strategy_params={},
                    stop_loss_usd=-50.0, active=False))
    db.commit()
    run(update_bot(1, BotUpdate(name="novo", timeframe="1m", strategy_params={"sl_mult": 0.1}, stop_loss_usd=-1.0,
                                stake_usd=9999.0, leverage=20), db))
    bot = db.get(BotModel, 1)
    assert bot.name == "novo"
    assert bot.timeframe == "1D" and bot.strategy_params == {} and bot.stop_loss_usd == -50.0
    assert bot.stake_usd == FIXED_STAKE_USD and bot.leverage == 1


# ── Scanner e Meta usam o mesmo timeframe que o bot vai usar ────────────────────────────────────────────────────
def test_scanner_evaluates_each_strategy_in_the_timeframe_its_bot_will_use(monkeypatch):
    seen = {}

    async def fake_load(symbol, timeframes, limits=None):
        return {tf: ["candles"] for tf in timeframes}

    async def fake_run_one(sid, symbol, tf, cache, **kw):
        seen[sid] = tf
        return {"strategy_id": sid, "recommendation": {"verdict": "CUIDADO", "score": 5.0}}

    monkeypatch.setattr(scanner_router, "load_candles_for", fake_load)
    monkeypatch.setattr(scanner_router, "run_one", fake_run_one)
    run(scanner_router._run_category_backtest_core("TF", "BTC-USDT"))
    assert seen["TF009-1D"] == "1D" and seen["TF008-1D"] == "1D"
    assert all(tf == validated_timeframe(sid, tf) for sid, tf in seen.items())


def test_goal_candidates_are_only_strategies_validated_in_an_intraday_timeframe():
    cands = dict(goal_svc.candidate_strategies())
    assert "TF009-1D" not in cands and "TF017-1D" not in cands          # validadas em 1D: não servem para uma meta de 24 h
    assert cands and all(tf.lower() in ("1m", "5m", "15m", "1h") for tf in cands.values())
    assert all(tf == validated_timeframe(sid, tf) for sid, tf in cands.items())


# ── origem do bot: a página do menu em que foi criado ────────────────────────────────────────────────────────────
def test_bot_records_the_page_it_was_created_from(db):
    for i, (src, expected) in enumerate([("scanner", "scanner"), ("strategies", "strategies"), ("bots", "bots"),
                                         (None, "bots"), ("hacker", "bots")]):
        run(create_bot(BotCreate(name=f"b{i}", strategy_id="TF008-1D", symbol=f"S{i}-USDT", source=src), db))
        assert db.query(BotModel).filter(BotModel.name == f"b{i}").one().source == expected
    # com meta, a origem é sempre a Meta 1%, seja o que o cliente enviar
    run(create_bot(BotCreate(name="g", strategy_id="TF008-1D", symbol="G-USDT", goal_pct=1.0, source="scanner"), db))
    assert db.query(BotModel).filter(BotModel.name == "g").one().source == "goal"
    listed = {b["name"]: b["source"] for b in bots_router.list_bots(db)}
    assert listed["b0"] == "scanner" and listed["g"] == "goal"


# ── o ID da estratégia carrega o timeframe para o qual ela foi desenhada e validada ──────────────────────────────
def test_every_strategy_id_ends_with_its_validated_timeframe():
    from backend.strategies.registry import REGISTRY
    for sid, cls in REGISTRY.items():
        if sid == "T000":                                    # diagnóstico da plataforma
            continue
        assert sid.endswith("-" + validated_timeframe(sid, cls.info().recommended_timeframe)), sid
        assert sid == cls.info().id and cls.info().name.startswith(sid)


def test_old_ids_still_resolve_to_the_new_ones():
    from backend.strategies.registry import get_strategy, legacy_ids, resolve_strategy_id
    assert resolve_strategy_id("TF008") == "TF008-1D" == resolve_strategy_id("tf008-1d")
    assert resolve_strategy_id("PA005") == "PA005-1H" and resolve_strategy_id("T000") == "T000"
    assert resolve_strategy_id("XX999") == "XX999"
    assert get_strategy("TF008").info().id == "TF008-1D"
    assert legacy_ids()["TF008"] == "TF008-1D" and "T000" not in legacy_ids()


def test_studies_and_hashes_follow_the_new_ids_without_going_stale():
    from backend.validation import reports as R
    from backend.strategies.registry import REGISTRY
    for sid in REGISTRY:
        if sid == "T000":
            continue
        study = R.load_study(sid)
        assert study["strategy_id"] == sid and study["verdict"] != "none", sid
        assert study["stale"] is False, f"{sid}: renomear não pode marcar o estudo como desatualizado"
    assert R.load_study("TF008")["strategy_id"] == "TF008-1D"        # o ID antigo ainda acha o estudo


def test_create_bot_stores_the_canonical_id_even_if_the_old_one_is_sent(db):
    run(create_bot(BotCreate(name="velho", strategy_id="TF008", symbol="SOL-USDT"), db))
    assert db.query(BotModel).one().strategy_id == "TF008-1D"


def test_startup_migrates_bots_that_still_use_the_old_ids(db):
    from backend.lifecycle import normalize_bots
    db.add(BotModel(name="a", strategy_id="TF008", symbol="BTC-USDT", timeframe="1D"))
    db.add(BotModel(name="b", strategy_id="F001", symbol="ETH-USDT", timeframe="1D"))
    db.add(BotModel(name="c", strategy_id="PA005-1H", symbol="XRP-USDT", timeframe="1H"))
    db.commit()
    normalize_bots(db)
    assert sorted(b.strategy_id for b in db.query(BotModel).all()) == ["PA005-1H", "TF008-1D", "TF014-1D"]
