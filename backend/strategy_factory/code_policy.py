"""
strategy_factory/code_policy.py — Política estática (AST) para código de estratégia gerado por LLM.

Substitui a checagem por substring (facilmente contornável com getattr, concatenação de
strings, etc.). Analisa a árvore sintática: imports em allowlist, nomes e atributos de
I/O/introspecção proibidos e acesso a dunders.

Isto é uma camada de defesa, não um sandbox: denylists de atributos nunca são completas.
A execução do código é isolada à parte (sandbox_runner.py, subprocesso com audit hook).
"""
from __future__ import annotations

import ast
import re

ALLOWED_IMPORT_ROOTS = {
    "__future__", "typing", "math", "re", "dataclasses", "datetime", "zoneinfo",
    "collections", "statistics", "itertools", "functools", "enum", "logging",
    "pandas", "pandas_ta", "numpy", "networkx",
}
# Único módulo interno permitido (absoluto ou relativo `from ..base import ...`).
BASE_MODULES = {"backend.strategies.base", "backend.strategies.indicators"}

FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "open", "input", "breakpoint", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "memoryview", "help", "exit", "quit",
}
ALLOWED_DUNDER_NAMES = {"__name__", "__future__"}
ALLOWED_DUNDER_ATTRS = {"__init__", "__name__", "__class__"}

# Atributos de pandas/numpy/networkx que leem/gravam disco, avaliam strings ou expõem módulos.
FORBIDDEN_ATTRS = {
    "load", "loads", "save", "savez", "savez_compressed", "savetxt", "loadtxt",
    "genfromtxt", "fromfile", "tofile", "memmap", "fromregex",
    "to_csv", "to_pickle", "to_parquet", "to_hdf", "to_sql", "to_excel", "to_feather",
    "to_clipboard", "to_orc", "to_stata", "to_json", "to_html", "to_latex", "to_xml",
    "eval", "query",
    "io", "ctypeslib", "testing", "sys", "os", "subprocess", "builtins", "modules",
    "system", "popen", "importlib", "socket", "shutil", "pathlib", "tempfile", "glob",
    "urllib", "http", "pickle", "ctypes", "threading", "multiprocessing", "asyncio",
}
FORBIDDEN_ATTR_PREFIXES = ("read_", "write_")

_FORMAT_ATTR_RE = re.compile(r"\{[^{}]*__[^{}]*\}")   # "{0.__class__...}".format(x)


def check_code(code: str) -> list[str]:
    """Retorna a lista de violações (vazia = código aceito pela política)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"Sintaxe inválida: {e}"]

    issues: list[str] = []

    # setattr(self, nome, valor) é o padrão de set_params() das estratégias; só ele é aceito.
    allowed_setattr = {
        id(n.func) for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "setattr"
        and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id == "self"
    }

    def add(node: ast.AST, msg: str) -> None:
        issues.append(f"linha {getattr(node, 'lineno', '?')}: {msg}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORT_ROOTS:
                    add(node, f"import não permitido: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:                                   # from ..base import X
                if not (node.level == 2 and mod == "base"):
                    add(node, f"import relativo não permitido: {'.' * node.level}{mod}")
            elif mod.split(".")[0] in ALLOWED_IMPORT_ROOTS:
                pass
            elif mod not in BASE_MODULES:
                add(node, f"import não permitido: {mod}")
            if any(a.name == "*" for a in node.names):
                add(node, "import * não permitido")
        elif isinstance(node, ast.Name):
            if id(node) in allowed_setattr:
                continue
            if node.id in FORBIDDEN_NAMES:
                add(node, f"uso proibido de '{node.id}'")
            elif node.id.startswith("__") and node.id not in ALLOWED_DUNDER_NAMES:
                add(node, f"nome especial proibido: {node.id}")
        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr.startswith("__") and attr.endswith("__"):
                if attr not in ALLOWED_DUNDER_ATTRS:
                    add(node, f"acesso a atributo especial proibido: {attr}")
            elif attr in FORBIDDEN_ATTRS or attr.startswith(FORBIDDEN_ATTR_PREFIXES):
                add(node, f"atributo proibido: .{attr}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _FORMAT_ATTR_RE.search(node.value):
                add(node, "string de formatação com acesso a atributos especiais")

    return issues
