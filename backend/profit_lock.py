"""Trava de lucro em degraus: a cada +LOCK_STEP_PCT alcançado, o stop sobe um degrau (independente do ATR).

Entre a entrada e o TP1 da estratégia não havia proteção de lucro: na TF008 o TP1 fica a ~12 ATR, então um ganho
de +0,9% podia evaporar até o stop inicial (3 ATR abaixo). O stop só sobe, nunca desce, e só em marcos de preço
(não acompanha cada tick), o que evita ser expulso por ruído:

    marco 1 (+0,5%)  -> stop no breakeven LÍQUIDO (cobre taxas e slippage; sair aqui não dá prejuízo)
    marco 2 (+1,0%)  -> stop em +0,5%
    marco 3 (+1,5%)  -> stop em +1,0%   ... e assim por diante

Compartilhado pelo bot (bot/position.py) e pelo backtest (backtest_engine.py) para o estudo medir o que roda ao
vivo. Vale até o TP1; no TP1 o trailing nativo assume, com distância limitada para nunca afrouxar este stop.
`OKX_LOCK_STEP_PCT` (fração; ex.: 0.005 = +0,5% por degrau) liga a regra e `OKX_FEE_RATE` (por lado, padrão 0.001)
ajusta as taxas.

DESLIGADA POR PADRÃO. Backtest da TF008 (OKX spot, taxa 0,1%/lado, slippage 0,05%, saída da trava preenchida no pior
entre stop e abertura), 1D, lucro em US$ por 100 de stake — sem trava -> com trava:
    BTC-USDT 3000 candles:  263 (PF 3,54) -> 25 (+0,5%) | 55 (1%) | 105 (2%) | 161 (4%) | 236 (8%)
    ETH-USDT 3000 candles:  372 (PF 2,31) -> 17 (+0,5%) | 27 (1%) |  84 (2%) | 196 (4%) | 213 (8%)
    SOL-USDT 2223 candles:  543 (PF 4,36) -> 271 (+0,5%) | 215 (1%) | 251 (2%) | 270 (4%) | 272 (8%)
No 4H a trava a +0,5% dá prejuízo nos três ativos. Nenhum degrau testado supera a TF008 original: a estratégia
vive de segurar a tendência até o TP1/sinal de saída, e um stop colado ao preço a expulsa e a faz reentrar
(26 -> 430 trades no BTC 1D), pagando taxas a cada ida e volta. Só ligue após validar a estratégia em questão.
"""
from __future__ import annotations

import math
import os

DEFAULT_STEP_PCT = 0.0          # desligada: ver o backtest acima
DEFAULT_FEE_RATE = 0.001
EXIT_SLIPPAGE_RATE = 0.0005     # mesmo valor do backtest estrito
MIN_STEP_MARGIN = 0.001         # o 1º degrau fica acima do breakeven líquido por pelo menos esta folga


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def fee_rate() -> float:
    return max(0.0, _env_float("OKX_FEE_RATE", DEFAULT_FEE_RATE))


def breakeven_stop(entry: float, direction: int = 1, *, fee: float | None = None,
                   slippage: float = EXIT_SLIPPAGE_RATE) -> float:
    """Preço de stop em que a saída (taxa de entrada + taxa de saída + slippage) resulta em lucro líquido zero."""
    f = fee_rate() if fee is None else fee
    if direction == 1:
        return entry * (1 + f) / ((1 - f) * (1 - slippage))
    return entry * (1 - f) / ((1 + f) * (1 + slippage))


def step_pct(entry: float = 1.0, direction: int = 1) -> float:
    """Tamanho do degrau (fração do preço); 0 = trava desligada. Nunca menor que o breakeven líquido + folga."""
    pct = _env_float("OKX_LOCK_STEP_PCT", DEFAULT_STEP_PCT)
    if pct <= 0:
        return 0.0
    be_pct = abs(breakeven_stop(1.0, direction) - 1.0)
    return max(pct, be_pct + MIN_STEP_MARGIN)


def ratchet_stop(entry: float, extreme: float, direction: int = 1) -> float | None:
    """Stop do degrau mais alto já alcançado, dado o melhor preço visto desde a entrada (`extreme`).

    None = nenhum degrau alcançado (ou trava desligada). O stop do marco 1 é o breakeven líquido; do marco k>=2,
    entry * (1 + (k-1) * step) (para short, espelhado)."""
    step = step_pct(entry, direction)
    if step <= 0 or entry <= 0 or extreme <= 0:
        return None
    gain = (extreme / entry - 1.0) * direction
    k = math.floor(gain / step + 1e-9)
    if k < 1:
        return None
    be = breakeven_stop(entry, direction)
    if k == 1:
        return be
    level = entry * (1 + direction * (k - 1) * step)
    return max(level, be) if direction == 1 else min(level, be)


def stop_is_tighter(candidate: float, current: float, direction: int = 1) -> bool:
    """True se `candidate` protege mais que `current` (o stop só sobe no long / só desce no short)."""
    if current <= 0:
        return candidate > 0
    tol = current * 1e-6
    return candidate > current + tol if direction == 1 else candidate < current - tol


def capped_callback(callback: float, stop_price: float, price: float, entry: float, direction: int = 1) -> float:
    """Limita a distância do trailing nativo (fração do preço) para que ele nunca fique abaixo do stop já travado.

    Só age quando o stop já está no lucro (stop >= entrada no long); antes disso o trailing segue a estratégia."""
    if stop_price <= 0 or price <= 0:
        return callback
    locked = stop_price >= entry if direction == 1 else stop_price <= entry
    if not locked:
        return callback
    room = (1 - stop_price / price) if direction == 1 else (stop_price / price - 1)
    if room <= 0:
        return callback
    return min(callback, max(room, 0.001))


# ── Visão para a tela: degraus alcançados e P&L líquido ──────────────────────────────────────────────────────────

def step_of_stop(entry: float, stop: float, direction: int = 1) -> int:
    """Quantos degraus o stop atual já representa (0 = nenhum). Derivado do stop real, que é persistido e
    sincronizado com a OKX: o card nunca diverge da corretora, nem depois de um reinício."""
    step = step_pct(entry, direction)
    if step <= 0 or entry <= 0 or stop <= 0:
        return 0
    level = (stop / entry - 1.0) * direction
    be_level = abs(breakeven_stop(1.0, direction) - 1.0)
    if level < be_level * (1 - 1e-6):
        return 0
    if level < step - 1e-9:
        return 1
    return math.floor(level / step + 1e-6) + 1


def stop_of_step(entry: float, k: int, direction: int = 1) -> float:
    """Stop correspondente ao degrau k (k>=1)."""
    be = breakeven_stop(entry, direction)
    if k <= 1:
        return be
    level = entry * (1 + direction * (k - 1) * step_pct(entry, direction))
    return max(level, be) if direction == 1 else min(level, be)


def net_unrealized(entry: float, last: float, qty: float, direction: int = 1,
                   *, fee: float | None = None) -> tuple[float, float]:
    """(USD, % sobre o capital investido) LÍQUIDOS de taxa de entrada e de saída, como se fechasse agora."""
    f = fee_rate() if fee is None else fee
    if entry <= 0 or last <= 0 or qty <= 0:
        return 0.0, 0.0
    entry_value = qty * entry
    gross = qty * (last - entry) * direction
    fees = (entry_value + qty * last) * f
    net = gross - fees
    return net, net / entry_value * 100


def net_pct_at_price(entry: float, price: float, direction: int = 1, *, fee: float | None = None) -> float:
    """% líquido (taxas incluídas) de sair a `price`, sobre o capital investido."""
    f = fee_rate() if fee is None else fee
    if entry <= 0 or price <= 0:
        return 0.0
    net = (price - entry) * direction - (entry + price) * f
    return net / entry * 100


def net_pct_at_stop(entry: float, stop: float, direction: int = 1) -> float:
    """% líquido de ser executado no stop: inclui taxas E o slippage de saída (a execução é a mercado)."""
    return net_pct_at_price(entry, stop * (1 - direction * EXIT_SLIPPAGE_RATE), direction)


def steps_view(entry: float, stop: float, direction: int = 1, reached_at: dict | None = None) -> dict:
    """Estrutura exibida no card: cada degrau (trailing stop) alcançado, o stop atual e o próximo gatilho."""
    step = step_pct(entry, direction)
    if step <= 0 or entry <= 0:
        return {"enabled": False, "steps": []}
    reached_at = reached_at or {}
    k = step_of_stop(entry, stop, direction)
    steps = []
    for i in range(1, k + 1):
        sp = stop_of_step(entry, i, direction)
        steps.append({
            "step": i,
            "trigger_pct": round(i * step * 100, 2),
            "stop_price": round(sp, 8),
            "stop_pct_net": round(net_pct_at_stop(entry, sp, direction), 2),
            "reached_at": reached_at.get(i),
        })
    nxt = k + 1
    return {
        "enabled": True,
        "step_pct": round(step * 100, 2),
        "reached": k,
        "steps": steps,
        "next": {
            "step": nxt,
            "trigger_pct": round(nxt * step * 100, 2),
            "trigger_price": round(entry * (1 + direction * nxt * step), 8),
            "stop_price": round(stop_of_step(entry, nxt, direction), 8),
        },
    }
