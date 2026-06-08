"""
strategy_factory/deployer.py — Etapa 4 da fábrica: hot-load da estratégia.

Escreve o arquivo Python em backend/strategies/factory/ e registra
a classe no REGISTRY em memória — sem reiniciar o servidor.
"""
from __future__ import annotations

import importlib
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("strategy_factory.deployer")

_FACTORY_DIR = Path(__file__).resolve().parent.parent / "strategies" / "factory"


def assign_next_id(db_ids: list[str], prefix: str = "TF") -> str:
    """
    Retorna o próximo ID disponível da série PREFIX001–PREFIX999.
    Prefixos válidos: TF, MR, PA, SC, RG, IF, NW (categorias semânticas).
    db_ids = lista de IDs já usados.
    IDs cujo arquivo foi deletado são reutilizáveis.
    """
    from ..strategies.registry import REGISTRY

    # IDs ativos no registry
    active_ids = set(REGISTRY.keys())

    # IDs do banco que ainda têm arquivo no disco
    existing_file_ids = set()
    for sid in db_ids:
        if (_FACTORY_DIR / f"{sid.lower()}.py").exists():
            existing_file_ids.add(sid)

    # IDs que existem no disco mas não estão no banco (órfãos)
    disk_only_ids = set()
    for fpath in _FACTORY_DIR.glob(f"{prefix.lower()}*.py"):
        sid = fpath.stem.upper()
        if sid not in db_ids:
            disk_only_ids.add(sid)

    used = active_ids | existing_file_ids | disk_only_ids
    log.info("[assign_next_id] prefix=%s active=%s existing=%s disk_orphans=%s used=%s",
             prefix, active_ids, existing_file_ids, disk_only_ids, used)
    
    for n in range(1, 1000):
        candidate = f"{prefix}{n:03d}"
        if candidate not in used:
            log.info("[assign_next_id] Próximo ID disponível: %s", candidate)
            return candidate
    raise RuntimeError(f"Todos os IDs {prefix}001–{prefix}999 estão em uso.")


def deploy(strategy_id: str, code: str, plan: dict) -> str:
    """
    1. Escreve o arquivo Python em strategies/factory/{id_lower}.py
    2. Importa o módulo dinamicamente
    3. Registra no REGISTRY em memória
    Retorna o nome da classe registrada.
    """
    filename = f"{strategy_id.lower()}.py"
    filepath = _FACTORY_DIR / filename

    # Adiciona header de geração e corrige o ID no código
    header = (
        f'"""\nstrategy_factory/{filename} — {plan.get("name", strategy_id)}\n'
        f'Gerado pela Fábrica de Estratégias em {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}\n"""\n'
    )
    final_code = _fix_strategy_id(code, strategy_id, plan)
    if not final_code.startswith('"""') and not final_code.startswith("'''"):
        final_code = header + final_code

    filepath.write_text(final_code, encoding="utf-8")
    log.info("Arquivo escrito: %s", filepath)

    # Hot-load via importlib
    module_name = f"backend.strategies.factory.{strategy_id.lower()}"
    if module_name in sys.modules:
        del sys.modules[module_name]

    try:
        mod = importlib.import_module(
            f".strategies.factory.{strategy_id.lower()}",
            package="backend",
        )
    except ImportError as e:
        filepath.unlink(missing_ok=True)
        raise RuntimeError(f"Falha ao importar estratégia implantada: {e}") from e

    # Encontra a classe que herda de BaseStrategy
    from ..strategies.base import BaseStrategy
    strategy_cls = None
    for attr_name in dir(mod):
        obj = getattr(mod, attr_name)
        try:
            if (isinstance(obj, type) and issubclass(obj, BaseStrategy)
                    and obj is not BaseStrategy):
                strategy_cls = obj
                break
        except TypeError:
            pass

    if strategy_cls is None:
        filepath.unlink(missing_ok=True)
        raise RuntimeError("Módulo implantado não contém classe BaseStrategy válida.")

    # Registra no REGISTRY
    from ..strategies.registry import REGISTRY
    REGISTRY[strategy_id] = strategy_cls
    log.info("Estratégia %s (%s) registrada no REGISTRY.", strategy_id, strategy_cls.__name__)

    return strategy_cls.__name__


def remove(strategy_id: str) -> bool:
    """
    Remove a estratégia do REGISTRY (desativa sem apagar o arquivo).
    Retorna True se removida, False se não estava registrada.
    """
    from ..strategies.registry import REGISTRY
    if strategy_id in REGISTRY:
        del REGISTRY[strategy_id]
        # Remove do sys.modules para forçar reimport se necessário
        module_name = f"backend.strategies.factory.{strategy_id.lower()}"
        sys.modules.pop(module_name, None)
        log.info("Estratégia %s removida do REGISTRY.", strategy_id)
        return True
    return False


def get_factory_file(strategy_id: str) -> str | None:
    """Retorna o conteúdo do arquivo de uma estratégia de fábrica."""
    filepath = _FACTORY_DIR / f"{strategy_id.lower()}.py"
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fix_strategy_id(code: str, strategy_id: str, plan: dict) -> str:
    """Substitui o ID placeholder pelo ID real atribuído."""
    placeholder = plan.get("id", "TF001")
    if placeholder != strategy_id:
        code = code.replace(f'id="{placeholder}"', f'id="{strategy_id}"')
        code = code.replace(f'id=\'{placeholder}\'', f'id=\'{strategy_id}\'')
        # Corrige o nome também
        old_name = plan.get("name", "")
        new_name = old_name.replace(placeholder, strategy_id, 1)
        if old_name and old_name in code:
            code = code.replace(f'name="{old_name}"', f'name="{new_name}"')
    return code
