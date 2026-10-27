"""
strategy_factory/sandbox_runner.py — Executa a validação dinâmica em subprocesso isolado.

Uso (pelo pai): `python -E -P sandbox_runner.py` com JSON {"code", "plan", "root"} no stdin.
O filho:
  1. importa o necessário (pandas, base, validador) com permissões normais;
  2. instala um audit hook que bloqueia rede, subprocessos, escrita em disco fora da pasta
     temporária do próprio sandbox, leitura fora de sys.prefix/projeto e dlopen de libs nativas;
  3. executa o código gerado sobre candles sintéticos e devolve o relatório em JSON.

Ambiente do filho é mínimo (sem DATABASE_URL, chaves de API, tokens), cwd é um diretório
temporário e há timeout no pai. Limitações: sem limite de memória no Windows; audit hooks
são best-effort contra código malicioso decidido a escapar (use revisão humana também).
"""
from __future__ import annotations

import json
import os
import sys

RESULT_MARK = "@@SANDBOX_RESULT@@"

# Nunca permitidos.
_DENY_PREFIXES = (
    "socket.", "subprocess.", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
    "os.fork", "os.forkpty", "os.chown", "os.startfile", "shutil.", "ctypes.dlopen",
    "winreg.", "webbrowser.", "urllib.Request", "http.client.", "ftplib.", "smtplib.",
    "telnetlib.", "msvcrt.", "pty.", "sqlite3.connect",
)
# Permitidos só dentro da pasta temporária do sandbox (ex.: cache de compilação do numba,
# que o pandas_ta grava no primeiro uso). Qualquer caminho fora dela é bloqueado.
_CONFINED_PREFIXES = (
    "os.remove", "os.unlink", "os.rename", "os.replace", "os.rmdir", "os.mkdir",
    "os.chmod", "os.truncate", "os.symlink", "os.link",
)


def _install_audit_hook(read_roots: list[str], write_root: str) -> None:
    def norm(p) -> str:
        return os.path.normcase(os.path.realpath(os.fsdecode(p)))

    roots = [norm(r) for r in read_roots if r]
    wroot = norm(write_root)

    def inside(p: str, base: str) -> bool:
        return p == base or p.startswith(base + os.sep)

    def hook(event: str, args: tuple) -> None:
        if event.startswith(_DENY_PREFIXES):
            raise PermissionError(f"sandbox: operação bloqueada ({event})")
        if event.startswith(_CONFINED_PREFIXES):
            for a in args:
                if isinstance(a, (str, bytes, os.PathLike)) and not inside(norm(a), wroot):
                    raise PermissionError(f"sandbox: {event} fora da pasta temporária ({a})")
            return
        if event == "open":
            path, mode = args[0], args[1] or "r"
            if not isinstance(path, (str, bytes, os.PathLike)):
                return                                   # file descriptor já aberto
            p = norm(path)
            if any(c in str(mode) for c in "wax+"):
                if not inside(p, wroot):
                    raise PermissionError(f"sandbox: escrita bloqueada ({path})")
            elif not (inside(p, wroot) or any(inside(p, r) for r in roots)):
                raise PermissionError(f"sandbox: leitura fora da área permitida ({path})")

    sys.addaudithook(hook)


def main() -> None:
    payload = json.loads(sys.stdin.read())
    code, plan, root = payload["code"], payload["plan"], payload["root"]

    real_stdout = sys.stdout
    sys.stdout = sys.stderr                              # prints da estratégia não poluem o protocolo
    sys.path.insert(0, root)

    # Imports pesados antes do hook (evita falsos positivos de leitura/import tardio).
    import numpy, pandas, pandas_ta, networkx            # noqa: F401,E401
    from backend.strategy_factory import validator
    from backend.strategies import base                  # noqa: F401

    _install_audit_hook([sys.prefix, sys.base_prefix, sys.exec_prefix, root], write_root=os.getcwd())

    try:
        report = validator.run_dynamic_checks(code, plan)
        out = {"checks": report.checks, "error": report.error, "passed": report.passed}
    except BaseException as e:                           # inclui PermissionError do sandbox
        out = {"checks": [{"name": "Execução no sandbox", "ok": False, "detail": f"{type(e).__name__}: {e}"}],
               "error": f"Erro no sandbox: {type(e).__name__}: {e}", "passed": False}

    real_stdout.write(RESULT_MARK + json.dumps(out) + "\n")
    real_stdout.flush()


if __name__ == "__main__":
    main()
