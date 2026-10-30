"""
BotManager: gerencia as instâncias de bots concorrentes.
"""

from __future__ import annotations

import asyncio
from typing import Optional
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from ..database import BotModel, TradeModel
from .instance import BotInstance
from .common import log


class BotManager:
    """Singleton que gerencia todas as instâncias de bots."""

    def __init__(self):
        self._instances: dict[int, BotInstance] = {}
        self._ws_clients: set = set()

    # ── WebSocket broadcast ───────────────────────────────────────────────────
    def register_ws(self, queue: asyncio.Queue):
        self._ws_clients.add(queue)

    def unregister_ws(self, queue: asyncio.Queue):
        self._ws_clients.discard(queue)

    async def _broadcast(self, msg: dict):
        import json
        data = json.dumps(BotInstance._to_json(msg))
        dead = set()
        for q in self._ws_clients:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dead.add(q)
        self._ws_clients -= dead

    # ── Gestão de bots ────────────────────────────────────────────────────────
    def start_bot(self, config: BotModel) -> BotInstance:
        restarted = False
        target_symbol = (config.symbol or "").upper()
        for other_id, other in self._instances.items():
            if other_id == config.id or other.status != "running":
                continue
            if (other.config.symbol or "").upper() == target_symbol:
                raise RuntimeError(
                    f"Ativo {config.symbol} já está em execução no bot {other.config.id} ({other.config.name})."
                )
        if config.id in self._instances:
            prev = self._instances[config.id]
            if prev.status == "running":
                return prev
            prev.stop()
            restarted = True

        inst = BotInstance(config, ws_broadcast=self._broadcast)
        inst._restarted = restarted
        inst.start()
        self._instances[config.id] = inst
        log.info("Bot %d iniciado (%s / %s)", config.id, config.strategy_id, config.symbol)
        return inst

    def stop_bot(self, bot_id: int):
        inst = self._instances.get(bot_id)
        if inst:
            inst.stop()
            log.info("Bot %d parado.", bot_id)

    async def liquidate_bot(self, bot_id: int):
        """Fecha a posição aberta de um bot específico."""
        inst = self._instances.get(bot_id)
        if not inst or not inst.exchange:
            return False, "Bot não está em execução."
        if inst._direction == 0:
            return False, "O bot não tem posição aberta."
        log.info("Reset: Liquidando manualmente bot %d (%s)", bot_id, inst.config.symbol)
        ok = await inst.manual_liquidate(inst.exchange)
        return bool(ok), (None if ok else (getattr(inst, "last_close_error", None) or "Falha ao fechar a posição."))

    async def liquidate_all(self, db: Session):
        """Para todos os bots, cancela ordens algo pendentes e fecha posições abertas."""
        running_ids = list(self._instances.keys())
        
        # 1. Cancela TODAS as ordens e fecha TODAS as posições na exchange
        # Usa o exchange do primeiro bot ativo como referência
        cancelled_exchange = False
        for bid in running_ids:
            inst = self._instances.get(bid)
            if inst and inst.exchange:
                try:
                    cancelled = await inst.exchange.close_all_positions()
                    log.info("Reset Global: %d posições e ordens limpas na exchange.", cancelled)
                    cancelled_exchange = True
                except Exception as e:
                    log.warning("Reset Global: falha ao limpar exchange: %s", e)
                break  # Só precisa fazer uma vez (a API cancela tudo)

        # Fallback: Se não havia bots rodando em memória, mas queremos limpar a exchange
        if not cancelled_exchange:
            try:
                import aiohttp
                from ..exchanges.factory import build_exchange
                async with aiohttp.ClientSession() as session:
                    ex = build_exchange(session)
                    cancelled = await ex.close_all_positions()
                    log.info("Reset Global (Fallback): %d posições e ordens limpas na exchange.", cancelled)
            except Exception as e:
                log.warning("Reset Global (Fallback): falha ao limpar exchange: %s", e)

        # 2. Desliga e para todos os robôs em execução na memória
        for bid in running_ids:
            inst = self._instances.get(bid)
            if inst:
                if inst._direction != 0 and inst.exchange:
                    log.info("Reset: Liquidando %s em memória", inst.config.symbol)
                    try:
                        await inst.manual_liquidate(inst.exchange)
                    except Exception:
                        pass
                self.stop_bot(bid)
            self._instances.pop(bid, None)
        
        # 3. Força o fechamento de todos os trades em aberto no banco de dados para evitar ressurgimento
        try:
            open_trades = db.query(TradeModel).filter(TradeModel.exit_price.is_(None)).all()
            for t in open_trades:
                t.exit_price = t.entry_price or 0.0
                t.pnl = 0.0
                t.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                t.event = "RESET_FORCE_CLOSE"
                
                # Também cria um registro de saída correspondente no histórico para manter consistência
                exit_trade = TradeModel(
                    bot_id=t.bot_id,
                    symbol=t.symbol,
                    type="exit",
                    event="RESET_FORCE_CLOSE",
                    direction=t.direction,
                    size=t.size,
                    entry_price=t.entry_price,
                    exit_price=t.entry_price,
                    sl_price=t.sl_price,
                    tp1_price=t.tp1_price,
                    pnl=0.0,
                    closed_at=t.closed_at,
                    source="bot"
                )
                db.add(exit_trade)
            db.commit()
            log.info("Reset Global: %d trades abertos no banco de dados foram fechados forçadamente.", len(open_trades))
        except Exception as e:
            log.warning("Reset Global: falha ao fechar trades em aberto no banco: %s", e)

        from ..database import BotModel
        db.query(BotModel).update({BotModel.active: False})
        db.commit()

    def get_status(self, bot_id: int) -> Optional[dict]:
        inst = self._instances.get(bot_id)
        return inst.get_status() if inst else None

    def all_statuses(self) -> list[dict]:
        return [inst.get_status() for inst in self._instances.values()]

    def get_open_positions_summary(self) -> list[dict]:
        """Retorna resumo das posições abertas para uso em deploy/alertas."""
        result = []
        for bot_id, inst in self._instances.items():
            if inst._direction != 0:
                result.append({
                    "bot_id": bot_id,
                    "name": inst.config.name,
                    "symbol": inst.config.symbol,
                    "direction": "LONG" if inst._direction == 1 else "SHORT",
                    "entry_price": inst._entry_price,
                    "size": inst._sz,
                })
        return result

    async def _reconcile_loop(self):
        """Verifica periodicamente se algum bot ficou divergente na OKX."""
        while True:
            await asyncio.sleep(15)
            try:
                for bot in list(self._instances.values()):
                    exchange = bot.exchange
                    if exchange is None:
                        continue
                    pos = await exchange.get_position(bot.config.symbol)
                    qty = abs(float(pos.size or 0.0)) if pos else 0.0
                    net_qty = bot._net_size(pos)
                    if bot._direction == 0:
                        if net_qty > 1e-9 and bot._is_spot_symbol(bot.config.symbol):
                            if not bot._can_adopt_spot_position(pos, context="reconcile_loop"):
                                continue
                            adopted_dir = 1 if pos.side == "long" else -1
                            adopted_px = float(pos.avg_price or 0.0)
                            fallback_sl = round(
                                adopted_px * (1 - 0.05) if adopted_dir == 1
                                else adopted_px * (1 + 0.05),
                                6,
                            )
                            orphan_tp1 = adopted_px * (1.02 if adopted_dir == 1 else 0.98)
                            bot._direction = adopted_dir
                            bot._sz = net_qty
                            bot._sz_remaining = net_qty
                            bot._entry_price = adopted_px
                            bot._sl_price = fallback_sl
                            bot._tp1_price = orphan_tp1
                            trade_id = bot._save_trade({
                                "type": "entry",
                                "event": "ORPHAN_ADOPTED_RECONCILE",
                                "direction": "LONG" if adopted_dir == 1 else "SHORT",
                                "size": qty,
                                "entry_price": adopted_px,
                                "sl_price": fallback_sl,
                                "tp1_price": orphan_tp1,
                                "source": "orphan_recovery",
                            })
                            bot._current_trade_id = trade_id
                            log.warning(
                                "[Bot %d] Reconciliação adotou saldo spot órfão: %.8f %s @ %.6f.",
                                bot.config.id, qty, bot.config.symbol, adopted_px,
                            )
                        continue
                    if net_qty <= 0:
                        # Antes de fabricar um fechamento com pnl=0, tenta recuperar o fill
                        # real de saída na OKX — o WS pode só estar atrasado, e a posição já
                        # ter sido fechada com um preço/P&L real que não pode ser descartado.
                        real_exit = await bot._resolve_desync_exit_fill(exchange)
                        if real_exit:
                            exit_price, fill_qty = real_exit
                            ct_size = exchange.get_contract_size(bot.config.symbol)
                            pnl = fill_qty * ct_size * (exit_price - bot._entry_price) * bot._direction
                            log.warning(
                                "[Bot %d] DESYNC: banco tem posição %s aberta mas OKX está FLAT. "
                                "Fill real recuperado @ %.6f (pnl=%.4f) — fechando com dados reais.",
                                bot.config.id, bot.config.symbol, exit_price, pnl,
                            )
                        else:
                            exit_price, pnl = bot._entry_price or 0.0, 0.0
                            log.error(
                                "[Bot %d] DESYNC CRÍTICO: banco tem posição %s aberta mas OKX está "
                                "FLAT, e nenhum fill correspondente foi encontrado na OKX. Fechando "
                                "com pnl=0 — dado pode estar incorreto, investigar manualmente.",
                                bot.config.id, bot.config.symbol,
                            )
                        bot._record_pnl(pnl)
                        bot._save_trade({
                            "type": "exit", "event": "DESYNC_RECONCILE",
                            "exit_price": exit_price,
                            "pnl": pnl, "peak_price": bot._peak_price or None,
                            "closed_at": datetime.now(timezone.utc).replace(tzinfo=None),
                        }, update_id=bot._current_trade_id)
                        bot._reset()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("Reconciliação de posições falhou: %s", exc)


manager = BotManager()
