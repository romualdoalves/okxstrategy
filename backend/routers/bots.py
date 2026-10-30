"""
Rotas de bots: CRUD, controle (start/stop/liquidate), performance e status.
"""

from __future__ import annotations
from fastapi import APIRouter
import logging

import aiohttp
import asyncio
from typing import Optional

from pydantic import BaseModel
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import AiAnalysisLogModel, BotModel, BotSnapshotModel, SignalLogModel, TradeModel, get_db
from ..exchanges.factory import build_exchange
from ..strategies.registry import get_strategy
from ..bot_manager import manager
from ..common import FIXED_STAKE_USD, _assert_spot_only, _assert_symbol_available, _assert_symbol_tradable, _norm_symbol

router = APIRouter()
log = logging.getLogger("main")


# ── Pydantic schemas ──────────────────────────────────────────────────────────
class BotCreate(BaseModel):
    name:            str
    strategy_id:     str
    symbol:          str    = "BTC-USDT"
    timeframe:       str    = "15m"
    demo:            bool   = True
    stake_usd:       float  = FIXED_STAKE_USD
    leverage:        int    = 1
    stop_loss_usd:   float  = -50.0
    strategy_params: dict   = {}
    force_retired:   bool   = False   # confirma criar bot com estratégia aposentada pelo estudo

class BotUpdate(BaseModel):
    name:            Optional[str]   = None
    symbol:          Optional[str]   = None
    timeframe:       Optional[str]   = None
    stake_usd:       Optional[float] = None
    leverage:        Optional[int]   = None
    stop_loss_usd:   Optional[float] = None
    strategy_params: Optional[dict]  = None

class BotSymbolSwitch(BaseModel):
    symbol: str = "BTC-USDT"


# ── Rotas: Bots ───────────────────────────────────────────────────────────────
@router.get("/api/bots/performance")
def get_performance_ranking(db: Session = Depends(get_db)):
    from sqlalchemy import func
    # 1. Busca robôs atuais
    bots = db.query(BotModel).all()
    bot_map = {b.id: b for b in bots}
    
    # 2. Usa a mesma base do /api/trades/summary: uma linha por round-trip
    # fechado. Linhas type="exit"/"tp1" são eventos visuais e não devem
    # duplicar a contagem do ranking histórico.
    # "pnl" aqui é o LÍQUIDO (após taxas) — mesma base do "P&L Total" do
    # Dashboard. Antes somava só o bruto, o que fazia esta tela e o Dashboard
    # mostrarem dois números diferentes sob o mesmo nome "PnL Total".
    stats_rows = db.query(
        TradeModel.bot_id,
        func.sum(TradeModel.pnl).label("pnl_sum"),
        func.sum(TradeModel.fee).label("fee_sum"),
        func.count(TradeModel.id).label("total_count")
    ).filter(
        TradeModel.type == "entry",
        TradeModel.exit_price.isnot(None),
        TradeModel.pnl.is_not(None),
    ).group_by(TradeModel.bot_id).all()
    
    # 3. Busca vitórias separadamente para segurança
    wins_rows = db.query(
        TradeModel.bot_id,
        func.count(TradeModel.id).label("win_count")
    ).filter(
        TradeModel.type == "entry",
        TradeModel.exit_price.isnot(None),
        TradeModel.pnl > 0,
    ).group_by(TradeModel.bot_id).all()
    wins_map = {row[0]: row[1] for row in wins_rows}
    
    ranking = []
    seen_ids = set()
    
    for bid, pnl_sum, fee_sum, total_count in stats_rows:
        bot = bot_map.get(bid)
        name = bot.name if bot else f"Bot Antigo (ID {bid})"

        pnl_gross = float(pnl_sum or 0)
        fee_total = float(fee_sum or 0)
        pnl_net   = pnl_gross - fee_total
        total     = int(total_count or 0)
        wins      = int(wins_map.get(bid, 0))
        win_rate  = (wins / total * 100) if total > 0 else 0

        ranking.append({
            "id": bid,
            "name": name,
            "strategy_id": bot.strategy_id if bot else "N/A",
            "symbol": bot.symbol if bot else "N/A",
            "pnl": round(pnl_net, 2),
            "pnl_gross": round(pnl_gross, 2),
            "fees": round(fee_total, 4),
            "trades": total,
            "win_rate": round(win_rate, 1),
            "is_active": bot is not None
        })
        seen_ids.add(bid)
    
    # 4. Filtra apenas quem executou pelo menos um trade
    final_ranking = [r for r in ranking if r["trades"] > 0]
    
    return sorted(final_ranking, key=lambda x: x["pnl"], reverse=True)

@router.get("/api/bots/performance/active")
async def get_active_performance(db: Session = Depends(get_db)):
    """Retorna o ranking de bots com operações abertas (PnL não realizado).

    Usa unrealized_pl e unrealized_plpc da OKX quando disponíveis;
    fallback para cálculo local se a posição não for encontrada na exchange.
    """
    from ..exchanges.base import Position as _Position
    import aiohttp as _aio_rank

    bots = db.query(BotModel).all()
    active_bots = [b for b in bots
                   if manager.get_status(b.id) and manager.get_status(b.id).get("direction", 0) != 0]

    # Busca posições da exchange em paralelo para todos os bots activos
    async with _aio_rank.ClientSession() as _sess_rank:
        exchange = build_exchange(_sess_rank)
    pos_results = await asyncio.gather(
        *[exchange.get_position(b.symbol) for b in active_bots],
        return_exceptions=True,
    )
    exchange_pos: dict[int, _Position] = {}
    for b, result in zip(active_bots, pos_results):
        if isinstance(result, _Position):
            exchange_pos[b.id] = result

    active_ranking = []

    for b in active_bots:
        status = manager.get_status(b.id)
        if not status:
            continue

        direction = status.get("direction", 0)
        if direction == 0:
            continue

        entry_price = status.get("entry_price", 0)
        last_price  = status.get("last_price", 0)
        sl_price    = status.get("sl_price", 0)

        pos = exchange_pos.get(b.id)

        if pos and pos.unrealized_plpc != 0.0:
            # Fonte de verdade: exchange
            pnl_pct = pos.unrealized_plpc * 100
            pnl_usd = pos.unrealized_pnl
        elif entry_price > 0 and last_price > 0:
            # Fallback local
            pnl_pct = ((last_price - entry_price) / entry_price) * 100 * direction
            base_qty = float(status.get("size", 0) or 0)
            if base_qty > 0:
                pnl_usd = base_qty * (last_price - entry_price) * direction
            else:
                pnl_usd = (pnl_pct / 100) * FIXED_STAKE_USD
        else:
            continue

        # PnL % Garantido (prioriza o cálculo dinâmico do robô/sombra)
        guaranteed_pnl_pct = status.get("guaranteed_pnl", 0.0)

        # Fallback se o robô não reportar (ex: robô ainda no SL fixo)
        if guaranteed_pnl_pct == 0 and sl_price > 0 and entry_price > 0:
            diff = (sl_price - entry_price) / entry_price * direction
            if diff > 0:
                guaranteed_pnl_pct = diff * 100

        guaranteed_pnl_usd = (guaranteed_pnl_pct / 100) * FIXED_STAKE_USD

        p = b.strategy_params or {}
        sts_trigger = (
            p.get("sts_activation_pct") or
            p.get("sts_trigger_pct") or
            p.get("ts_activation_pct") or
            p.get("trailing_stop_activation") or
            p.get("ts_activation") or
            0.0
        )
        sts_step = (
            p.get("sts_step_pct") or
            p.get("ts_step_pct") or
            p.get("ts_step") or
            0.0
        )
        if sts_trigger == 0:
            sts_trigger = 1.0

        active_ranking.append({
            "id": b.id,
            "name": b.name,
            "strategy_id": b.strategy_id,
            "symbol": b.symbol,
            "direction": "LONG" if direction == 1 else "SHORT",
            "entry_price": round(entry_price, 4),
            "last_price": round(last_price, 4),
            "sl_price": round(sl_price, 4),
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usd": round(pnl_usd, 2),
            "guaranteed_pnl_usd": round(guaranteed_pnl_usd, 2),
            "guaranteed_pnl_pct": round(guaranteed_pnl_pct, 2),
            "sts_trigger": sts_trigger,
            "sts_step": sts_step,
            "exchange_pnl": pos.unrealized_pnl if pos else None,
            "exchange_pnl_pct": round(pos.unrealized_plpc * 100, 2) if pos else None,
            "cost_basis": round(pos.cost_basis, 4) if pos else None,
            "change_today": round(pos.change_today * 100, 2) if pos else None,
        })
            
    # Ordena pelo maior PnL aberto
    return sorted(active_ranking, key=lambda x: x["pnl_usd"], reverse=True)

@router.get("/api/bots")
def list_bots(db: Session = Depends(get_db)):
    from sqlalchemy import func

    bots = db.query(BotModel).all()

    # Uma única query agregada para a taxa acumulada de todos os bots, em vez
    # de uma consulta por bot — mesmo padrão do /api/bots/performance.
    fee_rows = db.query(
        TradeModel.bot_id,
        func.sum(TradeModel.fee).label("fee_sum"),
    ).filter(
        TradeModel.type == "exit",
        TradeModel.fee.isnot(None),
    ).group_by(TradeModel.bot_id).all()
    fees_by_bot = {row[0]: float(row[1] or 0) for row in fee_rows}

    result = []
    for b in bots:
        status = manager.get_status(b.id)
        if status is not None:
            status = {**status, "accumulated_fees": round(fees_by_bot.get(b.id, 0.0), 4)}
        result.append({
            "id": b.id, "name": b.name,
            "strategy_id": b.strategy_id, "symbol": b.symbol,
            "timeframe": b.timeframe, "demo": b.demo,
            "stake_usd": FIXED_STAKE_USD, "leverage": b.leverage,
            "active": b.active, "created_at": str(b.created_at),
            "baseline_balance": b.baseline_balance or 0.0,
            "runtime": status,
        })
    return result

@router.post("/api/bots", status_code=201)
async def create_bot(payload: BotCreate, db: Session = Depends(get_db)):
    # Valida estratégia
    try:
        get_strategy(payload.strategy_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    from ..validation.reports import study_summary
    study = study_summary(payload.strategy_id)
    if study["study_verdict"] == "retired" and not payload.force_retired:
        raise HTTPException(409, f"Estratégia {payload.strategy_id.upper()} aposentada pelo estudo: "
                                 f"{study['study_reason']} Confirme para criar mesmo assim.")

    _assert_spot_only(payload.symbol)
    payload.symbol = _norm_symbol(payload.symbol)
    await _assert_symbol_tradable(payload.symbol, demo=payload.demo)
    payload.leverage = 1
    payload.stake_usd = FIXED_STAKE_USD
    _assert_symbol_available(db, payload.symbol)

    bot = BotModel(**payload.model_dump(exclude={"force_retired"}))
    db.add(bot)
    db.commit()
    db.refresh(bot)

    # Snapshot do saldo spot pré-existente — isola holdings do usuário das
    # divergências. Qualquer quantidade acima deste baseline é do bot.
    try:
        async with aiohttp.ClientSession() as _s:
            _ex = build_exchange(_s, demo=bot.demo)
            _pos = await _ex.get_position(bot.symbol)
            if _pos and _pos.size > 1e-9:
                bot.baseline_balance = float(_pos.size)
                db.commit()
                log.info(
                    "Bot %d: baseline=%.8f %s (holdings pré-existentes ignorados na detecção de divergências)",
                    bot.id, bot.baseline_balance, bot.symbol,
                )
    except Exception as _exc:
        log.warning("Bot %d: baseline snapshot falhou (%s) — assumindo 0.0", bot.id, _exc)

    log.info("Bot criado: %d — %s", bot.id, bot.name)
    return {"id": bot.id, "name": bot.name, "strategy_id": bot.strategy_id}

@router.get("/api/bots/{bot_id}")
def get_bot(bot_id: int, db: Session = Depends(get_db)):
    from sqlalchemy import func

    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")

    status = manager.get_status(bot_id)
    if status is not None:
        fee_sum = db.query(func.sum(TradeModel.fee)).filter(
            TradeModel.bot_id == bot_id,
            TradeModel.type == "exit",
            TradeModel.fee.isnot(None),
        ).scalar()
        status = {**status, "accumulated_fees": round(float(fee_sum or 0), 4)}

    return {**bot.__dict__, "runtime": status}

def _assert_flat_before_symbol_change(db: Session, bot: BotModel, new_symbol: str) -> None:
    """
    Trocar o ativo de um bot com posição aberta deixaria a posição (e o Stop Loss) órfãos no ativo antigo e o
    bot, ao reiniciar, adotaria o trade de entrada antigo para o ativo novo (tamanho e stop errados).
    Só permite a troca com o bot flat: sem posição em memória e sem trade de entrada em aberto no banco.
    """
    if _norm_symbol(new_symbol) == _norm_symbol(bot.symbol):
        return
    inst = manager._instances.get(bot.id)
    open_trade = (
        db.query(TradeModel)
        .filter(TradeModel.bot_id == bot.id, TradeModel.type == "entry", TradeModel.exit_price.is_(None))
        .first()
    )
    if (inst is not None and getattr(inst, "_direction", 0) != 0) or open_trade is not None:
        raise HTTPException(
            400,
            f"Não é possível trocar o ativo de '{bot.name}' com posição aberta em {bot.symbol}. "
            "Feche ou liquide a posição primeiro (e aguarde o fechamento ser registrado).",
        )


@router.patch("/api/bots/{bot_id}")
async def update_bot(bot_id: int, payload: BotUpdate, db: Session = Depends(get_db)):
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
    if bot.active:
        raise HTTPException(400, "Pare o bot antes de editar.")
    if payload.symbol:
        _assert_spot_only(payload.symbol)
        payload.symbol = _norm_symbol(payload.symbol)
        await _assert_symbol_tradable(payload.symbol, demo=bool(bot.demo))
    if payload.symbol and payload.symbol != _norm_symbol(bot.symbol):
        _assert_flat_before_symbol_change(db, bot, payload.symbol)
        _assert_symbol_available(db, payload.symbol, exclude_bot_id=bot_id)
    update_data = payload.model_dump(exclude_none=True)
    update_data["leverage"] = 1
    update_data["stake_usd"] = FIXED_STAKE_USD
    for k, v in update_data.items():
        setattr(bot, k, v)
    db.commit()
    return {"ok": True}

@router.post("/api/bots/{bot_id}/recapture-baseline")
async def recapture_baseline(bot_id: int, db: Session = Depends(get_db)):
    """Recaptura o saldo spot atual como baseline — limpa divergências de holdings pré-existentes."""
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")

    status = manager.get_status(bot_id)
    if status and int(status.get("direction", 0) or 0) != 0:
        raise HTTPException(
            400,
            "Bot tem uma posição aberta agora — recapturar o baseline absorveria essa "
            "posição real como se fosse holding pré-existente, escondendo-a de todos os "
            "cálculos de exposição/divergência. Feche a posição (ou espere ela fechar) "
            "antes de recapturar o baseline.",
        )

    try:
        async with aiohttp.ClientSession() as _s:
            _ex = build_exchange(_s, demo=bot.demo)
            _pos = await _ex.get_position(bot.symbol)
            new_baseline = float(_pos.size) if (_pos and _pos.size > 1e-9) else 0.0
            bot.baseline_balance = new_baseline
            db.commit()
            # Atualiza instância em memória para efeito imediato (sem reiniciar o bot)
            if bot_id in manager._instances:
                manager._instances[bot_id].config.baseline_balance = new_baseline
                manager._instances[bot_id]._last_order_error = None
                manager._instances[bot_id]._hold_reason = None
        log.info("Bot %d: baseline recapturado → %.8f %s", bot_id, new_baseline, bot.symbol)
        return {"ok": True, "baseline_balance": new_baseline, "symbol": bot.symbol}
    except Exception as exc:
        raise HTTPException(500, f"Falha ao recapturar baseline: {exc}")

@router.delete("/api/bots/{bot_id}", status_code=204)
def delete_bot(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
    manager.stop_bot(bot_id)
    # Remove registros filhos primeiro (FK constraint no PostgreSQL)
    db.query(AiAnalysisLogModel).filter(AiAnalysisLogModel.bot_id == bot_id).delete()
    db.query(SignalLogModel).filter(SignalLogModel.bot_id == bot_id).delete()
    db.query(BotSnapshotModel).filter(BotSnapshotModel.bot_id == bot_id).delete()
    db.query(TradeModel).filter(TradeModel.bot_id == bot_id).delete()
    db.delete(bot)
    db.commit()

@router.post("/api/bots/{bot_id}/start")
async def start_bot(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
    _assert_symbol_available(db, bot.symbol, exclude_bot_id=bot_id, active_only=True)
    bot.symbol = _norm_symbol(bot.symbol)
    bot.leverage = 1
    bot.active = True
    db.commit()
    try:
        manager.start_bot(bot)   # asyncio.create_task() exige contexto async
    except RuntimeError as exc:
        bot.active = False
        db.commit()
        raise HTTPException(400, str(exc)) from exc
    return {"status": "running", "bot_id": bot_id}

@router.post("/api/bots/{bot_id}/stop")
async def stop_bot(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
    bot.active = False
    db.commit()
    manager.stop_bot(bot_id)
    return {"status": "stopped", "bot_id": bot_id}

@router.post("/api/bots/{bot_id}/liquidate")
async def liquidate_bot_api(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
    ok, error = await manager.liquidate_bot(bot_id)
    if not ok:
        raise HTTPException(409, error or "Falha ao fechar a posição; ela continua aberta na OKX.")
    return {"status": "liquidated", "bot_id": bot_id}

@router.post("/api/bots/{bot_id}/switch-symbol")
async def switch_bot_symbol(
    bot_id: int,
    payload: BotSymbolSwitch,
    db: Session = Depends(get_db),
):
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")

    _assert_spot_only(payload.symbol)
    payload.symbol = _norm_symbol(payload.symbol)
    await _assert_symbol_tradable(payload.symbol, demo=bool(bot.demo))
    _assert_flat_before_symbol_change(db, bot, payload.symbol)
    _assert_symbol_available(db, payload.symbol, exclude_bot_id=bot_id)

    was_active = bool(bot.active)
    if was_active:
        manager.stop_bot(bot_id)

    bot.symbol = payload.symbol
    db.commit()
    db.refresh(bot)

    if was_active:
        try:
            manager.start_bot(bot)
        except RuntimeError as exc:
            bot.active = False
            db.commit()
            raise HTTPException(400, str(exc)) from exc

    return {
        "ok": True,
        "bot_id": bot_id,
        "symbol": bot.symbol,
        "status": "running" if was_active else "stopped",
    }

@router.post("/api/bots/sync-trailing")
async def sync_trailing_stops():
    """Força todos os robôs ativos a sincronizarem o Trailing Stop com a exchange."""
    active_ids = list(manager._instances.keys())
    count = 0
    for bid in active_ids:
        bot = manager._instances.get(bid)
        # Se o robô está em uma operação (direction != 0), ele DEVE sincronizar
        if bot and bot._direction != 0:
            if hasattr(bot, "exchange") and bot.exchange:
                try:
                    await bot.force_sync_trailing(bot.exchange)
                    count += 1
                except Exception as e:
                    log.error("Erro ao sincronizar Bot %d: %s", bid, e)
    log.info("Sincronização manual concluída para %d robôs.", count)
    return {"message": f"Sincronização concluída para {count} robôs.", "count": count}

@router.get("/api/bots/{bot_id}/status")
def bot_status(bot_id: int):
    status = manager.get_status(bot_id)
    if status is None:
        return {"status": "stopped"}
    return status
