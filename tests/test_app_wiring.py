"""Guarda da estrutura do app: rotas esperadas e imports relativos (inclusive os feitos dentro de funções)."""
import ast
import glob
import importlib
import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestAppWiring(unittest.TestCase):
    def test_routes_match_fixture(self):
        from fastapi.routing import APIRoute
        from starlette.routing import WebSocketRoute
        from backend.main import app

        actual = set()
        for r in app.routes:
            if isinstance(r, APIRoute):
                actual |= {f"{m} {r.path}" for m in r.methods}
            elif isinstance(r, WebSocketRoute):
                actual.add(f"WS {r.path}")
        with open(os.path.join(ROOT, "tests", "fixtures", "routes.json"), encoding="utf-8") as f:
            expected = set(json.load(f))
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
