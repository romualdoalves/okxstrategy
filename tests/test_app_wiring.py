"""Guarda da estrutura do app: rotas esperadas e imports relativos (inclusive os feitos dentro de funções)."""
import ast
import glob
import importlib
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestAppWiring(unittest.TestCase):
    def test_routes_match_fixture(self):
        """Usa o schema OpenAPI (e não app.routes): versões novas do FastAPI guardam routers incluídos
        como objetos opacos, e `app.routes` deixa de listar as rotas individuais."""
        from backend.main import app

        def norm(path):
            return re.sub(r":\w+}", "}", path)      # {symbol:path} -> {symbol}

        actual = {f"{m.upper()} {norm(p)}" for p, methods in app.openapi()["paths"].items() for m in methods}
        actual |= {"WS /ws", "GET /{full_path}"}      # fora do schema (websocket e catch-all da SPA)
        with open(os.path.join(ROOT, "tests", "fixtures", "routes.json"), encoding="utf-8") as f:
            expected = {re.sub(r":\w+}", "}", x) for x in json.load(f)}
        self.assertEqual(sorted(expected - actual), [], "rotas removidas")
        self.assertEqual(sorted(actual - expected), [], "rotas novas: atualize tests/fixtures/routes.json")

    def test_relative_imports_resolve(self):
        """Um `from .x import y` dentro de uma função só quebra em runtime, ao mudar o módulo de pacote."""
        failures = []
        files = [os.path.join(ROOT, "backend", n) for n in ("main.py", "common.py", "lifecycle.py")]
        files += glob.glob(os.path.join(ROOT, "backend", "routers", "*.py"))
        for path in files:
            rel = os.path.relpath(path, ROOT)[:-3].replace(os.sep, ".")
            pkg = rel.rsplit(".", 1)[0]
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for n in ast.walk(tree):
                if isinstance(n, ast.ImportFrom) and n.level:
                    parts = pkg.split(".")
                    base = ".".join(parts[: len(parts) - (n.level - 1)])
                    target = base + ("." + n.module if n.module else "")
                    try:
                        mod = importlib.import_module(target)
                        for a in n.names:
                            if a.name != "*" and not hasattr(mod, a.name):
                                importlib.import_module(f"{target}.{a.name}")
                    except Exception as exc:
                        failures.append(f"{rel}:{n.lineno} {target} ({exc})")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
