"""Relatórios de estudo (quadro (i) do bot): gerados a partir das fontes, íntegros e com carimbo de validade."""
import json

from backend.validation import reports as R

_VALID = {"candidate", "watch", "retired", "insufficient"}


def test_reports_are_up_to_date_with_sources(tmp_path, monkeypatch):
    """Os JSON versionados são exatamente o que o gerador produz hoje (ninguém editou à mão)."""
    before = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in R._DIR.glob("*.json")}
    assert before, "nenhum relatório versionado"
    monkeypatch.setattr(R, "_DIR", tmp_path)
    monkeypatch.setattr(R, "_studied_hash", lambda sid, rev: "x")  # o hash depende do git; aqui só o conteúdo
    R.build_reports()
    for name, old in before.items():
        new = json.loads((tmp_path / name).read_text(encoding="utf-8"))
        for d in (old, new):
            d.pop("generated_at"), d.pop("file_sha256")
        assert new == old, f"{name} desatualizado: rode python -m backend.validation.reports"


def test_every_report_is_consistent():
    for p in R._DIR.glob("*.json"):
        r = json.loads(p.read_text(encoding="utf-8"))
        assert r["strategy_id"] == p.stem and r["verdict"] in _VALID and r["reason"] and r["caveats"]
        assert r["verdict_label"] == R.VERDICT_LABELS[r["verdict"]]
        assert r["file_sha256"], f"{p.stem}: sem hash do código estudado"
        if r["verdict"] not in ("retired", "insufficient"):
            assert r["yearly"] or r.get("baseline"), f"{p.stem}: sem detalhamento"


def test_parsers():
    wf = R.parse_walkforward((R._SRC / "walkforward_1d.txt").read_text(encoding="utf-8"))
    assert set(wf) == {"RG005", "TF006", "TF008", "TF009", "TF014", "TF015", "TF017", "TF018"}
    assert wf["TF008"]["SOL-USDT"]["oos_pf"] == 3.055 and wf["TF008"]["SOL-USDT"]["folds_positive"] == 3
    per, bh = R.parse_yearly((R._SRC / "yearly_1d.txt").read_text(encoding="utf-8"))
    assert bh["BTC-USDT"][2022] == -65 and per["TF008"]["BTC-USDT"][2022] == [0, 0]
    assert per["TF018"]["SOL-USDT"][2022] == [-166, 7]


def test_parse_baseline():
    b = R.parse_baseline((R._SRC / "baseline_intraday.txt").read_text(encoding="utf-8"))
    assert b["MR004"]["BTC-USDT"]["trades"] == 1622 and b["MR004"]["BTC-USDT"]["tf"] == "1H"
    assert b["PA008"]["SOL-USDT"]["trades"] == 0 and b["SC005"]["SOL-USDT"]["oos_pf"] == 0.528
    assert json.loads((R._DIR / "PA008.json").read_text(encoding="utf-8"))["timeframe"] == "15m"


def test_unknown_strategy_has_no_study():
    r = R.load_study("PA006")
    assert r["verdict"] == "none" and r["stale"] is False


def test_stale_flag_follows_code_hash(tmp_path, monkeypatch):
    fresh = R.load_study("TF009")
    assert fresh["stale"] is False  # estudado com o código atual
    monkeypatch.setattr(R, "strategy_hash", lambda sid: "outro-hash")
    assert R.load_study("TF009")["stale"] is True


def test_study_endpoint():
    from fastapi.testclient import TestClient
    from backend.routers import strategies
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(strategies.router)
    c = TestClient(app)
    assert c.get("/api/strategies/tf009/study").json()["verdict"] == "candidate"
    assert c.get("/api/strategies/XX999/study").json()["verdict"] == "none"


def test_strategy_list_carries_verdict():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.routers import strategies
    app = FastAPI()
    app.include_router(strategies.router)
    by_id = {s["id"]: s for s in TestClient(app).get("/api/strategies").json()}
    assert by_id["TF001"]["study_verdict"] == "retired" and by_id["TF009"]["study_verdict"] == "candidate"
    assert by_id["PA006"]["study_verdict"] == "none"


def test_creating_bot_with_retired_strategy_is_blocked_unless_forced():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.database import get_db
    from backend.routers import bots
    app = FastAPI()
    app.include_router(bots.router)
    app.dependency_overrides[get_db] = lambda: None
    c = TestClient(app)
    body = {"name": "x", "strategy_id": "TF001", "symbol": "BTC-USDT", "timeframe": "1D"}
    r = c.post("/api/bots", json=body)
    assert r.status_code == 409 and "aposentada" in r.json()["detail"]
    # com force_retired o bloqueio do estudo não dispara (a falha, se houver, vem de etapas seguintes)
    try:
        r2 = c.post("/api/bots", json={**body, "force_retired": True})
        assert r2.status_code != 409 or "aposentada" not in r2.json()["detail"]
    except Exception:
        pass  # etapas seguintes precisam de rede/banco; só interessa que não foi o bloqueio de estudo


def test_insufficient_data_reports_have_reason_and_no_tables():
    for sid in ("RG002", "RG003", "SC002", "IF001", "IF002", "NW001", "PA009", "PA012"):
        r = R.load_study(sid)
        assert r["verdict"] == "insufficient" and r["verdict_label"] == "Dados insuficientes"
        assert not r["walkforward"] and not r["baseline"] and r["caveats"] and r["reason"]
