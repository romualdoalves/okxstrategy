"""
strategy_factory/sandbox.py — Lado do pai: lança o sandbox_runner em subprocesso isolado.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

log = logging.getLogger("strategy_factory.sandbox")

RESULT_MARK = "@@SANDBOX_RESULT@@"                      # mesmo valor de sandbox_runner.RESULT_MARK
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])   # pasta que contém `backend/`
_RUNNER = str(Path(__file__).with_name("sandbox_runner.py"))
# -E ignora PYTHON* do ambiente; -P não põe cwd/script no sys.path. Não usamos -I porque ele
# desliga o user site-packages, onde podem estar dependências (ex.: dateutil do pandas).
_PY_FLAGS = ("-E", "-P") if sys.version_info >= (3, 11) else ("-E",)
TIMEOUT_SECONDS = 60
_MAX_OUTPUT = 2_000_000
_MEMORY_LIMIT_BYTES = 2 * 1024 ** 3


def _child_env() -> dict[str, str]:
    """Ambiente mínimo: nada de segredos (DATABASE_URL, chaves de API, tokens)."""
    keep = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "TEMP", "TMP", "LANG", "LC_ALL",
            "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME")   # p/ localizar o user site-packages
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _limit_memory() -> None:                                # só POSIX
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (_MEMORY_LIMIT_BYTES, _MEMORY_LIMIT_BYTES))


def run_in_sandbox(code: str, plan: dict) -> dict:
    """Retorna {"checks": [...], "error": str, "passed": bool}. Nunca lança por falha do filho."""
    def fail(msg: str) -> dict:
        return {"checks": [{"name": "Execução no sandbox", "ok": False, "detail": msg}],
                "error": msg, "passed": False}

    payload = json.dumps({"code": code, "plan": plan, "root": _PROJECT_ROOT})
    try:
        with tempfile.TemporaryDirectory(prefix="okx_sandbox_") as workdir:
            env = _child_env()
            # numba (usado pelo pandas_ta) grava cache de compilação; mantém tudo na pasta temporária.
            env["NUMBA_CACHE_DIR"] = os.path.join(workdir, "numba_cache")
            proc = subprocess.run(
                [sys.executable, *_PY_FLAGS, _RUNNER],
                input=payload, capture_output=True, text=True, encoding="utf-8",
                timeout=TIMEOUT_SECONDS, cwd=workdir, env=env,
                preexec_fn=_limit_memory if os.name == "posix" else None,
            )
    except subprocess.TimeoutExpired:
        return fail(f"Timeout de {TIMEOUT_SECONDS}s na validação (possível loop infinito).")
    except Exception as e:
        log.exception("Falha ao lançar o sandbox")
        return fail(f"Falha ao iniciar o sandbox: {e}")

    for line in reversed((proc.stdout or "")[-_MAX_OUTPUT:].splitlines()):
        if line.startswith(RESULT_MARK):
            try:
                return json.loads(line[len(RESULT_MARK):])
            except json.JSONDecodeError:
                break
    tail = (proc.stderr or "").strip()[-500:]
    return fail(f"Sandbox terminou sem resultado (código {proc.returncode}). {tail}")
