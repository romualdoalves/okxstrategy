"""
fee_model.py — As DUAS taxas por lado que o app mostra, sempre lado a lado:

  * REFERÊNCIA (`fee_rate()`): a taxa de uma conta REAL (taker de ordem a mercado). É a que avalia estratégia: estudos, Scanner BT,
    backtests e Meta 1%. Padrão 0,10% (tabela publicada da OKX: BTC-USDT, DOT-USDT etc., regular user, taker). `OKX_FEE_RATE`
    a sobrepõe. Com `OKX_DEMO=false` (conta real) ela passa a ser a taxa cobrada de fato.
  * COBRADA (`charged_rate()`): o que a conta conectada está pagando AGORA, medida nos fills reais (ou a oficial da OKX, ou, sem
    nada medido, a de referência). Na conta demo os fills mostram 0,40% por lado (Order Center, 29–30/10/2026: ex.: 0,3756205
    USDT sobre 93,9051 USDT), 4x a referência: o demo cobra a mais. Vale para o dinheiro da conta: P&L diário, circuito de
    perda, cards, Registros, avisos.

A diferença (`demo_premium`) é exibida, nunca escondida. A decisão de avaliar estratégias pela taxa menor é de quem opera: é a
que a conta real pagaria. Origem de cada número: `status()` e /api/system/fee-model.
"""
from __future__ import annotations

import logging
import os
import time

log = logging.getLogger("fee_model")

REFERENCE_FEE_RATE = 0.001        # 0,10% por lado: taxa taker publicada pela OKX para pares spot de regular user
DEFAULT_FEE_RATE = REFERENCE_FEE_RATE
MIN_FILLS_TO_TRUST = 4            # menos fills que isso não bastam para sobrepor o padrão
REFRESH_SECONDS = 3600

_official: float | None = None           # taker oficial da conta (/account/trade-fee)
_official_maker: float | None = None
_official_level: str = ""
_official_raw: dict | None = None
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


def is_demo() -> bool:
    return os.getenv("OKX_DEMO", "true").lower() in ("1", "true", "yes")


def charged_rate() -> float:
    """O que a conta conectada paga por lado AGORA: medida nos fills > oficial da OKX > referência."""
    if _observed is not None:
        return _observed
    if _official is not None:
        return _official
    return REFERENCE_FEE_RATE


def fee_rate() -> float:
    """Taxa de REFERÊNCIA, a que avalia estratégias (estudos, Scanner, meta). Na conta real é a própria cobrada."""
    env = _env_rate()
    if env is not None:
        return env
    return REFERENCE_FEE_RATE if is_demo() else charged_rate()


def source() -> str:
    """De onde vem a taxa de REFERÊNCIA: 'env' | 'reference' (demo: tabela publicada) | 'official' | 'fills' | 'default' (conta real)."""
    if _env_rate() is not None:
        return "env"
    if is_demo():
        return "reference"
    if _observed is not None:
        return "fills"
    return "official" if _official is not None else "default"


def observed_rate(fills: list[dict]) -> tuple[float | None, int]:
    """(taxa efetiva por lado, nº de fills usados) a partir de fills JÁ reconciliados (backend/fills.py).
    Só ordens taker (o bot opera a mercado) com taxa conhecida; ponderada pelo valor negociado."""
    used = [f for f in fills
            if f.get("fee_known") and f.get("notional", 0) > 0 and f.get("exec_type", "Taker") in ("Taker", "")]
    notional = sum(f["notional"] for f in used)
    if len(used) < MIN_FILLS_TO_TRUST or notional <= 0:
        return None, len(used)
    # `feeRate` gravada pela OKX em cada fill (quando vem) vale mais que a divisão: é a taxa que ela mesma aplicou
    api = []
    for f in used:
        try:
            api.append((abs(float(f.get("fee_rate_api"))), f["notional"]))
        except (TypeError, ValueError):
            break
    if len(api) == len(used):
        return sum(r * n for r, n in api) / notional, len(used)
    return sum(f["fee_usd"] for f in used) / notional, len(used)


def observe(rate: float | None, n_fills: int) -> None:
    """Registra a taxa medida (None ou poucos fills: mantém a anterior)."""
    global _observed, _observed_at, _observed_fills
    if rate is None or n_fills < MIN_FILLS_TO_TRUST or not (0 < rate < 0.02):    # acima de 2% por lado seria dado corrompido
        return
    if _observed is None or abs(rate - _observed) > 1e-6:
        log.info("Taxa efetiva medida nos fills: %.4f%% por lado (%d fills).", rate * 100, n_fills)
    _observed, _observed_at, _observed_fills = rate, time.time(), n_fills


def set_official(taker: float | None, maker: float | None = None, level: str = "", raw: dict | None = None) -> None:
    global _official, _official_maker, _official_level, _official_raw
    _official_raw = raw if raw is not None else _official_raw
    if taker is None or not (0 < taker < 0.02):
        return
    if _official is None or abs(taker - _official) > 1e-9:
        log.info("Taxa oficial da conta (OKX trade-fee): taker %.4f%% / maker %s.", taker * 100,
                 f"{maker * 100:.4f}%" if maker is not None else "?")
    _official, _official_maker, _official_level = taker, maker, level


def status() -> dict:
    mismatch = (_official is not None and _observed is not None and abs(_official - _observed) > 0.0002)
    return {"fee_rate": fee_rate(), "source": source(), "charged_rate": charged_rate(), "demo": is_demo(),
            "demo_premium": round(charged_rate() - fee_rate(), 6), "official_taker": _official, "official_maker": _official_maker,
            "official_level": _official_level or None, "observed": _observed, "observed_fills": _observed_fills,
            "observed_at": _observed_at or None, "default": DEFAULT_FEE_RATE, "official_raw": _official_raw,
            # a OKX diz uma coisa e os fills mostram outra: o número em uso é o oficial, mas a divergência precisa ser vista
            "mismatch": mismatch}


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
            try:
                official = await exchange.get_trade_fee("BTC-USDT")
                set_official(official.get("taker"), official.get("maker"), official.get("level", ""), official.get("raw"))
            except Exception as exc:
                log.warning("Taxa oficial: não consegui perguntar à OKX (%s).", exc)
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
