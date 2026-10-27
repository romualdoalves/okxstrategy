"""A otimização (numpy) da TF004/TF017 não pode mudar nenhum resultado: compara com a saída gravada
ANTES da otimização (tests/fixtures/golden_supertrend.json)."""
import json
import math
import random
from pathlib import Path

import pytest

from backend.exchanges.base import CandleBar
from backend.strategies.registry import REGISTRY

GOLDEN = json.loads((Path(__file__).resolve().parent.parent / "fixtures" / "golden_supertrend.json").read_text())


def _walk(n, step, seed):
    r, p, out = random.Random(seed), 100.0, []
    for i in range(n):
        o, c = p, p * math.exp(r.gauss(0.0002, 0.012))
        out.append(CandleBar(1_600_000_000_000 + i * step, o, max(o, c) * 1.004, min(o, c) * 0.996, c, r.uniform(50, 500)))
        p = c
    return out


def _clean(x):
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in sorted(x.items())}
    return round(x, 8) if isinstance(x, float) else x


@pytest.mark.parametrize("key", sorted(GOLDEN))
def test_matches_pre_optimization_output(key):
    sid, seed, end = key.split("|")
    r = REGISTRY[sid]().compute_with_context(_walk(500, 3_600_000, int(seed))[:int(end)], {})
    got = None if r is None else _clean({"signal": r.signal.value, "ind": r.indicators, "meta": r.metadata,
                                         "hold": r.hold_reason, "met": r.criteria_met, "tot": r.criteria_total})
    assert got == GOLDEN[key]
