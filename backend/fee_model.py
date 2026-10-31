"""
fee_model.py — A taxa por lado que o app assume em TODO cálculo líquido (cards, trava de lucro, meta, backtest, Scanner).

Uma única fonte, para nenhuma página mostrar um número calculado com taxa diferente da que a corretora cobra:

  1. `OKX_FEE_RATE` (fração por lado), se definida: decisão explícita de quem opera;
  2. a taxa EFETIVA medida nos fills reais da conta (taxa em US$ ÷ valor negociado, só ordens taker), atualizada no startup,
     a cada hora e sempre que a tela Registros concilia os fills;
  3. DEFAULT_FEE_RATE: a taxa observada no Order Center da OKX (fills de 29–30/10/2026: 0,40% por lado, em compra e venda).

O padrão antigo (0,10%) era a tabela pública de VIP 0 e NÃO bate com o que esta conta paga: o líquido saía otimista.
"""
from __future__ import annotations

import logging
import os
import time

log = logging.getLogger("fee_model")

DEFAULT_FEE_RATE = 0.004          # 0,40% por lado: medido nos fills reais da conta (ver docstring)
MIN_FILLS_TO_TRUST = 4            # menos fills que isso não bastam para sobrepor o padrão
REFRESH_SECONDS = 3600

_observed: float | None = None
_observed_at: float = 0.0
_observed_fills: int = 0


def _env_rate() -> float | None:
    raw = os.environ.get("OKX_FEE_RATE")
    if raw in (None, ""):
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def fee_rate() -> float:
    env = _env_rate()
    if env is not None:
        return env
    return _observed if _observed is not None else DEFAULT_FEE_RATE


def source() -> str:
    """De onde vem a taxa em uso: 'env' | 'fills' | 'default'."""
    if _env_rate() is not None:
        return "env"
    return "fills" if _observed is not None else "default"


def observed_rate(fills: list[dict]) -> tuple[float | None, int]:
    """(taxa efetiva por lado, nº de fills usados) a partir de fills JÁ reconciliados (backend/fills.py).
    Só ordens taker (o bot opera a mercado) com taxa conhecida; ponderada pelo valor negociado."""
    used = [f for f in fills
            if f.get("fee_known") and f.get("notional", 0) > 0 and f.get("exec_type", "Taker") in ("Taker", "")]
    notional = sum(f["notional"] for f in used)
    if len(used) < MIN_FILLS_TO_TRUST or notional <= 0:
        return None, len(used)
    return sum(f["fee_usd"] for f in used) / notional, len(used)


def observe(rate: float | None, n_fills: int) -> None:
    """Registra a taxa medida (None ou poucos fills: mantém a anterior)."""
    global _observed, _observed_at, _observed_fills
    if rate is None or n_fills < MIN_FILLS_TO_TRUST or not (0 < rate < 0.02):    # acima de 2% por lado seria dado corrompido
        return
    if _observed is None or abs(rate - _observed) > 1e-6:
        log.info("Taxa efetiva medida nos fills: %.4f%% por lado (%d fills).", rate * 100, n_fills)
    _observed, _observed_at, _observed_fills = rate, time.time(), n_fills


def status() -> dict:
    return {"fee_rate": fee_rate(), "source": source(), "observed": _observed, "observed_fills": _observed_fills,
            "observed_at": _observed_at or None, "default": DEFAULT_FEE_RATE}


async def refresh_from_exchange(days: int = 30) -> dict:
    """Mede a taxa efetiva nos fills dos últimos `days` dias da conta. Falha de rede não derruba nada."""
    import datetime as dt
    import aiohttp
    from .exchanges.factory import build_exchange
    from .fills import reconcile_fills
    try:
        now = dt.datetime.now(dt.timezone.utc)
        async with aiohttp.ClientSession() as sess:
            exchange = build_exchange(sess)
            fills = await exchange.get_activities(
                after=(now - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                until=now.strftime("%Y-%m-%dT%H:%M:%SZ"), strict=True)
        rate, n = observed_rate(reconcile_fills(fills))
        observe(rate, n)
    except Exception as exc:
        log.warning("Taxa efetiva: não consegui medir nos fills (%s); mantenho %.4f%%.", exc, fee_rate() * 100)
    return status()


async def refresh_loop() -> None:
    import asyncio
    while True:
        await refresh_from_exchange()
        await asyncio.sleep(REFRESH_SECONDS)
