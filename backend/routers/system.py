"""
Rotas de sistema: health, monitor, diagnóstico operacional, Telegram, calendário.
"""

from __future__ import annotations
from fastapi import APIRouter
import logging

import asyncio
import os
from typing import Optional
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import BotModel, OrderRejectionModel, SessionLocal, SignalLogModel, TradeModel, get_db
from ..notifications import TelegramNotifier, build_balance_snapshot_msg
from ..exchanges.factory import build_exchange, get_exchange_provider
from ..feeds.economic_calendar import calendar_feed
from ..bot_manager import manager
from ..common import FIXED_STAKE_USD

router = APIRouter()
log = logging.getLogger("main")


_cached_commit_hash = None

_cached_commit_timestamp = None

@router.get("/api/health")
def health_check():
    global _cached_commit_hash, _cached_commit_timestamp
    if not _cached_commit_hash:
        try:
            with open('.git/HEAD', 'r') as f:
                ref = f.read().strip()
            if ref.startswith('ref: '):
                with open(f".git/{ref[5:]}", 'r') as f:
                    _cached_commit_hash = f.read().strip()[:7]
            else:
                _cached_commit_hash = ref[:7]
                
            with open('.git/logs/HEAD', 'r') as f:
                lines = f.readlines()
                if lines:
                    last_line = lines[-1].strip()
                    parts = last_line.split('\t')[0].split(' ')
                    _cached_commit_timestamp = int(parts[-2])
        except Exception:
            _cached_commit_hash = "unknown"
            _cached_commit_timestamp = None
            
    return {
        "ok": True, 
        "version": _cached_commit_hash,
        "timestamp": _cached_commit_timestamp
    }

@router.head("/api/health")
def health_check_head():
    return None

@router.get("/api/monitor")
async def monitor_bots():
    """Retorna status de todos os bots ativos para monitoramento."""
    from ..bot_manager import manager
    statuses = manager.all_statuses()
    
    # Enriquece com dados do banco
    db = SessionLocal()
    try:
        bots = db.query(BotModel).all()
        bot_map = {b.id: {"name": b.name, "symbol": b.symbol, "timeframe": b.timeframe,
                          "strategy_id": b.strategy_id, "active": b.active, "stake_usd": b.stake_usd,
                          "baseline_balance": b.baseline_balance or 0.0} for b in bots}
    finally:
        db.close()
    
    async def _exchange_positions():
        if not statuses:
            return {}
        try:
            import aiohttp as _aio
            async with _aio.ClientSession() as session:
                ex = build_exchange(session)
                pos_results = await asyncio.gather(
                    *[ex.get_position(bot_map.get(st.get("bot_id"), {}).get("symbol", "")) for st in statuses],
                    return_exceptions=True,
                )
            return {st.get("bot_id"): pos for st, pos in zip(statuses, pos_results)}
        except Exception as exc:
            log.warning("Monitor: falha ao buscar posições OKX: %s", exc)
            return {st.get("bot_id"): exc for st in statuses}

    exchange_pos = await _exchange_positions()

    result = []
    for st in statuses:
        bot_id = st.get("bot_id")
        info = bot_map.get(bot_id, {})
        
        # Calcula PnL em tempo real
        direction = st.get("direction", 0)
        entry = st.get("entry_price", 0)
        last = st.get("last_price", 0)
        pnl_pct = 0.0
        pnl_usd = 0.0
        if direction != 0 and entry > 0 and last > 0:
            # P&L líquido de taxas (calculado pelo robô); o bruto só como último recurso.
            if st.get("unrealized_net_usd") is not None:
                pnl_usd = float(st.get("unrealized_net_usd") or 0.0)
                pnl_pct = float(st.get("unrealized_net_pct") or 0.0)
            else:
                pnl_pct = (last - entry) / entry * direction * 100
                base_qty = float(st.get("size", 0) or 0)
                if base_qty > 0:
                    pnl_usd = base_qty * (last - entry) * direction
                else:
                    pnl_usd = FIXED_STAKE_USD * pnl_pct / 100

        pos = exchange_pos.get(bot_id)
        okx_size = 0.0
        app_notional = 0.0
        okx_notional = 0.0
        okx_direction = "FLAT"
        sync_status = "unknown"
        sync_detail = "OKX ainda não verificada."
        if isinstance(pos, Exception):
            sync_status = "error"
            sync_detail = f"Falha ao consultar OKX: {pos}"
        else:
            okx_size_raw = abs(float(getattr(pos, "size", 0.0) or 0.0)) if pos else 0.0
            baseline = float(info.get("baseline_balance") or 0.0)
            okx_size = max(0.0, okx_size_raw - baseline)  # desconsidera holdings pré-existentes
            okx_raw_direction = "LONG" if pos and getattr(pos, "side", "") == "long" else ("SHORT" if pos else "FLAT")
            okx_direction = okx_raw_direction if okx_size > 1e-9 else "FLAT"
            local_size = abs(float(st.get("size", 0.0) or 0.0))
            notional_price = float(last or 0.0)
            if notional_price <= 0 and pos:
                notional_price = float(getattr(pos, "avg_price", 0.0) or 0.0)
            if notional_price > 0:
                app_notional = local_size * notional_price
                okx_notional = okx_size * notional_price
            entry_notional_price = float(entry or notional_price or 0.0)
            okx_entry_notional = okx_size * entry_notional_price if entry_notional_price > 0 else 0.0
            notional_tolerance = max(2.0, FIXED_STAKE_USD * 0.05)
            local_open = direction != 0
            okx_open = okx_size > 1e-9
            if local_open and okx_open:
                local_direction = "LONG" if direction == 1 else "SHORT"
                if local_direction != okx_direction:
                    sync_status = "divergent"
                    sync_detail = f"Direção divergente: App {local_direction}, OKX {okx_direction}."
                elif local_size > 0 and abs(okx_size - local_size) > max(1e-8, local_size * 0.001):
                    sync_status = "divergent"
                    sync_detail = f"Tamanho divergente: App {local_size:.8f}, OKX {okx_size:.8f}."
                elif okx_entry_notional > 0 and abs(okx_entry_notional - FIXED_STAKE_USD) > notional_tolerance:
                    sync_status = "divergent"
                    sync_detail = (
                        f"Notional de entrada divergente: OKX ${okx_entry_notional:.2f}, "
                        f"esperado ${FIXED_STAKE_USD:.2f} por stake fixo."
                    )
                else:
                    sync_status = "ok"
                    sync_detail = "App e OKX com posição aberta correspondente."
            elif not local_open and not okx_open:
                sync_status = "ok"
                sync_detail = "App e OKX flat."
            elif local_open and not okx_open:
                sync_status = "divergent"
                sync_detail = "App mostra posição aberta, mas OKX está flat."
            else:
                sync_status = "divergent"
                sync_detail = "OKX mostra posição aberta, mas app está flat."
        
        result.append({
            "bot_id": bot_id,
            "name": info.get("name", "—"),
            "symbol": info.get("symbol", "—"),
            "timeframe": info.get("timeframe", "—"),
            "strategy_id": info.get("strategy_id", "—"),
            "active": info.get("active", False),
            "status": st.get("status", "—"),
            "direction": "LONG" if direction == 1 else ("SHORT" if direction == -1 else "FLAT"),
            "entry_price": round(entry, 4) if entry else None,
            "last_price": round(last, 4) if last else None,
            "sl_price": round(st.get("sl_price"), 4) if st.get("sl_price") else None,
            "tp1_price": round(st.get("tp1_price"), 4) if st.get("tp1_price") else None,
            "tp1_done": st.get("tp1_done", False),
            "size": round(float(st.get("size", 0.0) or 0.0), 8),
            "okx_direction": okx_direction,
            "okx_size": round(okx_size, 8),
            "app_notional": round(app_notional, 2),
            "okx_notional": round(okx_notional, 2),
            "okx_entry_notional": round(okx_entry_notional, 2),
            "expected_notional": FIXED_STAKE_USD if direction != 0 else 0.0,
            "sync_status": sync_status,
            "sync_detail": sync_detail,
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usd": round(pnl_usd, 2),
            "guaranteed_pnl": st.get("guaranteed_pnl", 0),
            "profit_lock": st.get("profit_lock"),
            "trailing": st.get("trailing"),
            "daily_pnl": st.get("daily_pnl", 0),
            "wins": st.get("wins", 0),
            "losses": st.get("losses", 0),
            "halted": st.get("halted", False),
            "hold_reason": st.get("hold_reason", ""),
            "last_update": st.get("last_update"),
        })
    
    return {"bots": result, "count": len(result), "timestamp": datetime.now(timezone.utc).isoformat()}

@router.get("/api/system/ai-usage")
def system_ai_usage():
    deepseek_enabled = bool(os.getenv("DEEPSEEK_API_KEY", "").strip())
    provider = get_exchange_provider()
    return {
        "exchange_provider": provider,
        "market_data": {
            "mode": f"{provider}_rest",
            "uses_mcp": False,
            "consumes_ai_tokens": False,
        },
        "actions": {
            "asset_ranking": {
                "consumes_ai_tokens": False,
                "source": "local_market_scoring",
            },
            "strategy_optimization": {
                "consumes_ai_tokens": False,
                "source": "local_statistical_analysis",
            },
            "signal_logs_analysis": {
                "consumes_ai_tokens": False,
                "source": "local_statistical_analysis",
            },
            "bot_ai_analysis": {
                "consumes_ai_tokens": deepseek_enabled,
                "source": "deepseek" if deepseek_enabled else "disabled",
            },
            "graph_interpretation": {
                "consumes_ai_tokens": deepseek_enabled,
                "source": "deepseek" if deepseek_enabled else "rules_fallback",
            },
        },
    }

def _pct(part: int | float, total: int | float) -> float:
    return round((float(part or 0) / float(total or 1)) * 100.0, 2) if total else 0.0

def _diagnostic_verdict(
    *,
    signal_total: int,
    executable_signals: int,
    linked_signals: int,
    rejections: int,
    current_order_ready: bool | None,
    current_order_reason: str,
    ignored_sell_spot: int = 0,
) -> tuple[str, str, str]:
    if signal_total <= 0:
        return (
            "no_data",
            "Sem dados",
            "Nenhum signal_log no período. Verifique se o bot estava ativo e recebendo candles.",
        )
    if rejections > 0:
        return (
            "rejection",
            "Rejeição OKX/proteção",
            "Houve falha ou recusa persistida em order_rejections.",
        )
    if executable_signals <= 0:
        detail = "A estratégia avaliou candles, mas não emitiu BUY/SELL no período."
        if ignored_sell_spot > 0:
            detail = (
                f"A estratégia avaliou candles e emitiu {ignored_sell_spot} SELL, mas em SPOT só há entradas "
                "compradas — sem BUY executável no período."
            )
        return ("market", "Mercado/filtro", detail)
    if linked_signals <= 0:
        detail = current_order_reason or "Houve BUY/SELL sem trade vinculado."
        return ("blocked", "Bloqueio operacional", detail)
    if current_order_ready is False and current_order_reason:
        return ("blocked", "Bloqueio operacional atual", current_order_reason)
    return (
        "operational",
        "Operacional",
        "BUY/SELL gerou trade no período; ausência recente tende a ser seletividade de mercado.",
    )

@router.get("/api/ops/diagnostics")
def ops_diagnostics(days: int = 7, bot_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Diagnóstico agregado para separar falta de mercado, bloqueio operacional e rejeição OKX."""
    days = max(1, min(int(days or 7), 90))
    cutoff = datetime.utcnow() - timedelta(days=days)

    bots_q = db.query(BotModel)
    if bot_id is not None:
        bots_q = bots_q.filter(BotModel.id == bot_id)
    bots = bots_q.order_by(BotModel.active.desc(), BotModel.id.asc()).all()
    bot_ids = [b.id for b in bots]
    if not bot_ids:
        return {
            "period_days": days,
            "since": cutoff.isoformat() + "Z",
            "totals": {
                "bots": 0,
                "signals": 0,
                "executable_signals": 0,
                "linked_signals": 0,
                "trades": 0,
                "rejections": 0,
            },
            "bots": [],
            "top_causes": [],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    signal_rows = db.query(
        SignalLogModel.bot_id,
        SignalLogModel.signal,
        func.count(SignalLogModel.id),
    ).filter(
        SignalLogModel.bot_id.in_(bot_ids),
        SignalLogModel.timestamp >= cutoff,
    ).group_by(SignalLogModel.bot_id, SignalLogModel.signal).all()

    linked_rows = db.query(
        SignalLogModel.bot_id,
        func.count(SignalLogModel.id),
    ).filter(
        SignalLogModel.bot_id.in_(bot_ids),
        SignalLogModel.timestamp >= cutoff,
        SignalLogModel.resulted_in_trade_id.isnot(None),
    ).group_by(SignalLogModel.bot_id).all()

    hold_rows = db.query(
        SignalLogModel.bot_id,
        SignalLogModel.hold_reason,
        func.count(SignalLogModel.id),
    ).filter(
        SignalLogModel.bot_id.in_(bot_ids),
        SignalLogModel.timestamp >= cutoff,
        SignalLogModel.signal == "hold",
        SignalLogModel.hold_reason.isnot(None),
    ).group_by(SignalLogModel.bot_id, SignalLogModel.hold_reason).order_by(
        func.count(SignalLogModel.id).desc()
    ).limit(80).all()

    trade_rows = db.query(
        TradeModel.bot_id,
        TradeModel.type,
        func.count(TradeModel.id),
    ).filter(
        TradeModel.bot_id.in_(bot_ids),
        TradeModel.timestamp >= cutoff,
    ).group_by(TradeModel.bot_id, TradeModel.type).all()

    closed_trade_rows = db.query(
        TradeModel.bot_id,
        func.count(TradeModel.id),
        func.coalesce(func.sum(TradeModel.pnl), 0.0),
    ).filter(
        TradeModel.bot_id.in_(bot_ids),
        TradeModel.timestamp >= cutoff,
        TradeModel.type == "entry",
        TradeModel.exit_price.isnot(None),
    ).group_by(TradeModel.bot_id).all()

    rejection_rows = db.query(
        OrderRejectionModel.bot_id,
        func.count(OrderRejectionModel.id),
    ).filter(
        OrderRejectionModel.bot_id.in_(bot_ids),
        OrderRejectionModel.created_at >= cutoff,
    ).group_by(OrderRejectionModel.bot_id).all()

    rejection_reason_rows = db.query(
        OrderRejectionModel.bot_id,
        OrderRejectionModel.reason,
        func.count(OrderRejectionModel.id),
    ).filter(
        OrderRejectionModel.bot_id.in_(bot_ids),
        OrderRejectionModel.created_at >= cutoff,
        OrderRejectionModel.reason.isnot(None),
    ).group_by(OrderRejectionModel.bot_id, OrderRejectionModel.reason).order_by(
        func.count(OrderRejectionModel.id).desc()
    ).limit(80).all()

    signal_dist: dict[int, dict[str, int]] = {bid: {"hold": 0, "buy": 0, "sell": 0} for bid in bot_ids}
    for bid, signal, count in signal_rows:
        signal_dist.setdefault(bid, {"hold": 0, "buy": 0, "sell": 0})[str(signal or "hold")] = int(count or 0)

    linked_by_bot = {int(bid): int(count or 0) for bid, count in linked_rows}
    rejections_by_bot = {int(bid): int(count or 0) for bid, count in rejection_rows}
    trades_by_bot: dict[int, dict[str, int]] = {bid: {"entry": 0, "exit": 0, "tp1": 0} for bid in bot_ids}
    for bid, typ, count in trade_rows:
        trades_by_bot.setdefault(int(bid), {"entry": 0, "exit": 0, "tp1": 0})[str(typ or "")] = int(count or 0)
    closed_by_bot = {
        int(bid): {"closed": int(count or 0), "pnl": round(float(pnl or 0.0), 4)}
        for bid, count, pnl in closed_trade_rows
    }

    hold_by_bot: dict[int, list[dict]] = {bid: [] for bid in bot_ids}
    for bid, reason, count in hold_rows:
        bucket = hold_by_bot.setdefault(int(bid), [])
        if len(bucket) < 5:
            bucket.append({"reason": str(reason or "HOLD sem motivo"), "count": int(count or 0)})

    rejection_reasons_by_bot: dict[int, list[dict]] = {bid: [] for bid in bot_ids}
    for bid, reason, count in rejection_reason_rows:
        bucket = rejection_reasons_by_bot.setdefault(int(bid), [])
        if len(bucket) < 5:
            bucket.append({"reason": str(reason or "Rejeição sem motivo"), "count": int(count or 0)})

    statuses = {st.get("bot_id"): st for st in manager.all_statuses()}
    cause_counts: dict[str, int] = {}
    bot_reports = []

    for bot in bots:
        dist = signal_dist.get(bot.id, {"hold": 0, "buy": 0, "sell": 0})
        signal_total = int(sum(dist.values()))
        # Em spot só BUY é entrada executável: SELL é inaplicável (ou saída), nunca ordem de entrada.
        sym = (bot.symbol or "").upper()
        is_spot = not (sym.endswith("-SWAP") or sym.endswith("-FUTURES"))
        ignored_sell = int(dist.get("sell", 0)) if is_spot else 0
        executable = int(dist.get("buy", 0) + (0 if is_spot else dist.get("sell", 0)))
        linked = linked_by_bot.get(bot.id, 0)
        rejections = rejections_by_bot.get(bot.id, 0)
        runtime = statuses.get(bot.id) or {}
        order_criteria = runtime.get("order_criteria") or {}
        current_ready = order_criteria.get("ready") if order_criteria else None
        current_reason = order_criteria.get("reason") or runtime.get("hold_reason") or ""
        verdict, label, detail = _diagnostic_verdict(
            signal_total=signal_total,
            executable_signals=executable,
            linked_signals=linked,
            rejections=rejections,
            current_order_ready=current_ready,
            current_order_reason=current_reason,
            ignored_sell_spot=ignored_sell,
        )
        cause_counts[label] = cause_counts.get(label, 0) + 1
        trades = trades_by_bot.get(bot.id, {"entry": 0, "exit": 0, "tp1": 0})
        closed = closed_by_bot.get(bot.id, {"closed": 0, "pnl": 0.0})

        bot_reports.append({
            "bot_id": bot.id,
            "name": bot.name,
            "strategy_id": bot.strategy_id,
            "symbol": bot.symbol,
            "timeframe": bot.timeframe,
            "active": bool(bot.active),
            "runtime_status": runtime.get("status") or ("active" if bot.active else "stopped"),
            "verdict": verdict,
            "verdict_label": label,
            "detail": detail,
            "signals": {
                "total": signal_total,
                "hold": int(dist.get("hold", 0)),
                "buy": int(dist.get("buy", 0)),
                "sell": int(dist.get("sell", 0)),
                "executable": executable,
                "ignored_sell_spot": ignored_sell,
                "linked_to_trade": linked,
                "untraded_executable": max(0, executable - linked),
                "hold_rate": _pct(dist.get("hold", 0), signal_total),
                "trade_conversion_rate": _pct(linked, executable),
            },
            "trades": {
                "entries": int(trades.get("entry", 0)),
                "exits": int(trades.get("exit", 0)),
                "tp1": int(trades.get("tp1", 0)),
                "closed": int(closed.get("closed", 0)),
                "pnl": closed.get("pnl", 0.0),
            },
            "rejections": {
                "count": rejections,
                "top_reasons": rejection_reasons_by_bot.get(bot.id, []),
            },
            "top_hold_reasons": hold_by_bot.get(bot.id, []),
            "current_order_criteria": order_criteria,
        })

    totals = {
        "bots": len(bot_reports),
        "signals": sum(b["signals"]["total"] for b in bot_reports),
        "executable_signals": sum(b["signals"]["executable"] for b in bot_reports),
        "linked_signals": sum(b["signals"]["linked_to_trade"] for b in bot_reports),
        "trades": sum(b["trades"]["entries"] for b in bot_reports),
        "rejections": sum(b["rejections"]["count"] for b in bot_reports),
    }

    return {
        "period_days": days,
        "since": cutoff.isoformat() + "Z",
        "totals": totals,
        "top_causes": [
            {"label": label, "count": count}
            for label, count in sorted(cause_counts.items(), key=lambda item: item[1], reverse=True)
        ],
        "bots": bot_reports,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

@router.get("/api/system/telegram-status")
def telegram_status():
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID",   "").strip()
    return {
        "enabled":         bool(token and chat_id),
        "token_set":       bool(token),
        "chat_id_set":     bool(chat_id),
        "token_preview":   f"{token[:6]}…" if token else None,
        "chat_id_preview": chat_id if chat_id else None,
    }

@router.post("/api/system/telegram-test")
async def telegram_test():
    import aiohttp as _aio

    n = TelegramNotifier()
    if not n.enabled:
        raise HTTPException(400, "Telegram não configurado — defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env do servidor.")
    async with _aio.ClientSession() as session:
        ex = build_exchange(session)
        try:
            account, positions = await asyncio.gather(
                ex.get_account_summary(),
                ex.get_all_positions(),
                return_exceptions=True,
            )
        except Exception as exc:
            raise HTTPException(502, f"Não foi possível obter saldos oficiais: {exc}") from exc
    if isinstance(account, Exception):
        raise HTTPException(502, f"Não foi possível obter saldos oficiais: {account}")
    if isinstance(positions, Exception):
        positions = []
    ok = await n.send(build_balance_snapshot_msg(
        account=account or {},
        positions=positions or [],
        provider=get_exchange_provider().upper(),
    ))
    if not ok:
        raise HTTPException(
            502,
            n.last_error or "Telegram configurado mas falhou ao enviar. Verifique o token e o chat_id.",
        )
    return {"ok": True, "message": "Saldos oficiais enviados com sucesso."}


# ── Rotas: Calendário Económico ───────────────────────────────────────────────
@router.get("/api/calendar/events")
def calendar_events():
    """Próximos eventos High-impact e estado do gate actual."""
    in_window_bollinger, ev_b = calendar_feed.is_high_impact_window("bollinger_breakout")
    in_window_graph,     ev_g = calendar_feed.is_high_impact_window("graph_regime")
    return {
        "upcoming":        calendar_feed.get_upcoming_events(8),
        "latest_surprise": calendar_feed.latest_surprise,
        "gate_active": {
            "bollinger_breakout": {"active": in_window_bollinger, "event": ev_b or None},
            "graph_regime":       {"active": in_window_graph,     "event": ev_g or None},
        },
    }
