"""Relatórios de estudo por estratégia (mostrados no (i) do bot).

Os relatórios `reports/<ID>.json` são GERADOS por `python -m backend.validation.reports` a partir de:
  - reports/sources/walkforward_1d.txt   (saída do subcomando `walkforward`)
  - reports/sources/yearly_1d.txt        (saída do subcomando `yearly`)
  - reports/sources/verdicts.json        (veredito e justificativa, decididos por uma pessoa)
Cada relatório guarda o hash do arquivo da estratégia estudada: se o código mudar depois, o quadro avisa que os
resultados estão desatualizados.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_DIR = Path(__file__).resolve().parent / "reports"
_SRC = _DIR / "sources"
_FACTORY = Path(__file__).resolve().parent.parent / "strategies" / "factory"

VERDICT_LABELS = {"candidate": "Candidata", "watch": "Em observação", "retired": "Aposentada"}
CAVEATS = [
    "Só o timeframe 1D foi estudado (BTC e ETH desde 2018, SOL desde 2021).",
    "A janela contém um único mercado de baixa de verdade (2022); 2018 fica no período de aquecimento.",
    "Amostras pequenas (poucas dezenas de trades fora da amostra): os resultados são indícios, não prova.",
    "Valores em US$ do backtest (taxa 0,1% por lado e slippage 0,05%); não garantem resultado futuro.",
]

_WF_RE = re.compile(
    r"^(?P<sid>\w+) (?P<sym>\S+) (?P<tf>\S+): trials=(?P<trials>\d+) \| WF-OOS trades=(?P<t>\d+) PF=(?P<pf>[\d.]+) "
    r"p5=(?P<p5>[\d.]+) lucro=\$(?P<pr>-?[\d.]+) \| parâmetros atuais nas mesmas janelas: trades=(?P<ct>\d+) "
    r"PF=(?P<cpf>[\d.]+) lucro=\$(?P<cpr>-?[\d.]+) \| dobras\+ (?P<fp>\d+)/(?P<ft>\d+) \| planalto=(?P<pl>True|False) "
    r"\| candidato=(?P<cand>\{.*\})\s*$")
_YEAR_CELL = re.compile(r"(\d{4}): ([+-]?\d+)\((\d+)\)")
_BH_CELL = re.compile(r"(\d{4}): ([+-]\d+)%")


def hash_text(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def strategy_hash(sid: str) -> str | None:
    path = _FACTORY / f"{sid.lower()}.py"
    return hash_text(path.read_text(encoding="utf-8")) if path.exists() else None


def parse_walkforward(text: str) -> dict:
    out: dict[str, dict] = {}
    for line in text.splitlines():
        m = _WF_RE.match(line.strip())
        if not m:
            continue
        out.setdefault(m["sid"], {})[m["sym"]] = {
            "trials": int(m["trials"]), "oos_trades": int(m["t"]), "oos_pf": float(m["pf"]), "oos_p5": float(m["p5"]),
            "oos_profit": float(m["pr"]), "current_trades": int(m["ct"]), "current_pf": float(m["cpf"]),
            "current_profit": float(m["cpr"]), "folds_positive": int(m["fp"]), "folds_total": int(m["ft"]),
            "plateau": m["pl"] == "True", "candidate": ast.literal_eval(m["cand"]),
        }
    return out


def parse_yearly(text: str) -> tuple[dict, dict]:
    """Retorna (por_estrategia[sid][ativo][ano] = [lucro, trades], buy_hold[ativo][ano] = %)."""
    per: dict[str, dict] = {}
    bh: dict[str, dict] = {}
    symbol = None
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^== (\S+) \S+ \(buy & hold por ano\) ==$", line)
        if m:
            symbol = m.group(1)
            continue
        if symbol and line and line.startswith("20") and symbol not in bh:
            bh[symbol] = {int(y): int(p) for y, p in _BH_CELL.findall(line)}
            continue
        m = re.match(r"^(\w+) (\S+)\s+\S+ \| (.*)$", line)
        if m:
            per.setdefault(m.group(1), {})[m.group(2)] = {
                int(y): [int(p), int(n)] for y, p, n in _YEAR_CELL.findall(m.group(3))}
    return per, bh


def _studied_hash(sid: str, rev: str | None) -> str | None:
    if not rev:
        return strategy_hash(sid)
    out = subprocess.run(["git", "show", f"{rev}:OKXStrategy/backend/strategies/factory/{sid.lower()}.py"],
                         capture_output=True, text=True, cwd=Path(__file__).resolve().parent, encoding="utf-8")
    if out.returncode != 0:  # repositório com a raiz em OKXStrategy
        out = subprocess.run(["git", "show", f"{rev}:backend/strategies/factory/{sid.lower()}.py"],
                             capture_output=True, text=True, cwd=Path(__file__).resolve().parent, encoding="utf-8")
    if out.returncode != 0:
        raise RuntimeError(f"git show falhou para {sid}@{rev}: {out.stderr.strip()}")
    return hash_text(out.stdout)


def build_reports() -> list[str]:
    wf = parse_walkforward((_SRC / "walkforward_1d.txt").read_text(encoding="utf-8"))
    yearly, bh = parse_yearly((_SRC / "yearly_1d.txt").read_text(encoding="utf-8"))
    verdicts = json.loads((_SRC / "verdicts.json").read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    written = []
    for sid, v in verdicts.items():
        report = {
            "strategy_id": sid, "timeframe": "1D", "generated_at": now,
            "verdict": v["verdict"], "verdict_label": VERDICT_LABELS[v["verdict"]], "reason": v["reason"],
            "applied_params": v.get("applied_params", {}), "note": v.get("note"),
            "file_sha256": _studied_hash(sid, v.get("studied_rev")),
            "walkforward": wf.get(sid, {}), "yearly": yearly.get(sid, {}), "buy_hold": bh,
            "caveats": CAVEATS,
        }
        (_DIR / f"{sid}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        written.append(sid)
    return written


def load_study(sid: str) -> dict:
    """Relatório do estudo da estratégia, com `stale=True` se o código mudou desde o estudo."""
    path = _DIR / f"{sid.upper()}.json"
    if not path.exists():
        return {"strategy_id": sid.upper(), "verdict": "none", "verdict_label": "Sem estudo",
                "reason": "Esta estratégia ainda não passou pela revisão (baseline e walk-forward).", "stale": False}
    report = json.loads(path.read_text(encoding="utf-8"))
    current = strategy_hash(sid)
    report["stale"] = bool(report.get("file_sha256") and current and report["file_sha256"] != current)
    return report


if __name__ == "__main__":
    print("gerados:", ", ".join(build_reports()))
