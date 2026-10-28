"""O espaço de busca do walk-forward só vale se os parâmetros existirem de fato: BaseStrategy.set_params ignora
em silêncio chave desconhecida (hasattr), então um nome errado faria a otimização testar a MESMA configuração
dezenas de vezes sem ninguém perceber."""
import pytest

from backend.optimizer import StrategyOptimizer
from backend.strategies.registry import REGISTRY

SIDS = sorted(s for s in REGISTRY if StrategyOptimizer(s).search_space)


def test_every_tf_strategy_with_a_baseline_has_a_search_space():
    missing = [s for s in REGISTRY if s.startswith("TF") and not StrategyOptimizer(s).search_space]
    assert not missing, f"sem espaço de busca (o walk-forward as pula): {missing}"


@pytest.mark.parametrize("sid", SIDS)
def test_search_space_params_exist_and_change_the_strategy(sid):
    cls = REGISTRY[sid]
    declared = cls.info().params
    space = StrategyOptimizer(sid).search_space
    for name, values in space.items():
        assert name in declared, f"{sid}: '{name}' não é parâmetro da estratégia"
        lo, hi = declared[name].min, declared[name].max
        for v in values:
            if isinstance(v, (int, float)) and lo is not None and hi is not None:
                assert lo <= v <= hi, f"{sid}.{name}={v} fora de [{lo}, {hi}]"
        # a chave tem de chegar ao objeto (set_params ignora o que não existe)
        s = cls()
        s.set_params({name: values[-1]})
        applied = getattr(s, name, None) == values[-1] or (
            isinstance(getattr(s, "params", None), dict) and s.params.get(name) == values[-1])   # atributo ou dict params
        assert applied, f"{sid}: set_params não aplicou '{name}'"
