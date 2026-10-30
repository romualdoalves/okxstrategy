"""Versão do código em execução (hash e data do commit), lida do .git copiado para a imagem no build.

Usada por /api/health e pelo index.html servido (meta `app-version`): a aba compara a versão com que foi carregada
com a do servidor para avisar quando está desatualizada depois de um deploy.
"""
from __future__ import annotations

_cache: dict | None = None


def get_commit_info() -> dict:
    """{"version": hash curto ou "unknown", "timestamp": epoch do commit ou None}. Lido uma vez por processo."""
    global _cache
    if _cache is not None:
        return _cache
    version, timestamp = "unknown", None
    try:
        with open(".git/HEAD", "r") as f:
            ref = f.read().strip()
        if ref.startswith("ref: "):
            with open(f".git/{ref[5:]}", "r") as f:
                version = f.read().strip()[:7]
        else:
            version = ref[:7]
        with open(".git/logs/HEAD", "r") as f:
            lines = f.readlines()
        if lines:
            timestamp = int(lines[-1].strip().split("\t")[0].split(" ")[-2])
    except Exception:
        version, timestamp = "unknown", None
    _cache = {"version": version, "timestamp": timestamp}
    return _cache
