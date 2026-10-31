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


def factory_stem(strategy_id: str) -> str:
    """Nome do arquivo (sem .py) em strategies/factory: o ID sem o timeframe, em minúsculas (TF008-1D -> tf008)."""
    return strategy_id.split("-")[0].lower()


def legacy_ids() -> dict[str, str]:
    """ID antigo (sem timeframe) -> ID atual. Desde 2026-10-30 o ID carrega o timeframe para o qual a estratégia foi
    desenhada e validada (TF008 -> TF008-1D); bancos, scripts e links antigos continuam funcionando por este mapa."""
    return {k.split("-")[0]: k for k in REGISTRY if "-" in k}


def resolve_strategy_id(strategy_id: str) -> str:
    """ID canônico: aceita o atual (qualquer caixa) ou o antigo sem timeframe. Desconhecido volta como veio."""
    sid = (strategy_id or "").strip()
    if sid in REGISTRY:
        return sid
    for k in REGISTRY:
        if k.upper() == sid.upper():
            return k
    return legacy_ids().get(sid.upper().split("-")[0], sid) if "-" not in sid else sid


def get_strategy(strategy_id: str) -> BaseStrategy:
    """Instancia a estratégia pelo ID."""
    strategy_id = resolve_strategy_id(strategy_id)
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
