"""
Rotas de trades: listagem, taxas, resumo, relatório e atividades.
"""

from __future__ import annotations
from fastapi import APIRouter
import logging

import asyncio
import os
from typing import Optional

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import BotModel, SignalLogModel, TradeModel, TradeReportModel, get_db
from ..exchanges.factory import build_exchange
from ..common import _TZ_OFFSET, _local_date_to_utc_range
from .integrity import _closest_fill, _norm_sym

router = APIRouter()
log = logging.getLogger("main")


@router.get("/api/activities")
async def get_activities(date: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Retorna os fills da exchange ativa para a data informada (padrão: hoje),
    correlacionados com os bots do sistema e trades internos.
    """
    import aiohttp as _aio
    from datetime import date as _date_cls
    import datetime as _dt

    # Usa data local (não UTC) como padrão para target_date
    if date:
        target_date = date
    else:
        _now_utc = _dt.datetime.utcnow()
        _now_local = _now_utc + _dt.timedelta(hours=_TZ_OFFSET)
        target_date = _now_local.strftime("%Y-%m-%d")

    async with _aio.ClientSession() as _act_sess:
        exchange = build_exchange(_act_sess)

    # 1. Descobre se há posições abertas em dias anteriores (para FIFO cross-day)
    bots_all = db.query(BotModel).all()
    open_entry = db.query(TradeModel).filter(
        TradeModel.exit_price.is_(None),
        TradeModel.type == "entry",
    ).order_by(TradeModel.timestamp.asc()).first()

    earliest_date: str | None = None
    if open_entry and open_entry.timestamp:
        d = open_entry.timestamp.date()
        if d.isoformat() < target_date:
            earliest_date = d.isoformat()

    # UTC boundaries para target_date (considerando fuso local)
    _utc_start, _utc_end = _local_date_to_utc_range(target_date)

    # 2. Dados da exchange em paralelo — fills com range se cross-day necessário
    def _fills_fetch():
        if earliest_date:
            # Pega desde o início do dia mais antigo até o fim do dia local atual
            _, _cross_end = _local_date_to_utc_range(target_date)
            return exchange.get_activities(after=earliest_date + "T00:00:00Z", until=_cross_end)
        return exchange.get_activities(after=_utc_start, until=_utc_end)

    try:
        all_fills, account, port_history, open_orders = await asyncio.gather(
            _fills_fetch(),
            exchange.get_account_summary(),
            exchange.get_portfolio_history("1D", "1H"),
            exchange.get_open_orders(),
        )
    except Exception as exc:
        raise HTTPException(502, f"Erro ao contactar exchange: {exc}")

    # 3. Mapa de bots por símbolo normalizado
    def _norm(sym: str) -> str:
        return sym.upper().replace("/", "").replace("-", "")

    bot_by_symbol: dict[str, list] = {}
    for b in bots_all:
        key = _norm(b.symbol)
        bot_by_symbol.setdefault(key, []).append({"id": b.id, "name": b.name, "strategy_id": b.strategy_id})

    # 4. Trades internos do dia para reconciliação
    day_start = _dt.datetime.fromisoformat(_utc_start.replace("Z", ""))
    day_end   = _dt.datetime.fromisoformat(_utc_end.replace("Z", ""))
    db_trades = db.query(TradeModel).filter(
        TradeModel.timestamp >= day_start,
        TradeModel.timestamp <  day_end,
    ).all()

    db_trade_list = [
        {
            "id": t.id, "bot_id": t.bot_id, "type": t.type, "direction": t.direction,
            "symbol": t.symbol, "size": t.size,
            "entry_price": t.entry_price, "exit_price": t.exit_price,
            "pnl": t.pnl,
            "timestamp": t.timestamp.isoformat() + "Z" if t.timestamp else None,
            "closed_at": t.closed_at.isoformat() + "Z" if t.closed_at else None,
            "event": t.event,
        }
        for t in db_trades
    ]

    # 5. FIFO P&L sobre TODOS os fills (inclui cross-day se earliest_date foi usado).
    #    Exibição: apenas fills de target_date; P&L totalizado só para fills de hoje.
    from collections import defaultdict, deque
    buy_queues: dict[str, deque] = defaultdict(deque)
    enriched_fills = []
    total_realized_from_fills = 0.0

    for f in all_fills:
        sym_raw   = f.get("symbol", "")
        sym_norm  = _norm(sym_raw)
        side      = f.get("side", "")
        try:
            price = float(f.get("price", 0))
            qty   = float(f.get("qty", 0))
        except (TypeError, ValueError):
            price = qty = 0.0

        matched_bots = bot_by_symbol.get(sym_norm, [])

        fill_pnl = None
        if side == "buy":
            buy_queues[sym_norm].append({"price": price, "qty": qty})
        elif side == "sell" and buy_queues[sym_norm]:
            entry = buy_queues[sym_norm].popleft()
            matched_qty = min(entry["qty"], qty)
            fill_pnl = round((price - entry["price"]) * matched_qty, 4)

        # Determina data LOCAL do fill para filtrar exibição e totalização do dia
        try:
            fill_ts = _dt.datetime.fromisoformat(
                f.get("transaction_time", "").replace("Z", "+00:00")
            ).replace(tzinfo=None)
            fill_local_date = (fill_ts + _dt.timedelta(hours=_TZ_OFFSET)).strftime("%Y-%m-%d")
        except Exception:
            fill_ts = None
            fill_local_date = f.get("transaction_time", "")[:10]

        is_today = fill_local_date == target_date
        if is_today and fill_pnl is not None:
            total_realized_from_fills += fill_pnl

        # Só inclui fills de target_date na resposta (cross-day fills servem apenas para FIFO)
        if not is_today:
            continue

        matched_db_trade = None
        if fill_ts:
            for t in db_trades:
                if t.symbol and _norm(t.symbol) == sym_norm:
                    ref_ts = t.closed_at or t.timestamp
                    if ref_ts and abs((ref_ts - fill_ts).total_seconds()) <= 300:
                        matched_db_trade = {"id": t.id, "type": t.type, "pnl": t.pnl}
                        break

        # Taxa real da OKX, já incluída no próprio fill (campo `fee`, tipicamente negativo).
        try:
            fee_usd = abs(float(f.get("fee") or 0))
        except (TypeError, ValueError):
            fee_usd = 0.0
        net_pnl = round(fill_pnl - fee_usd, 4) if fill_pnl is not None else None

        enriched_fills.append({
            **f,
            "symbol_norm": sym_norm,
            "price":       price,
            "qty":         qty,
            "fill_pnl":    fill_pnl,
            "fee_usd":     fee_usd,
            "net_pnl":     net_pnl,
            "matched_bots": matched_bots,
            "matched_db_trade": matched_db_trade,
        })

    # 6. Resumo da conta
    equity      = float(account.get("equity", 0))
    last_equity = float(account.get("last_equity", equity))
    cash        = float(account.get("cash", 0))
    unreal_pl   = float(account.get("unrealized_pl") or 0)
    day_pl      = equity - last_equity

    # P&L app (apenas exits do dia)
    app_pnl_today = sum(t.pnl for t in db_trades if t.type == "exit" and t.pnl is not None)

    total_fees = round(sum(f.get("fee_usd", 0.0) for f in enriched_fills), 4)
    net_pnl_after_fees = round(total_realized_from_fills - total_fees, 4)

    # Portfólio history — último ponto do dia
    ph_timestamps = port_history.get("timestamp", [])
    ph_equity     = port_history.get("equity",    [])
    ph_pl         = port_history.get("profit_loss", [])
    history_points = [
        {"ts": ts, "equity": eq, "pl": pl}
        for ts, eq, pl in zip(ph_timestamps, ph_equity, ph_pl)
        if eq is not None
    ]

    return {
        "date": target_date,
        "account": {
            "equity":       round(equity, 2),
            "cash":         round(cash, 2),
            "last_equity":  round(last_equity, 2),
            "day_pl":       round(day_pl, 2),
            "unrealized_pl": round(unreal_pl, 2),
        },
        "reconciliation": {
            "exchange_realized_pl": round(total_realized_from_fills, 2),
            "total_fees":          round(total_fees, 4),
            "net_pnl_after_fees":  round(net_pnl_after_fees, 2),
            "app_pnl_today":       round(app_pnl_today, 2),
            "discrepancy":         round(total_realized_from_fills - app_pnl_today, 2),
            "fills_count":         len(enriched_fills),
            "db_trades_count":     len(db_trades),
            "cross_day_fifo":      earliest_date is not None,
            "fifo_since":          earliest_date,
        },
        "fills":       enriched_fills,
        "db_trades":   db_trade_list,
        "history":     history_points,
        "open_orders": open_orders,
    }


# ── Rotas: Trades ─────────────────────────────────────────────────────────────
@router.get("/api/trades")
def list_trades(bot_id: Optional[int] = None,
                limit: int = 200,
                db: Session = Depends(get_db)):
    # Retorna apenas registros de SAÍDA (exit/tp1) para o histórico visual
    # O registro de entrada fica no banco apenas para o summary calcular estatísticas
    q = db.query(TradeModel).filter(TradeModel.type.in_(["exit", "tp1"]))
    if bot_id:
        q = q.filter(TradeModel.bot_id == bot_id)
    trades = q.order_by(TradeModel.timestamp.desc()).limit(limit).all()
    # Build a bot-id → name map in one query to avoid N+1
    bot_ids = {t.bot_id for t in trades}
    bot_names = {b.id: b.name for b in db.query(BotModel).filter(BotModel.id.in_(bot_ids)).all()}
    def _capture_pct(t):
        """% do movimento favorável máximo (entrada → pico) que foi realmente capturado
        na saída — a métrica de efetividade do trailing/SL. None quando não aplicável
        (peak desconhecido, ou o preço nunca chegou a ficar favorável antes de sair)."""
        if t.type != "exit" or not (t.entry_price and t.exit_price and t.peak_price):
            return None
        mult = 1 if (t.direction or "").upper() == "LONG" else -1 if (t.direction or "").upper() == "SHORT" else None
        if mult is None:
            return None
        max_favorable = (t.peak_price - t.entry_price) * mult
        if max_favorable <= 1e-9:
            return None
        captured = (t.exit_price - t.entry_price) * mult
        return round(max(0.0, min(1.0, captured / max_favorable)) * 100, 1)

    def _serialize(t):
        fee = t.fee  # None = ainda não sincronizado
        net = round(t.pnl - fee, 2) if (t.pnl is not None and fee is not None) else None
        return {
            "id": t.id, "bot_id": t.bot_id, "bot_name": bot_names.get(t.bot_id, f"Bot {t.bot_id}"),
            "type": t.type, "event": t.event, "direction": t.direction, "symbol": t.symbol,
            "size": t.size, "entry_price": t.entry_price, "exit_price": t.exit_price,
            "sl_price": t.sl_price, "tp1_price": t.tp1_price, "atr": t.atr,
            "peak_price": t.peak_price, "capture_pct": _capture_pct(t),
            "pnl": t.pnl, "fee": fee, "net_pnl": net,
            "daily_pnl": t.daily_pnl, "source": t.source,
            "timestamp": t.timestamp.isoformat() + "Z" if t.timestamp else None,
            "closed_at": t.closed_at.isoformat() + "Z" if t.closed_at else None,
        }
    return [_serialize(t) for t in trades]

async def _sync_trade_fees_impl(db: Session, days_back: int = 30) -> dict:
    """
    Sincroniza taxas de corretagem REAIS da OKX para trades registrados.
    Para cada trade pendente, busca os fills de entrada e saída na OKX
    (GET /trade/fills) na janela de tempo do trade e soma as taxas reais
    (campo `fee` de cada fill) — não estima um percentual fixo.
    """
    from datetime import datetime, timedelta
    import aiohttp as _aio

    cutoff = datetime.utcnow() - timedelta(days=days_back)
    pending = db.query(TradeModel).filter(
        TradeModel.type == "exit",
        TradeModel.fee.is_(None),
        TradeModel.timestamp >= cutoff,
    ).all()

    if not pending:
        return {"updated": 0, "skipped": False, "reason": "Nenhum trade pendente de sincronização."}

    # _norm_sym / _closest_fill definidos mais abaixo neste módulo (compartilhados com
    # /api/integrity/check) — resolvidos em tempo de execução, não de definição.
    updated = 0
    skipped_no_fill = 0

    async with _aio.ClientSession() as sess:
        exchange = build_exchange(sess)
        for trade in pending:
            entry_trade = db.query(TradeModel).filter(
                TradeModel.bot_id == trade.bot_id,
                TradeModel.type == "entry",
                TradeModel.exit_price == trade.exit_price,
            ).first()
            entry_ts = (entry_trade.timestamp if entry_trade else None) or trade.timestamp
            exit_ts  = trade.closed_at or trade.timestamp
            if not entry_ts or not exit_ts:
                skipped_no_fill += 1
                continue

            window_start = (entry_ts - timedelta(minutes=10)).isoformat() + "Z"
            window_end   = (exit_ts + timedelta(minutes=10)).isoformat() + "Z"
            try:
                fills = await exchange.get_activities(after=window_start, until=window_end)
            except Exception as exc:
                log.warning("[sync-fees] Falha ao buscar fills da OKX para trade %d: %s", trade.id, exc)
                skipped_no_fill += 1
                continue

            sym_norm = _norm_sym(trade.symbol)
            matching = [f for f in fills if _norm_sym(f.get("symbol", "")) == sym_norm]
            if not matching:
                skipped_no_fill += 1
                continue

            # trade.direction é gravado como "LONG"/"SHORT" (maiúsculo) por _save_trade —
            # comparar em minúsculo aqui sempre falhava e invertia compra/venda.
            is_long = (trade.direction or "").upper() == "LONG"
            entry_side = "buy" if is_long else "sell"
            exit_side  = "sell" if is_long else "buy"
            entry_fill = _closest_fill([f for f in matching if f.get("side") == entry_side], entry_ts)
            exit_fill  = _closest_fill([f for f in matching if f.get("side") == exit_side], exit_ts)

            total_fee = 0.0
            found_any = False
            for fill in (entry_fill, exit_fill):
                if fill and fill.get("fee") not in (None, ""):
                    try:
                        total_fee += abs(float(fill["fee"]))
                        found_any = True
                    except (TypeError, ValueError):
                        pass

            if not found_any:
                skipped_no_fill += 1
                continue

            trade.fee = round(total_fee, 6)
            if entry_trade and entry_trade.fee is None:
                entry_trade.fee = trade.fee
            updated += 1

    db.commit()
    return {
        "updated": updated,
        "skipped_no_fill": skipped_no_fill,
        "skipped": False,
        "reason": f"Taxas reais sincronizadas da OKX para {updated} trade(s) ({skipped_no_fill} sem fill correspondente).",
    }

@router.post("/api/trades/sync-fees")
async def sync_trade_fees(days_back: int = 30, db: Session = Depends(get_db)):
    """
    Dispara manualmente a sincronização de taxas de corretagem.
    Preenche o campo `fee` nos trades sem valor registrado.
    """
    result = await _sync_trade_fees_impl(db, days_back=days_back)
    return result

@router.get("/api/trades/summary")
def trades_summary(bot_id: Optional[int] = None,
                   db: Session = Depends(get_db)):
    # Usa trades de ENTRADA fechados (type="entry" + exit_price preenchido) para evitar
    # duplicação, pois cada round-trip gera registros entry + tp1 + exit no banco.
    # O campo pnl e fee ficam no entry record.
    q = db.query(TradeModel).filter(
        TradeModel.type == "entry",
        TradeModel.exit_price.isnot(None),
        TradeModel.pnl.isnot(None),
    )
    if bot_id:
        q = q.filter(TradeModel.bot_id == bot_id)
    trades = q.all()
    if not trades:
        return {"total_trades": 0, "total_pnl": 0, "win_rate": 0,
                "total_fees": 0, "net_pnl": 0, "fees_synced": False}

    pnls       = [t.pnl for t in trades]
    fees       = [t.fee if t.fee is not None else 0.0 for t in trades]
    net_pnls   = [p - f for p, f in zip(pnls, fees)]
    fees_known = sum(1 for t in trades if t.fee is not None)

    total_gross = sum(pnls)
    total_fees  = sum(fees)
    total_net   = sum(net_pnls)

    wins_gross  = sum(1 for p in pnls     if p > 0)
    wins_net    = sum(1 for p in net_pnls if p > 0)

    return {
        "total_trades":  len(pnls),
        "total_pnl":     round(total_gross, 2),      # P&L bruto (sem taxas) — retro-compat
        "total_fees":    round(total_fees, 4),        # Corretagem acumulada
        "net_pnl":       round(total_net, 2),         # P&L líquido após taxas
        "fees_synced":   fees_known,                  # quantos trades têm fee registrada
        "fees_pending":  len(trades) - fees_known,    # ainda sem sincronização
        "wins":          wins_gross,
        "losses":        len(pnls) - wins_gross,
        "wins_net":      wins_net,
        "win_rate":      round(wins_gross / len(pnls) * 100, 1),
        "win_rate_net":  round(wins_net   / len(pnls) * 100, 1),
        "best_trade":    round(max(pnls), 2),
        "worst_trade":   round(min(pnls), 2),
        "avg_trade":     round(total_gross / len(pnls), 2),
        "avg_fee":       round(total_fees  / len(pnls), 4) if pnls else 0,
    }

@router.get("/api/bots/{bot_id}/trade-report")
async def trade_report(bot_id: int, trade_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Relatório evolutivo completo de um trade, com análise de IA (DeepSeek). Resultado persistido."""
    import aiohttp as _aio
    import datetime as _dt

    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")

    # ── 1. Escolhe o trade ──────────────────────────────────────────────────────
    # Preferimos trades de entrada (entry) fechados pois têm dados completos
    q = db.query(TradeModel).filter(
        TradeModel.bot_id == bot_id,
        TradeModel.type == "entry",
        TradeModel.exit_price.isnot(None),
    )
    trade = db.get(TradeModel, trade_id) if trade_id else q.order_by(TradeModel.timestamp.desc()).first()
    if not trade:
        raise HTTPException(404, "Nenhum trade finalizado encontrado para este bot")

    # All closed trades for this bot (for the selector)
    all_trades = q.order_by(TradeModel.timestamp.desc()).all()

    # ── 2. Busca candles via signal_logs ────────────────────────────────────────
    window_start = trade.timestamp - _dt.timedelta(days=30)
    window_end   = (trade.closed_at or _dt.datetime.utcnow()) + _dt.timedelta(days=7)
    logs = (
        db.query(SignalLogModel)
        .filter(
            SignalLogModel.bot_id == bot_id,
            SignalLogModel.timestamp >= window_start,
            SignalLogModel.timestamp <= window_end,
            SignalLogModel.candle_close.isnot(None),
        )
        .order_by(SignalLogModel.timestamp)
        .limit(300)
        .all()
    )

    # Convert to [epoch_ms, open, high, low, close, volume] (Chart.jsx format)
    candles = [
        [
            int(log.timestamp.timestamp() * 1000),
            log.candle_open or log.candle_close,
            log.candle_high or log.candle_close,
            log.candle_low  or log.candle_close,
            log.candle_close,
            log.candle_volume or 0,
        ]
        for log in logs
    ]

    # ── 3. Estatísticas do trade ────────────────────────────────────────────────
    closes    = [c[4] for c in candles]
    peak      = max(closes) if trade.direction == "LONG" else min(closes) if closes else 0
    n_candles = len(candles)
    entry_ts  = trade.timestamp
    closed_ts = trade.closed_at
    duration  = str(closed_ts - entry_ts).split(".")[0] if closed_ts else "Em aberto"
    pnl_pct   = 0.0
    if trade.entry_price and trade.exit_price:
        pnl_pct = (trade.exit_price - trade.entry_price) / trade.entry_price * 100
        if trade.direction == "SHORT":
            pnl_pct = -pnl_pct

    # ── 4. Marcadores do gráfico (entry + exit) ─────────────────────────────────
    markers = [
        {
            "type":        "entry",
            "timestamp":   entry_ts.isoformat(),
            "direction":   trade.direction,
            "entry_price": trade.entry_price,
            "sl_price":    trade.sl_price,
            "tp1_price":   trade.tp1_price,
        }
    ]
    if closed_ts and trade.exit_price:
        markers.append({
            "type":      "exit",
            "timestamp": closed_ts.isoformat(),
            "direction": trade.direction,
            "pnl":       round(trade.pnl or 0, 2),
            "event":     trade.event or "SW_TS",
        })

    # ── 5. Análise de IA (DeepSeek) — usa cache se já gerado ───────────────────
    cached = db.query(TradeReportModel).filter(TradeReportModel.trade_id == trade.id).first()
    ai_analysis = cached.ai_analysis if cached else None

    if not ai_analysis:
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if api_key:
            prompt = (
                "Você é um especialista em trading algorítmico. Analise o relatório abaixo de uma operação "
                "já finalizada e forneça uma análise técnica objetiva em 3-4 frases.\n"
                "REGRAS: 1) Nunca use saudações. 2) Seja direto e quantitativo. "
                "3) Avalie qualidade da entrada, gestão do risco, e eficiência da saída. "
                "4) Se o resultado foi positivo, explique o motivo técnico. Se negativo, aponte o erro.\n\n"
                f"Robô: {bot.name} | Estratégia: {bot.strategy_id} | Símbolo: {trade.symbol}\n"
                f"Direção: {trade.direction}\n"
                f"Entrada: ${trade.entry_price:.4f} em {entry_ts.strftime('%Y-%m-%d %H:%M UTC')}\n"
                f"Saída: ${trade.exit_price:.4f} em {closed_ts.strftime('%Y-%m-%d %H:%M UTC') if closed_ts else 'N/A'}\n"
                f"PnL: {pnl_pct:+.2f}% (${trade.pnl:.2f})\n"
                f"Duração: {duration}\n"
                f"Stop Loss Inicial: ${trade.sl_price:.4f}\n"
                f"Preço Máximo na Operação: ${peak:.4f}\n"
                f"Candles na Operação: {n_candles}\n"
            )
            try:
                async with _aio.ClientSession() as session:
                    async with session.post(
                        "https://api.deepseek.com/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                              "max_tokens": 400, "temperature": 0.5},
                        timeout=_aio.ClientTimeout(total=25),
                    ) as r:
                        data   = await r.json()
                        ai_analysis = data["choices"][0]["message"]["content"].strip()
                        report = TradeReportModel(trade_id=trade.id, bot_id=bot_id, ai_analysis=ai_analysis)
                        db.add(report)
                        db.commit()
            except Exception as exc:
                log.warning("DeepSeek trade-report falhou: %s", exc)
                ai_analysis = "Análise de IA indisponível no momento."
        else:
            ai_analysis = "DEEPSEEK_API_KEY não configurada."

    return {
        "bot": {
            "id": bot.id, "name": bot.name,
            "strategy_id": bot.strategy_id, "symbol": bot.symbol,
            "timeframe": bot.timeframe,
        },
        "trade": {
            "id":          trade.id,
            "direction":   trade.direction,
            "symbol":      trade.symbol,
            "entry_price": trade.entry_price,
            "exit_price":  trade.exit_price,
            "sl_price":    trade.sl_price,
            "tp1_price":   trade.tp1_price,
            "pnl":         round(trade.pnl or 0, 2),
            "pnl_pct":     round(pnl_pct, 2),
            "peak_price":  round(peak, 4) if peak else None,
            "n_candles":   n_candles,
            "duration":    duration,
            "entry_time":  entry_ts.isoformat(),
            "closed_at":   closed_ts.isoformat() if closed_ts else None,
        },
        "all_trades": [
            {"id": t.id, "entry_price": t.entry_price, "exit_price": t.exit_price,
             "pnl": round(t.pnl or 0, 2), "entry_time": t.timestamp.isoformat()}
            for t in all_trades
        ],
        "candles":    candles,
        "markers":    markers,
        "ai_analysis": ai_analysis,
    }
