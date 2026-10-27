"""
backfill_history.py — Completa o histórico de candles no banco (historic_candles), baixando só o que falta.

Usa o mesmo repositório do app (candle_store.ensure_history): atualiza o fim da série e, se o histórico
guardado for menor que o alvo, busca para trás a partir do candle mais antigo guardado. Rodar de novo não
baixa nada que já esteja no banco.

Uso (a partir da pasta do projeto):
  python -m backend.backfill_history --symbols BTC-USDT,ETH-USDT --timeframes 4h,1D
  python -m backend.backfill_history --symbols BTC-USDT --timeframes 1h --target 40000
  python -m backend.backfill_history --tracked          # todos os ativos/timeframes rastreados no banco
Na VPS:
  docker exec okx_strategy python -m backend.backfill_history --tracked
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

try:   # o .env precisa ser carregado ANTES de importar o banco (lê DATABASE_URL no import)
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
except ImportError:
    pass

from .candle_store import ensure_history, history_target  # noqa: E402
from .exchanges.factory import map_timeframe_for_history  # noqa: E402

ALLOWED = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "12h", "1D", "1W"}
_CASE = {tf.lower(): tf for tf in ALLOWED}


def normalize_timeframe(tf: str) -> str:
    key = (tf or "").strip().lower()
    if key not in _CASE:
        raise ValueError(f"timeframe inválido: {tf!r} (use um de {sorted(ALLOWED)})")
    return _CASE[key]


async def backfill(db, ex, pairs: list[tuple[str, str]], target: int | None = None, log=print) -> list[dict]:
    """Completa cada (símbolo, timeframe). `ex` precisa de fetch_candles/fetch_history_candles."""
    results = []
    for symbol, tf in pairs:
        t0 = time.time()
        r = await ensure_history(db, ex, symbol, tf, map_timeframe_for_history(tf), target=target)
        r.update(symbol=symbol, timeframe=tf, secs=round(time.time() - t0, 1))
        results.append(r)
        log(f"  {symbol:12} {tf:>4}: +{r['added']:6d} candles | total {r['count']:6d}"
            f"{' (histórico da OKX esgotado)' if r['exhausted'] else ''} | {r['secs']}s")
    return results


def _pairs_from_args(args, db) -> list[tuple[str, str]]:
    if args.tracked:
        from .database import TrackedSymbolModel
        rows = db.query(TrackedSymbolModel).filter(TrackedSymbolModel.is_active == True).all()  # noqa: E712
        return [(r.symbol, normalize_timeframe(tf)) for r in rows for tf in (r.timeframes or [])]
    symbols = [s.strip().upper() for s in (args.symbols or "").split(",") if s.strip()]
    tfs = [normalize_timeframe(t) for t in (args.timeframes or "").split(",") if t.strip()]
    return [(s, t) for s in symbols for t in tfs]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Completa o histórico de candles no banco.")
    ap.add_argument("--symbols", help="lista separada por vírgula, ex.: BTC-USDT,ETH-USDT")
    ap.add_argument("--timeframes", help="lista separada por vírgula, ex.: 4h,1D")
    ap.add_argument("--tracked", action="store_true", help="usa os ativos/timeframes rastreados no banco")
    ap.add_argument("--target", type=int, help="candles por série (padrão: alvo de cada timeframe)")
    args = ap.parse_args(argv)
    if not args.tracked and not (args.symbols and args.timeframes):
        ap.error("informe --tracked, ou --symbols e --timeframes")

    from .database import SessionLocal, create_tables
    from .exchanges.okx import OKXExchange

    create_tables()
    with SessionLocal() as db:
        pairs = _pairs_from_args(args, db)
        if not pairs:
            print("nada para fazer (nenhum par símbolo/timeframe).")
            return 0
        print(f"{len(pairs)} série(s); alvo: {args.target or 'padrão por timeframe '}"
              f"({', '.join(sorted({f'{t}={history_target(t)}' for _, t in pairs}))})")
        asyncio.run(backfill(db, OKXExchange(), pairs, target=args.target))
    print("concluído.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
