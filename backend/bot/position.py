"""
Gestão da posição aberta: breakeven, TP1/trailing, saída, liquidação e confirmação de fills.
"""

from __future__ import annotations

import asyncio
from typing import Optional
from datetime import datetime, timedelta, timezone

from ..exchanges.base import BaseExchange
from ..database import SessionLocal, TradeModel
from ..notifications import build_exit_msg, build_order_confirmed_msg
from .common import log


class PositionMixin:
    # ── Saída: TP1 da estratégia arma o trailing ──────────────────────────────
    # Espelha o backtest (backtest_engine.run_strict_sync): o trailing nativo é armado quando o preço chega ao
    # TP1 da estratégia e usa o `ts_pct` dela. Antes o bot armava a +1% fixo, travava breakeven a +0,4% e
    # limitava o callback a 1,5%, então os estudos não descreviam o que rodava ao vivo.
    def _trailing_callback(self, price: float) -> float:
        """Distância do trailing: `ts_pct` da estratégia; sem ele, 1,5 ATR limitado a 0,5%–5% (igual ao backtest)."""
        if self._ts_pct > 0:
            return self._ts_pct
        atr_cb = (self._current_atr * 1.5) / price if price and self._current_atr else 0.0
        return max(0.005, min(0.05, atr_cb))

    def _tp1_reached(self, price: float) -> bool:
        tp1 = self._tp1_price or (self._entry_price * (1.02 if self._direction == 1 else 0.98))
        return price >= tp1 if self._direction == 1 else price <= tp1

    async def _check_tp1(self, price: float, exchange: BaseExchange):
        if self._tp1_done: return

        pnl_pct = (price - self._entry_price) / self._entry_price
        if self._direction == -1: pnl_pct = -pnl_pct

        if self._tp1_reached(price):
            close_side = "sell" if self._direction == 1 else "buy"

            # Proteção Dupla: Garante que trailing OU fallback SL seja criado ANTES de marcar tp1_done
            protection_established = False

            old_sl_algo_id = self._sl_algo_id

            # Ativa a Sombra Dinâmica (Trailing Stop 100% dinâmico) para toda a posição!
            # A distância é baseada no ATR (volatilidade real) mas limitada por faixa de
            # lucro: quanto mais a operação avança, mais apertada a sombra fica.
            cb = self._trailing_callback(price)

            log.info("[Bot %d] DYNAMIC SHADOW TRIGGER ativado! Lucro: %.2f%%. Callback dinâmico: %.2f%%",
                     self.config.id, pnl_pct*100, cb*100)
            
            ts_id = await exchange.place_trailing_stop(
                self.config.symbol, close_side, self._sz, cb, price)
            if ts_id:
                self._ts_algo_id = ts_id
                self._ts_callback_ratio = cb
                self._sl_price = (
                    price * (1 - cb) if self._direction == 1
                    else price * (1 + cb)
                )
                protection_established = True
                log.info("[Bot %d] Trailing stop nativo criado com sucesso: %s", self.config.id, ts_id)
                if old_sl_algo_id:
                    try:
                        cancel_ok = await exchange.cancel_algo(self.config.symbol, old_sl_algo_id)
                        if cancel_ok:
                            self._sl_algo_id = None
                            log.info("[Bot %d] SL fixo cancelado após trailing confirmado.", self.config.id)
                    except Exception as cancel_err:
                        log.warning("[Bot %d] Falha ao cancelar SL fixo antigo após trailing: %s",
                                   self.config.id, cancel_err)
            else:
                err = getattr(exchange, "last_order_error", None) or {}
                self._record_order_rejection(
                    side=close_side,
                    order_type="trailing_stop",
                    reason=err.get("message", "Trailing stop nativo recusado pela exchange."),
                    status="fallback_attempted",
                    raw_payload=err,
                )
                # Tenta fallback SL fixo
                fallback_stop = (
                    max(self._entry_price, price * (1 - cb))
                    if self._direction == 1
                    else min(self._entry_price, price * (1 + cb))
                )
                fallback_id = await exchange.place_stop_loss(
                    self.config.symbol,
                    close_side,
                    self._sz,
                    fallback_stop,
                )
                if fallback_id:
                    if old_sl_algo_id:
                        try:
                            await exchange.cancel_algo(self.config.symbol, old_sl_algo_id)
                        except Exception as cancel_err:
                            log.warning("[Bot %d] Falha ao cancelar SL fixo antigo após fallback: %s",
                                        self.config.id, cancel_err)
                    self._sl_algo_id = fallback_id
                    self._sl_price = fallback_stop
                    protection_established = True
                    log.warning(
                        "[Bot %d] Trailing recusado; Stop Loss fixo fallback criado: %s @ %.4f",
                        self.config.id,
                        fallback_id,
                        fallback_stop,
                    )
                else:
                    fallback_err = getattr(exchange, "last_order_error", None) or {}
                    self._record_order_rejection(
                        side=close_side,
                        order_type="stop_loss_fallback",
                        reason=fallback_err.get("message", "Fallback de Stop Loss recusado pela exchange."),
                        status="critical",
                        raw_payload=fallback_err,
                    )
                    log.error("[Bot %d] ERRO CRÍTICO: Trailing E fallback SL foram recusados no TP1.", self.config.id)
            
            # APENAS marca tp1_done se proteção foi estabelecida com sucesso
            if not protection_established:
                log.error("[Bot %d] TP1 NÃO ATIVADO: Nenhuma proteção pôde ser estabelecida.", self.config.id)
                return
            
            self._tp1_done = True
            # Mantém get_status() coerente no mesmo ciclo em que a proteção nasce.
            # Nunca regride: o pico desde a entrada já pode ser maior que `price` agora.
            if self._ts_algo_id:
                if self._direction == 1:
                    self._peak_price = max(self._peak_price, price)
                else:
                    self._peak_price = min(self._peak_price, price) if self._peak_price else price
            
            self._save_trade({
                "type": "tp1", "event": "DYNAMIC_SHADOW_TRIGGER",
                "pnl": 0.0,   # Lucro não é realizado agora, a sombra garantiu o breakeven
                "sl_price": self._sl_price,   # Nível inicial da sombra, não o SL estático da entrada
            }, update_id=self._current_trade_id)
            # Persiste imediatamente o nível do trailing stop no entry record
            self._persist_trailing_stop_level()

            await self._broadcast({"type": "trade", "bot_id": self.config.id,
                                   "event": "shadow_trigger", "price": price, "pnl": 0.0})

            # Notifica o usuário no Telegram
            direction_str = "long" if self._direction == 1 else "short"
            try:
                msg = (
                    f"🛡️ <b>DYNAMIC SHADOW TRIGGER ATIVADO</b> 🛡️\n\n"
                    f"🤖 Bot: {self.config.name} ({self.config.symbol})\n"
                    f"📈 Lucro Atual: +{pnl_pct*100:.2f}%\n"
                    f"🎯 Sombra (Trailing): {cb*100:.2f}% de distância da máxima\n\n"
                    f"O Stop Loss inicial foi cancelado. Uma sombra dinâmica foi acoplada ao preço. "
                    f"A partir de agora, é matematicamente impossível perder dinheiro nesta operação."
                )
                asyncio.create_task(self._notifier.send(msg))
            except:
                pass

    # ── Persistência ──────────────────────────────────────────────────────────
    def _persist_trailing_stop_level(self):
        """
        Salva o nível atual do trailing stop (SW) no registro de entrada do banco.
        Chamado sempre que _sl_price avança para garantir que restarts recuperem
        o nível correto — e não apenas o SL original da entrada.
        """
        if not self._current_trade_id or self._sl_price <= 0:
            return
        db = SessionLocal()
        try:
            trade = db.query(TradeModel).filter(
                TradeModel.id == self._current_trade_id
            ).first()
            if trade:
                trade.sl_price = round(self._sl_price, 6)
                db.commit()
                self._last_persisted_sl = self._sl_price
        except Exception as exc:
            log.debug("[Bot %d] Falha ao persistir trailing stop: %s", self.config.id, exc)
        finally:
            db.close()

    async def _deferred_fee_sync(self, trade_id: int, symbol: str):
        """
        OKX registra taxas diretamente nos fills. Mantido como no-op para
        compatibilidade com chamadas antigas após fechamento.
        """
        return

    async def _resolve_desync_exit_fill(self, exchange: BaseExchange) -> Optional[tuple[float, float]]:
        """
        Busca na OKX o fill real que fechou esta posição, para quando o WebSocket
        privado perde/atrasa o evento e o `_reconcile_loop` detecta a posição já
        flat na exchange antes do app processar a saída. Retorna (preço médio
        ponderado, quantidade) do(s) fill(s) de fechamento, ou None se nenhum
        fill correspondente for encontrado na janela de busca.
        """
        close_side = "sell" if self._direction == 1 else "buy"
        sym_norm = self.config.symbol.upper().replace("/", "").replace("-", "")
        now = datetime.now(timezone.utc)
        lookback = now - timedelta(hours=3)
        try:
            fills = await exchange.get_activities(
                after=lookback.strftime("%Y-%m-%dT%H:%M:%SZ"),
                until=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        except Exception as exc:
            log.warning("[Bot %d] Falha ao buscar fills para reconciliação de desync: %s",
                        self.config.id, exc)
            return None

        matching = [
            f for f in fills
            if f.get("symbol", "").upper().replace("/", "").replace("-", "") == sym_norm
            and f.get("side") == close_side
        ]
        if not matching:
            return None

        total_qty = 0.0
        weighted_px = 0.0
        for f in matching:
            try:
                px  = float(f.get("price") or 0)
                qty = float(f.get("qty") or 0)
            except (TypeError, ValueError):
                continue
            if px <= 0 or qty <= 0:
                continue
            weighted_px += px * qty
            total_qty   += qty

        if total_qty <= 0:
            return None
        return round(weighted_px / total_qty, 8), total_qty

    async def _on_position_closed(self, price: float, size: float, exchange: BaseExchange):
        """Finaliza o trade e limpa o estado do bot."""
        if self._direction == 0: return

        ct_size = exchange.get_contract_size(self.config.symbol)
        pnl = size * ct_size * (price - self._entry_price) * self._direction
        entry_price_snap = self._entry_price
        direction_snap   = "long" if self._direction == 1 else "short"

        self._record_pnl(pnl)
        self._save_trade({
            "type": "exit", "event": "WS_EXECUTION",
            "exit_price": price, "pnl": pnl, "peak_price": self._peak_price or None,
            "closed_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }, update_id=self._current_trade_id)

        # Captura trade_id e símbolo antes do _reset()
        _trade_id_snap = self._current_trade_id
        _symbol_snap   = self.config.symbol

        log.info("[Bot %d] POSIÇÃO ENCERRADA via exchange | P&L: %.2f", self.config.id, pnl)

        await self._broadcast({
            "type":      "trade",
            "bot_id":    self.config.id,
            "event":     "exit",
            "price":     price,
            "pnl":       pnl,
            "direction": 0
        })

        asyncio.create_task(self._notifier.send(build_exit_msg(
            bot_name      = self.config.name,
            demo          = self.config.demo,
            symbol        = self.config.symbol,
            direction     = direction_snap,
            exit_reason   = "WS_EXECUTION",
            entry_price   = entry_price_snap,
            exit_price    = price,
            pnl           = pnl,
            daily_pnl     = self._daily_pnl,
            stop_loss_usd = self.config.stop_loss_usd,
            wins          = self._wins,
            losses        = self._losses,
        )))

        # Registra corretagem em background (CFEE aparece ~10-60s após o fill)
        if _trade_id_snap:
            asyncio.create_task(self._deferred_fee_sync(_trade_id_snap, _symbol_snap))

        self._reset()

    async def _confirm_fill_price(self, ord_id: str, exchange: BaseExchange):
        """Fallback REST polling para entry fill price caso o WebSocket não chegue a tempo.

        _handle_order_event já captura fills via trade_updates WebSocket e atualiza
        _entry_price + envia broadcast/Telegram em tempo real. Este método serve como
        segurança: tenta por ~5 minutos e desiste (o WebSocket deve ter tratado antes).
        """
        delays = [1, 3, 5, 10, 15, 30, 60]  # ~2 min total; WS deve chegar antes
        for delay in delays:
            await asyncio.sleep(delay)
            if self._direction == 0:
                return  # posição fechada
            if ord_id != self._entry_ord_id:
                return  # nova entrada sobrescreveu — não atualizar com ordem antiga
            try:
                order = await exchange.get_order(ord_id)
                if not order:
                    continue
                status = order.get("status", "")
                if status == "filled":
                    filled_avg = order.get("filled_avg_price")
                    filled_size = order.get("filled_size")
                    if filled_size:
                        actual_size = await self._entry_size_from_exchange(exchange, float(filled_size))
                        if actual_size > 0 and abs(actual_size - abs(self._sz)) > max(1e-8, abs(self._sz) * 0.001):
                            log.info(
                                "[Bot %d] Fill REST: tamanho real %.8f (estimado %.8f) — corrigindo.",
                                self.config.id, actual_size, self._sz,
                            )
                            self._sz = actual_size
                            self._sz_remaining = actual_size
                            self._save_trade({"size": actual_size}, update_id=self._current_trade_id)
                    if filled_avg:
                        actual = float(filled_avg)
                        if abs(actual - self._entry_price) > 0.001:
                            log.info(
                                "[Bot %d] Fill confirmado via REST fallback: $%.4f (estimado: $%.4f)",
                                self.config.id, actual, self._entry_price,
                            )
                            self._entry_price = actual
                            self._save_trade({"entry_price": actual}, update_id=self._current_trade_id)
                            # Broadcast apenas se o WS ainda não enviou (entry_price diferiu)
                            direction_str = "long" if self._direction == 1 else "short"
                            await self._broadcast({
                                "type":         "order_confirmed",
                                "bot_id":       self.config.id,
                                "bot_name":     self.config.name,
                                "symbol":       self.config.symbol,
                                "direction":    direction_str,
                                "order_id":     ord_id,
                                "filled_price": actual,
                                "qty":          self._sz,
                                "sl_price":     self._sl_price,
                                "tp1_price":    self._tp1_price,
                            })
                            asyncio.create_task(self._notifier.send(build_order_confirmed_msg(
                                bot_name     = self.config.name,
                                demo         = self.config.demo,
                                symbol       = self.config.symbol,
                                direction    = direction_str,
                                order_id     = ord_id,
                                filled_price = actual,
                                sz           = self._sz,
                                sl_price     = self._sl_price,
                                tp1_price    = self._tp1_price,
                            )))
                    return
                if status in ("canceled", "expired", "rejected"):
                    log.warning(
                        "[Bot %d] Ordem %s não executada (status: %s) — "
                        "removendo trade não confirmado do banco.",
                        self.config.id, ord_id, status,
                    )
                    # Remove o trade registrado antes da confirmação
                    if self._current_trade_id:
                        _db = SessionLocal()
                        try:
                            _db.query(TradeModel).filter(
                                TradeModel.id == self._current_trade_id
                            ).delete()
                            _db.commit()
                        except Exception as _de:
                            log.error("[Bot %d] Falha ao remover trade não confirmado: %s",
                                      self.config.id, _de)
                        finally:
                            _db.close()
                    await self._broadcast({
                        "type":     "order_error",
                        "bot_id":   self.config.id,
                        "bot_name": self.config.name,
                        "symbol":   self.config.symbol,
                        "error": {
                            "kind":    "order_not_filled",
                            "message": f"Ordem {ord_id} não executada pela OKX: {status}",
                        },
                    })
                    self._reset()
                    self._current_trade_id = None
                    return
            except Exception as exc:
                log.warning("[Bot %d] Erro ao confirmar fill (fallback): %s", self.config.id, exc)

    async def _sw_exit(self, price: float, exchange: BaseExchange):
        """Execute software trailing stop: place market exit, then poll REST to confirm."""
        if self._direction == 0:
            return
        close_side = "sell" if self._direction == 1 else "buy"
        close_size = await self._close_size_from_exchange(exchange)
        if close_size <= 0:
            log.warning("[Bot %d] SW-TS: tamanho de saída zero para %s.", self.config.id, self.config.symbol)
            return
        try:
            ord_id = await exchange.market_order(
                self.config.symbol, close_side, close_size, reduce_only=True)
            self._exit_ord_id = ord_id or "sw_exit"
            log.info("[Bot %d] SW-TS: ordem de saída enviada (ID: %s)", self.config.id, self._exit_ord_id)
        except Exception as e:
            log.error("[Bot %d] SW-TS: falha ao enviar ordem de saída: %s", self.config.id, e)
            self._exit_ord_id = None
            return
        # Poll REST until OKX confirms position closed; use real fill price for PnL.
        # WebSocket (trade_updates) may already call _on_position_closed first — that's fine,
        # _on_position_closed guards against double-execution via self._direction == 0.
        for attempt in range(12):
            await asyncio.sleep(2)
            if self._direction == 0:
                return  # already handled by WebSocket trade_updates
            try:
                # From attempt 2 onwards, query order directly for fill price
                if ord_id and attempt >= 1:
                    order = await exchange.get_order(ord_id)
                    if order and order.get("status") == "filled":
                        exit_price = float(order.get("filled_avg_price") or price)
                        filled_size = float(order.get("filled_size") or close_size)
                        log.info("[Bot %d] SW-TS fill real de saída: $%.4f (estimado: $%.4f)",
                                 self.config.id, exit_price, price)
                        if await self._confirm_flat_then_close(exit_price, filled_size, exchange):
                            return
                        continue
                pos = await exchange.get_position(self.config.symbol)
                if pos is None or self._net_size(pos) <= 1e-9:
                    exit_price = price
                    if ord_id:
                        try:
                            order = await exchange.get_order(ord_id)
                            if order and order.get("filled_avg_price"):
                                exit_price = float(order["filled_avg_price"])
                        except Exception:
                            pass
                    await self._on_position_closed(exit_price, close_size, exchange)
                    return
            except Exception:
                pass
        log.warning("[Bot %d] SW-TS: posição pode ainda estar aberta após saída", self.config.id)

    async def manual_liquidate(self, exchange: BaseExchange):
        """Fecha a posição a mercado e confirma via REST polling OKX."""
        if self._direction == 0: return

        last_price = self._candles[-1].close if self._candles else self._entry_price
        close_size = await self._close_size_from_exchange(exchange)

        try:
            # 1. Tenta liquidação nativa na exchange
            ord_id = await exchange.liquidate_position(self.config.symbol)
            if not ord_id:
                # 2. Fallback para ordem contrária manual se a liquidação nativa falhar ou não for suportada
                close_side = "sell" if self._direction > 0 else "buy"
                ord_id = await exchange.market_order(
                    symbol=self.config.symbol,
                    side=close_side,
                    size=close_size,
                    reduce_only=True
                )
                if not ord_id:
                    err = getattr(exchange, "last_order_error", None) or {"message": "Erro desconhecido ao liquidar."}
                    raise RuntimeError(f"Ordem de mercado falhou: {err.get('message')}")
            
            self._exit_ord_id = ord_id
            log.info("[Bot %d] Liquidação manual enviada (ID: %s). Polling REST...", self.config.id, ord_id)
        except Exception as e:
            log.warning("[Bot %d] Falha ao liquidar na exchange: %s", self.config.id, e)
            self._save_trade({
                "type": "exit", "event": "MANUAL_FAILED",
                "exit_price": self._entry_price,
                "pnl": 0.0, "peak_price": self._peak_price or None,
            }, update_id=self._current_trade_id)
            self._reset()
            return

        # Polling REST até confirmar fechamento (mesmo padrão do _sw_exit).
        # WebSocket pode chegar primeiro — _on_position_closed guarda contra duplicação.
        for attempt in range(12):
            await asyncio.sleep(2)
            if self._direction == 0:
                return  # already handled by WebSocket trade_updates
            try:
                if ord_id and attempt >= 1:
                    order = await exchange.get_order(ord_id)
                    if order and order.get("status") == "filled":
                        exit_price = float(order.get("filled_avg_price") or last_price)
                        filled_size = float(order.get("filled_size") or close_size)
                        if await self._confirm_flat_then_close(exit_price, filled_size, exchange):
                            return
                        continue
                pos = await exchange.get_position(self.config.symbol)
                if pos is None or self._net_size(pos) <= 1e-9:
                    exit_price = last_price
                    if ord_id:
                        try:
                            order = await exchange.get_order(ord_id)
                            if order and order.get("filled_avg_price"):
                                exit_price = float(order["filled_avg_price"])
                        except Exception:
                            pass
                    await self._on_position_closed(exit_price, close_size, exchange)
                    return
            except Exception:
                pass
        log.warning("[Bot %d] Liquidação manual: posição pode ainda estar aberta", self.config.id)
