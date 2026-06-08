"""
strategy_factory/validator.py — Etapa 3 da fábrica: validação do código gerado.

Executa a estratégia em um ambiente controlado com candles sintéticos para
verificar: sintaxe, imports, instanciação, retorno correto e critérios.
"""
from __future__ import annotations

import ast
import logging
import math
import random
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("strategy_factory.validator")

FORBIDDEN_PATTERNS = [
    "subprocess", "os.system", "os.popen", "os.exec",
    "eval(", "exec(", "__import__", "socket.",
    "requests.", "aiohttp.", "urllib.", "http.",
    "open(", "pathlib.", "shutil.", "tempfile.",
]

ALLOWED_IMPORTS = {
    "__future__", "typing", "math", "re", "dataclasses",
    "pandas", "pandas_ta", "numpy",
    "..base",  # import relativo da base
    "backend",  # import absoluto do backend (strategy_factory)
}


@dataclass
class ValidationReport:
    passed: bool = False
    checks: list[dict[str, Any]] = field(default_factory=list)
    error:  str = ""

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "ok": ok, "detail": detail})

    def summary(self) -> dict:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "error":  self.error,
            "ok_count":   sum(1 for c in self.checks if c["ok"]),
            "fail_count": sum(1 for c in self.checks if not c["ok"]),
        }


# ── CandleBar mock ─────────────────────────────────────────────────────────────

class _CandleBar:
    """Mock de candle para testes — mesma interface do CandleBar real.
    Suporta tanto .epoch (nome correto) quanto .timestamp (compatibilidade
    com código gerado por IA que pode usar qualquer um dos dois)."""
    __slots__ = ("epoch", "open", "high", "low", "close", "volume")

    def __init__(self, o, h, l, c, v, ts=0):
        self.epoch = int(ts)          # ms — mesmo nome do CandleBar real
        self.open = float(o); self.high = float(h)
        self.low  = float(l); self.close = float(c)
        self.volume = float(v)

    @property
    def timestamp(self) -> int:
        """Compatibilidade com código que usa candle.timestamp em vez de candle.epoch."""
        return self.epoch

    @property
    def time(self) -> int:
        """Compatibilidade com código que usa candle.time em vez de candle.epoch."""
        return self.epoch


def _make_candles(n: int = 250, seed: int = 42) -> list[_CandleBar]:
    """Gera n candles OHLCV sintéticos com tendência aleatória."""
    rng = random.Random(seed)
    price = 100.0
    base_ts = 1700000000000  # ~2023-11-14 em ms
    candles = []
    for i in range(n):
        change = rng.gauss(0, 0.8)
        price  = max(1.0, price * (1 + change / 100))
        spread = price * rng.uniform(0.001, 0.015)
        o = price
        c = price + rng.gauss(0, spread / 2)
        h = max(o, c) + rng.uniform(0, spread)
        l = min(o, c) - rng.uniform(0, spread)
        v = rng.uniform(500, 5000)
        ts = base_ts + i * 60000  # +1 minuto por candle em ms
        candles.append(_CandleBar(o, h, max(l, 0.01), c, v, ts))
    return candles


# ── Validador principal ────────────────────────────────────────────────────────

def validate_code(code: str, plan: dict) -> ValidationReport:
    """
    Valida o código gerado sincronamente.
    Executa dentro do processo principal em namespace isolado.
    Retorna ValidationReport com resultado de cada verificação.
    """
    report = ValidationReport()

    # ── Verificação 1: Sintaxe ────────────────────────────────────────────────
    try:
        compile(code, "<strategy>", "exec")
        report.add("Sintaxe Python válida", True)
    except SyntaxError as e:
        report.add("Sintaxe Python válida", False, str(e))
        report.error = f"Erro de sintaxe: {e}"
        return report

    # ── Verificação 2: Imports proibidos ─────────────────────────────────────
    forbidden_found = [p for p in FORBIDDEN_PATTERNS if p in code]
    if forbidden_found:
        detail = ", ".join(forbidden_found)
        report.add("Sem imports proibidos", False, f"Encontrado: {detail}")
        report.error = f"Imports proibidos: {detail}"
        return report
    report.add("Sem imports proibidos", True)

    # ── Verificação 3: Imports do AST ─────────────────────────────────────────
    try:
        tree = ast.parse(code)
        bad_imports = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = getattr(node, "module", "") or ""
                for alias in getattr(node, "names", []):
                    full = f"{mod}.{alias.name}" if mod else alias.name
                    top  = full.split(".")[0] if not full.startswith(".") else full
                    ok   = any(top.startswith(a) or top == a for a in ALLOWED_IMPORTS)
                    if not ok:
                        bad_imports.append(full)
        if bad_imports:
            report.add("Imports do AST seguros", False, f"Não permitidos: {bad_imports}")
        else:
            report.add("Imports do AST seguros", True)
    except Exception as e:
        report.add("Imports do AST seguros", False, str(e))

    # ── Verificação 4: Execução em namespace isolado ──────────────────────────
    from ..strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult

    namespace: dict[str, Any] = {
        "__name__":    "__strategy_validate__",
        "__package__": "backend.strategies.factory",
        "BaseStrategy": BaseStrategy,
        "ParamDef":    ParamDef,
        "Signal":      Signal,
        "StrategyInfo": StrategyInfo,
        "StrategyResult": StrategyResult,
        "Optional":    __import__("typing").Optional,
        "pd":          __import__("pandas"),
        "ta":          __import__("pandas_ta"),
        "math":        math,
    }

    # Patch imports relativos/absolutos → objetos já no namespace
    import re
    patched = re.sub(
        r"from\s+\.\.base\s+import\s+[^\n]+",
        "# (base imports injetados pelo validador)",
        code,
    )
    patched = re.sub(
        r"from\s+backend\.strategies\.base\s+import\s+[^\n]+",
        "# (base imports injetados pelo validador)",
        patched,
    )
    patched = re.sub(r"from\s+__future__\s+import\s+[^\n]+", "", patched)
    patched = re.sub(r"import\s+math\s*\n", "", patched)
    patched = re.sub(r"import\s+pandas\s+as\s+pd\s*\n", "", patched)
    patched = re.sub(r"import\s+pandas_ta\s+as\s+ta\s*\n", "", patched)
    patched = re.sub(r"from\s+typing\s+import\s+[^\n]+", "", patched)

    try:
        exec(compile(patched, "<strategy>", "exec"), namespace)
        report.add("Execução do módulo sem erros", True)
    except Exception as e:
        report.add("Execução do módulo sem erros", False, str(e))
        report.error = f"Erro ao executar módulo: {e}"
        return report

    # ── Verificação 5: Classe encontrada e é BaseStrategy ────────────────────
    strategy_cls = None
    for obj in namespace.values():
        try:
            if (isinstance(obj, type) and issubclass(obj, BaseStrategy)
                    and obj is not BaseStrategy):
                strategy_cls = obj
                break
        except TypeError:
            pass

    if strategy_cls is None:
        report.add("Classe BaseStrategy encontrada", False, "Nenhuma classe derivada de BaseStrategy")
        report.error = "Classe de estratégia não encontrada"
        return report
    report.add("Classe BaseStrategy encontrada", True, strategy_cls.__name__)

    # ── Verificação 6: info() retorna StrategyInfo válida ────────────────────
    try:
        info = strategy_cls.info()
        assert isinstance(info, StrategyInfo), "info() deve retornar StrategyInfo"
        assert info.id,   "StrategyInfo.id está vazio"
        assert info.name, "StrategyInfo.name está vazio"
        report.add("info() retorna StrategyInfo válida", True, f"id={info.id} name={info.name}")
    except Exception as e:
        report.add("info() retorna StrategyInfo válida", False, str(e))
        report.error = str(e)
        return report

    # ── Verificação 7: Instanciação sem erros ────────────────────────────────
    try:
        instance = strategy_cls()
        report.add("Instanciação sem erros", True)
    except Exception as e:
        report.add("Instanciação sem erros", False, str(e))
        report.error = str(e)
        return report

    # ── Verificação 8: compute_with_context em aquecimento ───────────────────
    few_candles = _make_candles(5)
    try:
        result = instance.compute_with_context(few_candles, None)
        if result is not None:
            assert result.signal == Signal.HOLD, "Com poucos candles deve retornar HOLD"
            assert result.hold_reason, "hold_reason deve ser não-vazio no aquecimento"
        report.add("Warmup retorna HOLD correto", True)
    except Exception as e:
        report.add("Warmup retorna HOLD correto", False, str(e))

    # ── Verificação 9: compute_with_context com candles completos ─────────────
    candles = _make_candles(250)
    try:
        result = instance.compute_with_context(candles, None)
        assert result is not None, "compute_with_context retornou None"
        assert result.signal in Signal, f"signal inválido: {result.signal}"
        assert isinstance(result.indicators, dict), "indicators deve ser dict"
        report.add("compute_with_context retorna StrategyResult válido", True,
                   f"signal={result.signal.value}")
    except Exception as e:
        report.add("compute_with_context retorna StrategyResult válido", False, str(e))
        report.error = str(e)
        return report

    # ── Verificação 10: "atr" OBRIGATÓRIO em indicators ──────────────────────
    # O bot_manager usa indicators["atr"] para alimentar _current_atr, que é
    # usado para calcular o callback do Trailing Stop dinâmico (SW-TS).
    if "atr" not in result.indicators or not result.indicators.get("atr", 0):
        report.add('indicators["atr"] presente (obrigatório para SW-TS)', False,
                   'Sem "atr" em indicators o Trailing Stop não funciona. '
                   'Calcule: atr_s = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)')
    else:
        report.add('indicators["atr"] presente (obrigatório para SW-TS)', True,
                   f'atr={result.indicators["atr"]:.6f}')

    # ── Verificação 11: "ts_pct" OBRIGATÓRIO em metadata ─────────────────────
    # Padrão de todas as estratégias nativas: ts_pct = atr * ts_mult / close
    metadata = result.metadata or {}
    if "ts_pct" not in metadata:
        report.add('metadata["ts_pct"] presente (padrão trailing stop)', False,
                   'ts_pct ausente. Adicione: "ts_pct": round(atr * self.ts_mult / close, 5)')
    else:
        report.add('metadata["ts_pct"] presente (padrão trailing stop)', True,
                   f'ts_pct={metadata["ts_pct"]:.5f}')

    # ── Verificação 12: campos essenciais de metadata ─────────────────────────
    required_meta = {"sl_price", "tp1_price"}
    missing_meta  = required_meta - set(metadata.keys())
    if missing_meta:
        report.add("metadata contém sl_price e tp1_price", False, f"Ausentes: {missing_meta}")
    else:
        report.add("metadata contém sl_price e tp1_price", True)

    # ── Verificação 13: Indicadores do plano presentes ────────────────────────
    expected_keys = {ind["key"] for ind in plan.get("indicators", [])}
    actual_keys   = set(result.indicators.keys())
    missing_keys  = expected_keys - actual_keys - {"_signal", "_hold_reason", "_criteria_met", "_criteria_total"}
    if missing_keys:
        report.add("Indicadores do plano presentes", False,
                   f"Ausentes: {missing_keys}")
    else:
        report.add("Indicadores do plano presentes", True)

    # ── Verificação 15: criteria_met ≤ criteria_total ─────────────────────────
    cm = getattr(result, "criteria_met", 0)
    ct = getattr(result, "criteria_total", 0)
    
    # Verifica se criteria_total bate com o número de critérios no plano
    plan_criteria_count = len(plan.get("criteria", []))
    if plan_criteria_count > 1 and ct == 1:
        report.add("criteria_total corresponde ao plano", False,
                   f"criteria_total={ct} mas o plano define {plan_criteria_count} critérios. "
                   f"Defina criteria_total={plan_criteria_count} e incremente criteria_met para cada critério satisfeito.")
    elif ct > 0 and cm <= ct:
        report.add("criteria_met ≤ criteria_total", True, f"{cm}/{ct}")
    elif ct == 0:
        report.add("criteria_met ≤ criteria_total", False, "criteria_total=0 — estratégia não expõe critérios")
    else:
        report.add("criteria_met ≤ criteria_total", False, f"{cm} > {ct}")

    # ── Verificação 16: A estratégia aceita diferentes cenários de mercado ─────
    # (não exige sinais direcionais — estratégias conservadoras podem emitir apenas HOLD)
    signals_seen = set()
    try:
        for seed in [42, 7, 123, 999]:
            candles_v = _make_candles(250, seed=seed)
            r = instance.compute_with_context(candles_v, None)
            if r:
                signals_seen.add(r.signal.value)
        # Apenas verifica se não crashou e retornou sinais válidos
        has_valid_signals = all(s in {"hold", "buy", "sell"} for s in signals_seen)
        report.add(
            "Aceita múltiplos cenários de mercado sem crash",
            has_valid_signals,
            f"Sinais observados: {signals_seen}" if signals_seen else "Apenas HOLD nos cenários testados",
        )
    except Exception as e:
        report.add("Aceita múltiplos cenários de mercado sem crash", False, str(e))

    # ── Resultado final ────────────────────────────────────────────────────────
    # Verificações que NÃO impedem aprovação (warnings apenas):
    # - "Aceita múltiplos cenários...": estratégias conservadoras podem emitir só HOLD
    # - "Indicadores do plano presentes": indicadores opcionais do plano de estratégia
    hard_fails = [c for c in report.checks
                  if not c["ok"] and c["name"] not in (
                      "Aceita múltiplos cenários de mercado sem crash",
                      "Indicadores do plano presentes",
                  )]
    report.passed = len(hard_fails) == 0
    return report
