"""
strategies/registry.py — Registro central de estratégias disponíveis.

Todas as estratégias são carregadas dinamicamente de backend/strategies/factory/.
Nativas (TF/MR/PA/SC/RG/IF/NW/T) e Factory AI (FX) coexistem no mesmo diretório.
"""

from .base import BaseStrategy

# Mapa: strategy_id → classe
# Populado dinamicamente via _load_factory_strategies()
REGISTRY: dict[str, type[BaseStrategy]] = {}


def _load_factory_strategies() -> None:
    """Carrega todas as estratégias de factory/ no startup."""
    import importlib
    import logging
    from pathlib import Path

    _log = logging.getLogger("strategies.registry")
    factory_dir = Path(__file__).parent / "factory"

    # Padrão: tf001.py, mr001.py, fx001.py, t000.py, etc.
    pattern = re.compile(r"^[a-z]{2}\d{3}\.py$|^t000\.py$")

    for fpath in sorted(factory_dir.glob("*.py")):
        if fpath.name == "__init__.py" or not pattern.match(fpath.name):
            continue

        module_key = f".factory.{fpath.stem}"
        try:
            mod = importlib.import_module(module_key, package=__package__)
            for attr in dir(mod):
                obj = getattr(mod, attr)
                try:
                    if (isinstance(obj, type)
                            and issubclass(obj, BaseStrategy)
                            and obj is not BaseStrategy):
                        info = obj.info()
                        if info.id not in REGISTRY:
                            REGISTRY[info.id] = obj
                            _log.info("Strategy loaded: %s (%s)", info.id, info.name)
                except (TypeError, Exception):
                    pass
        except Exception as exc:
            _log.error("Failed to load strategy %s: %s", fpath.name, exc)


import re  # noqa: E402
_load_factory_strategies()


def get_strategy(strategy_id: str) -> BaseStrategy:
    """Instancia a estratégia pelo ID."""
    cls = REGISTRY.get(strategy_id)
    if cls is None:
        raise ValueError(f"Estratégia desconhecida: {strategy_id!r}. "
                         f"Disponíveis: {list(REGISTRY)}")
    return cls()


def list_strategies() -> list[dict]:
    """Retorna lista de metadados de todas as estratégias."""
    result = []
    for cls in REGISTRY.values():
        info = cls.info()
        result.append({
            "id":                    info.id,
            "name":                  info.name,
            "description":           info.description,
            "tags":                  info.tags,
            "recommended_timeframe": info.recommended_timeframe,
            "recommended_symbol":    info.recommended_symbol,
            "params":      {
                k: {
                    "type":        v.type,
                    "default":     v.default,
                    "min":         v.min,
                    "max":         v.max,
                    "step":        v.step,
                    "description": v.description,
                }
                for k, v in info.params.items()
            },
            "criteria": getattr(info, 'criteria', []),
        })
    return result
