"""
Rota de verificação de integridade (app vs. OKX).
"""

from __future__ import annotations
from fastapi import APIRouter

from typing import Optional
from datetime import datetime, timedelta, timezone

from fastapi import Depends
from sqlalchemy.orm import Session

from ..database import BotModel, TradeModel, get_db
from ..exchanges.factory import build_exchange
from ..bot_manager import manager

router = APIRouter()


_INTEGRITY_SEVERITY_RANK = {"ok": 0, "warn": 1, "fail": 2, "error": 3}

# OKX /trade/fills só garante retenção recente (poucos dias) — a janela do
# check de P&L da Integridade fica dentro desse limite prático.
_INTEGRITY_PNL_LOOKBACK = timedelta(days=3)

def _norm_sym(sym: str) -> str:
    return (sym or "").upper().replace("/", "").replace("-", "")

def _closest_fill(candidates: list[dict], target_ts: datetime) -> Optional[dict]:
    best, best_delta = None, None
    for f in candidates:
        try:
            f_ts = datetime.fromisoformat(f["transaction_time"].replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            continue
        delta = abs((f_ts - target_ts).total_seconds())
        if best is None or delta < best_delta:
            best, best_delta = f, delta
    return best

@router.get("/api/integrity/check")
async def integrity_check(db: Session = Depends(get_db)):
    """
    Auditoria ao vivo: para cada bot, compara o estado que o app acredita ter
    (posição, tamanho, proteção) contra o estado real na OKX, agora — não uma
    reconciliação de histórico, mas um raio-x do momento presente.
    """
    import aiohttp as _aio

    bots = db.query(BotModel).all()
    results = []

    async with _aio.ClientSession() as session:
        for bot in bots:
            checks: list[dict] = []
            try:
                ex = build_exchange(session, demo=bot.demo)
                status = manager.get_status(bot.id) or {}
                app_direction = int(status.get("direction") or 0)
                app_size      = float(status.get("size") or 0.0)
                app_entry     = float(status.get("entry_price") or 0.0)
                app_open      = app_direction != 0

                pos = await ex.get_position(bot.symbol)
                okx_qty  = abs(float(pos.size or 0.0)) if pos else 0.0
                baseline = abs(float(getattr(bot, "baseline_balance", 0.0) or 0.0))
                net_qty  = max(0.0, okx_qty - baseline)
                okx_open = net_qty > 1e-9

                if app_open == okx_open:
                    checks.append({
                        "label": "Posição", "status": "ok",
                        "detail": f"App {'aberta' if app_open else 'flat'} · "
                                  f"OKX {'aberta' if okx_open else 'flat'} (líquido de baseline).",
                    })
                else:
                    checks.append({
                        "label": "Posição", "status": "fail",
                        "detail": f"App diz {'ABERTA' if app_open else 'FLAT'}, mas OKX está "
                                  f"{'ABERTA' if okx_open else 'FLAT'} (líquido de baseline: {net_qty:.8f}).",
                    })

                # P&L: para cada trade fechado do app na janela, casa o fill de entrada e
                # de saída correspondentes NA OKX (mais próximos no tempo, mesmo símbolo e
                # lado) — não um FIFO agregado sobre todos os fills do símbolo. Isso importa
                # porque o mesmo símbolo pode ter sido usado por um bot antigo (deletado/
                # recriado) dentro da janela de 3 dias; um FIFO cego misturaria os fills
                # daquele bot com os deste, produzindo um P&L "da OKX" sem relação real com
                # este bot (achado real: PA001 puxou 32 fills para 1 round-trip).
                window_start = datetime.now(timezone.utc) - _INTEGRITY_PNL_LOOKBACK
                app_trades_window = db.query(TradeModel).filter(
                    TradeModel.bot_id == bot.id,
                    TradeModel.type == "exit",
                    TradeModel.pnl.isnot(None),
                    TradeModel.timestamp >= window_start.replace(tzinfo=None),
                ).all()
                if app_trades_window:
                    app_pnl_window = sum(t.pnl for t in app_trades_window)
                    sym_norm = _norm_sym(bot.symbol)
                    okx_fills_window = await ex.get_activities(
                        after=window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        until=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    )
                    matching_fills = [f for f in okx_fills_window if _norm_sym(f.get("symbol", "")) == sym_norm]

                    okx_pnl_window = 0.0
                    confirmed = 0
                    for t in app_trades_window:
                        entry_trade = db.query(TradeModel).filter(
                            TradeModel.bot_id == bot.id,
                            TradeModel.type == "entry",
                            TradeModel.exit_price == t.exit_price,
                        ).first()
                        entry_ts = (entry_trade.timestamp if entry_trade else None) or t.timestamp
                        exit_ts = t.closed_at or t.timestamp
                        is_long = (t.direction or "").upper() == "LONG"
                        entry_side = "buy" if is_long else "sell"
                        exit_side = "sell" if is_long else "buy"
                        entry_fill = _closest_fill([f for f in matching_fills if f.get("side") == entry_side], entry_ts)
                        exit_fill = _closest_fill([f for f in matching_fills if f.get("side") == exit_side], exit_ts)
                        if entry_fill and exit_fill:
                            try:
                                e_px, x_px = float(entry_fill["price"]), float(exit_fill["price"])
                                qty = min(float(entry_fill["qty"]), float(exit_fill["qty"]))
                                okx_pnl_window += (x_px - e_px) * qty * (1 if is_long else -1)
                                confirmed += 1
                            except (TypeError, ValueError, KeyError):
                                pass

                    if confirmed == 0:
                        checks.append({
                            "label": "P&L (fills OKX)", "status": "warn",
                            "detail": f"App tem {len(app_trades_window)} trade(s) fechado(s) "
                                      f"(P&L {app_pnl_window:+.2f}) nos últimos 3 dias, mas não achei "
                                      f"fills de entrada+saída correspondentes na OKX para nenhum — não "
                                      f"dá para confirmar (não significa que está errado).",
                        })
                    elif confirmed < len(app_trades_window):
                        checks.append({
                            "label": "P&L (fills OKX)", "status": "warn",
                            "detail": f"App {app_pnl_window:+.2f} ({len(app_trades_window)} trade(s)) · "
                                      f"OKX (fills reais) {okx_pnl_window:+.2f} confirmando apenas "
                                      f"{confirmed}/{len(app_trades_window)} trade(s) — os demais podem "
                                      f"estar fora da retenção curta do /trade/fills, cobertura "
                                      f"incompleta, não necessariamente um erro.",
                        })
                    else:
                        diff = abs(app_pnl_window - okx_pnl_window)
                        tolerance = max(0.05, abs(okx_pnl_window) * 0.02)
                        checks.append({
                            "label": "P&L (fills OKX)",
                            "status": "ok" if diff <= tolerance else "fail",
                            "detail": f"App {app_pnl_window:+.2f} · OKX (fills reais) "
                                      f"{okx_pnl_window:+.2f} (dif. {diff:.2f}) — últimos 3 dias "
                                      f"({confirmed}/{len(app_trades_window)} trade(s) confirmados).",
                        })

                if app_open and okx_open:
                    diff_pct = abs(app_size - net_qty) / net_qty if net_qty > 0 else 1.0
                    checks.append({
                        "label": "Tamanho",
                        "status": "ok" if diff_pct <= 0.01 else "fail",
                        "detail": f"App {app_size:.8f} · OKX {net_qty:.8f} (dif. {diff_pct*100:.2f}%).",
                    })

                    close_side = "sell" if app_direction == 1 else "buy"
                    protective = await ex.find_protective_algo(bot.symbol, close_side)
                    if protective:
                        trigger = protective.get("moveTriggerPx") or protective.get("slTriggerPx") or "?"
                        checks.append({
                            "label": "Proteção (SL/Trailing)", "status": "ok",
                            "detail": f"Ordem ativa na OKX: {protective.get('ordType')} @ {trigger}.",
                        })
                    else:
                        checks.append({
                            "label": "Proteção (SL/Trailing)", "status": "fail",
                            "detail": "Nenhuma ordem de proteção ativa encontrada na OKX — posição desprotegida!",
                        })

                    if app_entry > 0 and pos and pos.avg_price:
                        okx_avg = float(pos.avg_price)
                        entry_diff_pct = abs(app_entry - okx_avg) / okx_avg if okx_avg > 0 else 0.0
                        checks.append({
                            "label": "Preço de entrada",
                            "status": "ok" if entry_diff_pct <= 0.05 else "warn",
                            "detail": f"App {app_entry:.6f} · OKX preço médio {okx_avg:.6f} "
                                      f"(dif. {entry_diff_pct*100:.2f}% — pode incluir holdings pré-existentes).",
                        })

                if status.get("halted"):
                    checks.append({
                        "label": "Estado", "status": "warn",
                        "detail": "Bot está HALTED (circuit breaker ou erro crítico registrado).",
                    })
            except Exception as exc:
                checks.append({"label": "Erro na auditoria", "status": "error", "detail": str(exc)})

            overall = max(
                (c["status"] for c in checks),
                key=lambda s: _INTEGRITY_SEVERITY_RANK.get(s, 0),
                default="ok",
            )
            results.append({
                "bot_id": bot.id,
                "name":   bot.name,
                "symbol": bot.symbol,
                "overall": overall,
                "checks": checks,
            })

    return {
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "bots": results,
    }
