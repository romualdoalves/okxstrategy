"""
Validação rigorosa das estratégias (fora do app): baseline medido e walk-forward.

Uso (no VPS, dentro do container, com os candles do banco):
    docker exec okx_strategy python -m backend.validation.run baseline \
        --ids TF --symbols BTC-USDT,ETH-USDT,SOL-USDT --tfs 1h,4h,1D --out /app/data/validation
    docker exec okx_strategy python -m backend.validation.run baseline --ids TF008 --legacy ...   # números ANTIGOS (com lookahead)
    docker exec okx_strategy python -m backend.validation.run walkforward --ids TF001 --symbols BTC-USDT --tfs 4h
    docker exec okx_strategy python -m backend.validation.run export --symbols BTC-USDT --tfs 4h,1D,1W --out /app/data/csv

`--ids TF` = todas as TF*. Sem banco: `--source csv --csv-dir DIR` com arquivos SIMBOLO_TF.csv
(colunas epoch,open,high,low,close,volume; epoch em ms).

Princípios: só candles fechados; custos do motor estrito (taxa 0,1%/lado + slippage 0,05%); a escolha de
parâmetros usa SÓ o treino de cada dobra (walk-forward ancorado) e avalia no teste seguinte; o score de seleção é
o percentil 5 do profit factor por bootstrap (penaliza amostra pequena); relatamos quantas combinações foram
testadas (risco de múltiplos testes) e se o parâmetro escolhido está num planalto ou num pico isolado.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import random
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..backtest_engine import BacktestEngine, INITIAL_BALANCE
from ..backtest_stats import bootstrap_profit_factor, profit_factor, summarize
from ..candle_store import drop_open_candle, timeframe_ms
from ..exchanges.base import CandleBar
from ..optimizer import StrategyOptimizer
from ..strategies.registry import REGISTRY

WARMUP = 100
YEAR_MS = 365.25 * 86_400_000
_DATA: dict[tuple[str, str], list] = {}


# ── Dados ────────────────────────────────────────────────────────────────────

def _load(symbol: str, tf: str, source: str, csv_dir: str | None) -> list:
    if source == "csv":
        for variant in (tf, tf.lower(), tf.upper()):
            p = Path(csv_dir or ".") / f"{symbol}_{variant}.csv"
            if p.exists():
                with open(p, newline="") as fh:
                    rows = [CandleBar(int(r["epoch"]), float(r["open"]), float(r["high"]),
                                      float(r["low"]), float(r["close"]), float(r["volume"]))
                            for r in csv.DictReader(fh)]
                return drop_open_candle(sorted(rows, key=lambda c: c.epoch), tf)
        return []
    from ..candle_store import load_candles
    from ..database import SessionLocal
    with SessionLocal() as db:
        for variant in dict.fromkeys((tf, tf.lower(), tf.upper())):
            c = load_candles(db, symbol, variant)
            if c:
                return drop_open_candle(c, tf)
    return []


def _series(symbol: str, tf: str, source: str, csv_dir: str | None) -> list:
    key = (symbol, tf)
    if key not in _DATA:
        _DATA[key] = _load(symbol, tf, source, csv_dir)
    return _DATA[key]


def _eligible_ids(spec: str) -> list[str]:
    ids = []
    for token in [t.strip() for t in spec.split(",") if t.strip()]:
        ids += [token] if token in REGISTRY else [s for s in REGISTRY if s.upper().startswith(token.upper())]
    out = []
    for sid in dict.fromkeys(ids):
        cls = REGISTRY.get(sid)
        if cls is None:
            print(f"[skip] {sid}: não existe no registry")
        elif any(getattr(cls, a, False) for a in dir(cls) if a.startswith("needs_")):
            print(f"[skip] {sid}: depende de contexto externo (não backtestável)")
        else:
            out.append(sid)
    return out


# ── Execução de um backtest ──────────────────────────────────────────────────

def _init_worker(data):
    _DATA.update(data)


def _base(symbol: str, tf: str, max_bars: int) -> list:
    c = _DATA[(symbol, tf)]
    return c[-max_bars:] if max_bars else c


def _run_job(job: dict) -> dict:
    t0 = time.time()
    try:
        out = _run_job_unsafe(job)
    except Exception as exc:  # um job com erro não pode derrubar as centenas seguintes
        out = {"job": {k: job[k] for k in ("sid", "symbol", "tf", "params")}, "trades": [], "mdd": 0.0,
               "n": 0, "error": f"{type(exc).__name__}: {exc}"}
    out["secs"] = round(time.time() - t0, 1)
    return out


def _run_job_unsafe(job: dict) -> dict:
    sid, params, symbol, tf = job["sid"], job["params"], job["symbol"], job["tf"]
    candles = _base(symbol, tf, job.get("max_bars", 0))
    extra = {x: _DATA[(symbol, x)] for x in job["extra"] if (symbol, x) in _DATA}
    res = BacktestEngine(sid, params or None).run_strict_sync(candles, extra_candles=extra,
                                                              legacy_lookahead=job["legacy"])
    trades = [{"e": t["entry_time"], "x": t["exit_time"], "p": t["profit"], "k": t["exit_type"], "b": t["bars_held"]}
              for t in res.get("closed_trades", [])]
    return {"job": {k: job[k] for k in ("sid", "symbol", "tf", "params")}, "trades": trades,
            "mdd": res.get("max_drawdown", 0.0), "n": len(candles), "error": res.get("error")}


def _run_many(jobs: list[dict], workers: int, quiet: bool = False) -> list[dict]:
    """Executa os jobs imprimindo o progresso (flush) a cada término. Ordem de saída = ordem dos jobs."""
    results: list = [None] * len(jobs)
    done, t0 = 0, time.time()

    def note(i, r):
        nonlocal done
        results[i] = r
        done += 1
        if not quiet:
            j = r["job"]
            print(f"[{done}/{len(jobs)}] {j['sid']} {j['symbol']} {j['tf']} trades={len(r['trades'])} "
                  f"{r['secs']}s total={time.time() - t0:.0f}s" + (f" ERRO={r['error']}" if r["error"] else ""),
                  flush=True)

    if workers <= 1 or len(jobs) == 1:
        for i, j in enumerate(jobs):
            note(i, _run_job(j))
        return results
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(dict(_DATA),)) as pool:
        futures = {pool.submit(_run_job, j): i for i, j in enumerate(jobs)}
        from concurrent.futures import as_completed
        for fut in as_completed(futures):
            note(futures[fut], fut.result())
    return results


def _metrics(r: dict, candles: list) -> dict:
    trades, n = r["trades"], r["n"]
    profits = [t["p"] for t in trades]
    s = summarize(profits)
    boot = bootstrap_profit_factor(profits)
    split = WARMUP + int((n - WARMUP) * 0.7)
    oos = summarize([t["p"] for t in trades if t["e"] >= split])
    years = max((candles[-1].epoch - candles[WARMUP].epoch) / YEAR_MS, 1e-9) if n > WARMUP else 0.0
    bh = (candles[-1].close / candles[WARMUP].close - 1) * 100 if n > WARMUP else 0.0
    # Queda máxima de quem apenas comprou e segurou na mesma janela (referência para o mdd da estratégia).
    bh_mdd, peak = 0.0, 0.0
    for c in candles[WARMUP:]:
        peak = max(peak, c.close)
        bh_mdd = max(bh_mdd, (peak - c.close) / peak * 100 if peak else 0.0)
    held = sum(t["b"] for t in trades)
    kinds: dict[str, int] = {}
    for t in trades:
        kinds[t["k"]] = kinds.get(t["k"], 0) + 1
    return {
        **{k: r["job"][k] for k in ("sid", "symbol", "tf")}, "bars": n, "years": round(years, 2),
        "trades": s["trades"], "trades_per_year": round(s["trades"] / years, 1) if years else 0,
        "win_rate": s["win_rate"], "pf": s["profit_factor"], "pf_p5": boot["pf_p5"],
        "prob_pf_gt_1": boot["prob_pf_gt_1"], "profit_usd": s["profit"],
        "avg_trade_pct": round(s["profit"] / s["trades"], 2) if s["trades"] else 0.0,   # stake fixa de US$100
        "oos_trades": oos["trades"], "oos_pf": oos["profit_factor"], "oos_profit": oos["profit"],
        "mdd_usd": r["mdd"], "exposure_pct": round(held / max(n - WARMUP, 1) * 100, 1),
        "profit_usd": s["profit"], "buy_hold_pct": round(bh, 1), "bh_mdd_pct": round(bh_mdd, 1),
        "exits": kinds, "error": r["error"],
    }


# ── Comandos ─────────────────────────────────────────────────────────────────

def cmd_baseline(a) -> None:
    ids, symbols, tfs = _eligible_ids(a.ids), a.symbols.split(","), a.tfs.split(",") if a.tfs else []
    jobs = []
    for sid in ids:
        cls = REGISTRY[sid]
        extra = list(cls.extra_timeframes() or [])
        for tf in (tfs or [cls.info().recommended_timeframe or "15m"]):
            for sym in symbols:
                if not _series(sym, tf, a.source, a.csv_dir):
                    print(f"[sem dados] {sym} {tf}")
                    continue
                if any(not _series(sym, x, a.source, a.csv_dir) for x in extra):
                    print(f"[sem dados extra] {sid} {sym} {tf} precisa de {extra}")
                    continue
                jobs.append({"sid": sid, "params": None, "symbol": sym, "tf": tf, "extra": extra,
                             "legacy": a.legacy, "max_bars": a.max_bars})
    print(f"{len(jobs)} backtests (workers={a.workers}, max_bars={a.max_bars or 'todas'})", flush=True)
    t0 = time.time()
    rows = []
    for r in _run_many(jobs, a.workers):
        if r["error"] or not r["n"]:
            rows.append({**r["job"], "error": r["error"] or "dados insuficientes", "trades": 0})
        else:
            rows.append(_metrics(r, _base(r["job"]["symbol"], r["job"]["tf"], a.max_bars)))
    _emit(rows, a.out, f"baseline{'_legacy' if a.legacy else ''}", time.time() - t0)


def cmd_walkforward(a) -> None:
    ids, symbols = _eligible_ids(a.ids), a.symbols.split(",")
    summary = []
    for sid in ids:
        space = StrategyOptimizer(sid).search_space
        if not space:
            print(f"[skip] {sid}: sem espaço de busca em optimizer.py")
            continue
        extra = list(REGISTRY[sid].extra_timeframes() or [])
        for tf in (a.tfs.split(",") if a.tfs else [REGISTRY[sid].info().recommended_timeframe or "15m"]):
            for sym in symbols:
                if not _series(sym, tf, a.source, a.csv_dir) or any(not _series(sym, x, a.source, a.csv_dir) for x in extra):
                    print(f"[sem dados] {sid} {sym} {tf}")
                    continue
                summary.append(_walkforward_one(sid, sym, tf, space, extra, a))
    _emit_wf(summary, a.out)


def _walkforward_one(sid, sym, tf, space, extra, a) -> dict:
    keys = list(space)
    combos = [dict(zip(keys, v)) for v in itertools.product(*space.values())]
    default = BacktestEngine(sid).strategy.info().params
    default_combo = {k: default[k].default for k in keys if k in default}
    if len(combos) > a.max_combos:
        rnd = random.Random(7)
        combos = rnd.sample(combos, a.max_combos)
    jobs = [{"sid": sid, "params": c, "symbol": sym, "tf": tf, "extra": extra, "legacy": False,
             "max_bars": a.max_bars} for c in combos]
    jobs.append({"sid": sid, "params": None, "symbol": sym, "tf": tf, "extra": extra, "legacy": False,
                 "max_bars": a.max_bars})  # parâmetros atuais
    print(f"{sid} {sym} {tf}: {len(jobs)} backtests", flush=True)
    t0 = time.time()
    results = _run_many(jobs, a.workers)
    base, runs = results[-1], results[:-1]
    n = base["n"]
    edges = [WARMUP + round((n - WARMUP) * i / (a.folds + 1)) for i in range(a.folds + 2)]

    def pick(trades_by_combo, lo, hi):
        best, best_score = None, -1.0
        for idx, r in enumerate(runs):
            tr = [t["p"] for t in r["trades"] if lo <= t["e"] < hi]
            if len(tr) < a.min_train_trades:
                continue
            score = bootstrap_profit_factor(tr)["pf_p5"]
            if score > best_score:
                best, best_score = idx, score
        return best

    wf_profits, base_profits, fold_rows = [], [], []
    for k in range(1, a.folds + 1):
        lo, hi, hi2 = edges[0], edges[k], edges[k + 1]
        idx = pick(runs, lo, hi)
        test_base = [t["p"] for t in base["trades"] if hi <= t["e"] < hi2]
        base_profits += test_base
        if idx is None:
            fold_rows.append({"fold": k, "chosen": None, "test": summarize([])})
            continue
        test = [t["p"] for t in runs[idx]["trades"] if hi <= t["e"] < hi2]
        wf_profits += test
        fold_rows.append({"fold": k, "chosen": runs[idx]["job"]["params"], "test": summarize(test)})

    final_idx = pick(runs, edges[0], edges[-1])
    plateau = None
    if final_idx is not None:
        chosen = runs[final_idx]["job"]["params"]
        plateau = _plateau(chosen, runs, space, edges)
    wf_boot = bootstrap_profit_factor(wf_profits)
    return {
        "sid": sid, "symbol": sym, "tf": tf, "trials": len(combos), "seconds": round(time.time() - t0, 1),
        "walkforward_oos": {**summarize(wf_profits), **wf_boot},
        "current_params_same_windows": summarize(base_profits),
        "folds_positive": sum(1 for f in fold_rows if f["test"]["profit"] > 0), "folds": a.folds,
        "folds_detail": fold_rows, "candidate_params": runs[final_idx]["job"]["params"] if final_idx is not None else None,
        "candidate_train_full": summarize([t["p"] for t in runs[final_idx]["trades"]]) if final_idx is not None else None,
        "plateau": plateau, "current_params": default_combo,
    }


def _plateau(chosen: dict, runs: list[dict], space: dict, edges: list[int]) -> dict:
    """Fração dos vizinhos (1 parâmetro mudado para o valor adjacente) cujo PF é >= 70% do escolhido e > 1."""
    by_params = {json.dumps(r["job"]["params"], sort_keys=True): r for r in runs}

    def pf_of(p):
        r = by_params.get(json.dumps(p, sort_keys=True))
        return profit_factor([t["p"] for t in r["trades"]]) if r else None

    ref = pf_of(chosen) or 0.0
    neigh = []
    for k, vals in space.items():
        if chosen.get(k) not in vals:
            continue
        i = vals.index(chosen[k])
        for j in (i - 1, i + 1):
            if 0 <= j < len(vals):
                neigh.append(pf_of({**chosen, k: vals[j]}))
    neigh = [x for x in neigh if x is not None]
    ok = sum(1 for x in neigh if x > 1.0 and x >= 0.7 * ref)
    return {"chosen_pf": round(ref, 3), "neighbors": len(neigh), "neighbors_ok": ok,
            "stable": bool(neigh) and ok / len(neigh) >= 0.6}


def cmd_yearly(a) -> None:
    """Lucro da estratégia por ANO (pela data de entrada) ao lado da variação do ativo no mesmo ano. Mostra se a
    estratégia ganha por tendência/bull market (anos de alta) ou se protege/ganha nos anos de baixa."""
    from datetime import datetime, timezone
    ids, symbols, tfs = _eligible_ids(a.ids), a.symbols.split(","), a.tfs.split(",") if a.tfs else []
    jobs = []
    for sid in ids:
        cls = REGISTRY[sid]
        extra = list(cls.extra_timeframes() or [])
        for tf in (tfs or [cls.info().recommended_timeframe or "15m"]):
            for sym in symbols:
                if _series(sym, tf, a.source, a.csv_dir) and all(_series(sym, x, a.source, a.csv_dir) for x in extra):
                    jobs.append({"sid": sid, "params": None, "symbol": sym, "tf": tf, "extra": extra,
                                 "legacy": False, "max_bars": a.max_bars})
    results = _run_many(jobs, a.workers, quiet=True)
    year_of = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).year
    print("\nano: lucro_US$(trades) | variacao do ativo no ano")
    seen_bh: set = set()
    for r in results:
        if r["error"] or not r["n"]:
            print(f"{r['job']['sid']} {r['job']['symbol']} {r['job']['tf']}: ERRO {r['error']}")
            continue
        j = r["job"]
        cs = _base(j["symbol"], j["tf"], a.max_bars)
        by_year: dict[int, list] = {}
        for t in r["trades"]:
            by_year.setdefault(year_of(cs[t["e"]].epoch), []).append(t["p"])
        closes: dict[int, tuple] = {}
        for c in cs[WARMUP:]:
            y = year_of(c.epoch)
            first, _ = closes.get(y, (c.close, c.close))
            closes[y] = (first, c.close)
        years = sorted(set(by_year) | set(closes))
        key = (j["symbol"], j["tf"])
        if key not in seen_bh:
            seen_bh.add(key)
            print(f"\n== {j['symbol']} {j['tf']} (buy & hold por ano) ==")
            print("   " + " | ".join(f"{y}: {(closes[y][1] / closes[y][0] - 1) * 100:+.0f}%" for y in years if y in closes))
        cells = []
        for y in years:
            pr = by_year.get(y, [])
            cells.append(f"{y}: {sum(pr):+.0f}({len(pr)})")
        print(f"{j['sid']} {j['symbol']} {j['tf']:>3} | " + " | ".join(cells))


def cmd_export(a) -> None:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for sym in a.symbols.split(","):
        for tf in a.tfs.split(","):
            c = _series(sym, tf, "db", None)
            with open(out / f"{sym}_{tf}.csv", "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["epoch", "open", "high", "low", "close", "volume"])
                w.writerows([[x.epoch, x.open, x.high, x.low, x.close, x.volume] for x in c])
            print(f"{sym} {tf}: {len(c)} candles")


# ── Saída ────────────────────────────────────────────────────────────────────

def _emit(rows: list[dict], out: str, name: str, secs: float) -> None:
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(rows, indent=1))
    cols = ["sid", "symbol", "tf", "years", "trades", "trades_per_year", "win_rate", "pf", "pf_p5", "prob_pf_gt_1",
            "avg_trade_pct", "profit_usd", "oos_trades", "oos_pf", "mdd_usd", "exposure_pct", "buy_hold_pct", "bh_mdd_pct"]
    with open(out_dir / f"{name}.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols + ["error"])
        w.writerows([[r.get(c) for c in cols + ["error"]] for r in rows])
    print(f"\n{name}: {len(rows)} séries em {secs:.0f}s -> {out_dir}")
    print(" | ".join(cols))
    for r in sorted(rows, key=lambda r: (r["sid"], r["symbol"], r["tf"])):
        print(" | ".join(str(r.get(c)) for c in cols) + (f" | ERRO: {r['error']}" if r.get("error") else ""))


def _emit_wf(summary: list[dict], out: str) -> None:
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "walkforward.json").write_text(json.dumps(summary, indent=1, default=str))
    print(f"\nwalkforward -> {out_dir / 'walkforward.json'}")
    for s in summary:
        wf, cur = s["walkforward_oos"], s["current_params_same_windows"]
        print(f"{s['sid']} {s['symbol']} {s['tf']}: trials={s['trials']} | WF-OOS trades={wf['trades']} PF={wf['profit_factor']} "
              f"p5={wf['pf_p5']} lucro=${wf['profit']} | parâmetros atuais nas mesmas janelas: trades={cur['trades']} "
              f"PF={cur['profit_factor']} lucro=${cur['profit']} | dobras+ {s['folds_positive']}/{s['folds']} | "
              f"planalto={s['plateau'] and s['plateau']['stable']} | candidato={s['candidate_params']}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["baseline", "walkforward", "yearly", "export"])
    p.add_argument("--ids", default="TF")
    p.add_argument("--symbols", default="BTC-USDT,ETH-USDT,SOL-USDT")
    p.add_argument("--tfs", default="")
    p.add_argument("--source", choices=["db", "csv"], default="db")
    p.add_argument("--csv-dir", default=None)
    p.add_argument("--out", default="validation_out")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--max-bars", type=int, default=0, help="usa só os N candles mais recentes de cada série (0 = todos)")
    p.add_argument("--legacy", action="store_true", help="reproduz o backtest ANTIGO (com lookahead multi-TF)")
    p.add_argument("--folds", type=int, default=4)
    p.add_argument("--max-combos", type=int, default=120)
    p.add_argument("--min-train-trades", type=int, default=8)
    a = p.parse_args()
    {"baseline": cmd_baseline, "walkforward": cmd_walkforward, "yearly": cmd_yearly, "export": cmd_export}[a.cmd](a)


if __name__ == "__main__":
    main()
