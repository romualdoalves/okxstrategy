"""
P&L diário/circuit breaker e persistência (trades, rejeições, signal logs, recuperação no restart).
"""

from __future__ import annotations

import asyncio
from typing import Optional
from datetime import datetime

from ..database import OrderRejectionModel, SessionLocal, SignalLogModel, TradeModel
from ..risk import current_day_start_utc as _current_day_start_utc
from ..notifications import build_circuit_breaker_msg
from .common import log


class AccountingMixin:
    # ── P&L ──────────────────────────────────────────────────────────────────
    def _roll_day(self) -> None:
        """Na virada do dia local zera o P&L diário e libera o circuit breaker de perda diária.
        Halts críticos (ex.: posição sem Stop Loss) NÃO são liberados automaticamente."""
        today = _current_day_start_utc()
        if today == self._pnl_day:
            return
        log.info("[Bot %d] Novo dia: P&L diário %.2f zerado.", self.config.id, self._daily_pnl)
        self._pnl_day = today
        self._daily_pnl = 0.0
        if self._halted and self._halt_kind == "daily_loss":
            self._halted = False
            self._halt_kind = None
            self._hold_reason = ""
            log.warning("[Bot %d] Circuit breaker diário liberado: bot retomado.", self.config.id)

    def _record_pnl(self, pnl: float):
        self._roll_day()
        self._daily_pnl += pnl
        if pnl > 0:
            self._wins += 1
        else:
            self._losses += 1
        if self._daily_pnl <= self.config.stop_loss_usd:
            if not self._halted:
                self._halt_kind = "daily_loss"
            self._halted = True
            log.warning("[Bot %d] CIRCUIT BREAKER: P&L diário %.2f",
                        self.config.id, self._daily_pnl)
            asyncio.create_task(self._notifier.send(build_circuit_breaker_msg(
                bot_name      = self.config.name,
                demo          = self.config.demo,
                symbol        = self.config.symbol,
                daily_pnl     = self._daily_pnl,
                stop_loss_usd = self.config.stop_loss_usd,
                wins          = self._wins,
                losses        = self._losses,
            )))

    def _reset(self):
        self._direction = self._sz = self._sz_remaining = 0
        self._entry_price = self._tp1_price = self._sl_price = 0.0
        self._tp1_done = False
        self._ts_pct = 0.0
        self._sl_algo_id = self._ts_algo_id = None
        self._ts_callback_ratio = 0.0
        self._peak_price = 0.0
        self._last_persisted_sl = 0.0

    def _record_order_rejection(
        self,
        *,
        side: str | None = None,
        order_type: str | None = None,
        reason: str | None = None,
        ord_id: str | None = None,
        algo_id: str | None = None,
        status: str = "open",
        raw_payload: dict | None = None,
    ) -> None:
        db = SessionLocal()
        try:
            db.add(OrderRejectionModel(
                bot_id=self.config.id,
                bot_name=self.config.name,
                symbol=self.config.symbol,
                side=side,
                order_type=order_type,
                ord_id=ord_id,
                algo_id=algo_id,
                status=status,
                reason=reason,
                raw_payload=raw_payload or {},
            ))
            db.commit()
        except Exception as exc:
            log.warning("[Bot %d] Falha ao persistir rejeição de ordem: %s", self.config.id, exc)
        finally:
            db.close()

    def _save_trade(self, data: dict, update_id: Optional[int] = None) -> Optional[int]:
        db = SessionLocal()
        try:
            # Para trades de saída (exit), criamos um NOVO registro vinculado ao trade de entrada
            # para que o histórico completo (entry → tp1 → exit) apareça no gráfico
            if data.get("type") == "exit":
                # Busca o trade de entrada aberto para copiar dados base
                entry_trade = None
                if update_id:
                    entry_trade = db.query(TradeModel).filter(TradeModel.id == update_id).first()
                if not entry_trade:
                    entry_trade = db.query(TradeModel).filter(
                        TradeModel.bot_id == self.config.id,
                        TradeModel.exit_price.is_(None)
                    ).order_by(TradeModel.id.desc()).first()
                
                if entry_trade:
                    # Atualiza o trade de entrada com o preço de saída e PnL
                    # para que o summary possa calcular corretamente (filtra entry + exit_price not null)
                    entry_trade.exit_price = data.get("exit_price")
                    entry_trade.pnl = data.get("pnl")
                    entry_trade.closed_at = data.get("closed_at")
                    entry_trade.event = data.get("event", entry_trade.event)
                    db.commit()
                    
                    # Cria um novo registro de saída para aparecer no gráfico/histórico
                    exit_trade = TradeModel(
                        bot_id=self.config.id,
                        symbol=self.config.symbol,
                        type="exit",
                        event=data.get("event", "EXIT"),
                        direction=entry_trade.direction,
                        size=entry_trade.size,
                        entry_price=entry_trade.entry_price,
                        exit_price=data.get("exit_price"),
                        sl_price=entry_trade.sl_price,
                        tp1_price=entry_trade.tp1_price,
                        pnl=data.get("pnl"),
                        closed_at=data.get("closed_at"),
                        source=data.get("source", "bot"),
                    )
                    db.add(exit_trade)
                    db.commit()
                    db.refresh(exit_trade)
                    return exit_trade.id
            
            # Para TP1 (Dynamic Shadow Trigger), criamos um registro separado
            if data.get("type") == "tp1":
                entry_trade = None
                if update_id:
                    entry_trade = db.query(TradeModel).filter(TradeModel.id == update_id).first()
                if not entry_trade:
                    entry_trade = db.query(TradeModel).filter(
                        TradeModel.bot_id == self.config.id,
                        TradeModel.exit_price.is_(None)
                    ).order_by(TradeModel.id.desc()).first()
                
                if entry_trade:
                    # Marca a sombra como ativa no registro de entrada para sobreviver a restarts
                    entry_trade.tp1_done = True
                    # Cria registro separado de TP1 para o gráfico
                    tp1_trade = TradeModel(
                        bot_id=self.config.id,
                        symbol=self.config.symbol,
                        type="tp1",
                        event=data.get("event", "TP1"),
                        direction=entry_trade.direction,
                        size=entry_trade.size,
                        entry_price=entry_trade.entry_price,
                        # Usa o nível inicial do trailing stop (passado via data["sl_price"])
                        # em vez do SL estático da entrada — mais preciso para análise histórica.
                        sl_price=data.get("sl_price", entry_trade.sl_price),
                        tp1_price=entry_trade.tp1_price,
                        pnl=data.get("pnl", 0.0),
                        source=data.get("source", "bot"),
                    )
                    db.add(tp1_trade)
                    db.commit()
                    db.refresh(tp1_trade)
                    return tp1_trade.id

            if update_id:
                # Tenta atualizar um trade existente
                trade = db.query(TradeModel).filter(TradeModel.id == update_id).first()
                if trade:
                    for k, v in data.items():
                        setattr(trade, k, v)
                    db.commit()
                    return trade.id

            # Criação de um novo registro (normalmente para "entry")
            trade = TradeModel(bot_id=self.config.id,
                               symbol=self.config.symbol, **data)
            db.add(trade)
            db.commit()
            db.refresh(trade)
            return trade.id
        except Exception as e:
            log.error("Erro ao salvar trade: %s", e)
            db.rollback()
            return None
        finally:
            db.close()

    def _recover_state(self):
        """Tenta recuperar o estado de uma posição aberta do banco de dados."""
        db = SessionLocal()
        try:
            # wins/losses (histórico) e o P&L do dia vêm do banco (tabela trades), não da
            # memória: um restart do backend NÃO deve zerar o histórico do bot nem rearmar
            # o circuit breaker — só o Reset Geral (que apaga os trades) zera o histórico.
            closed = db.query(TradeModel).filter(
                TradeModel.bot_id == self.config.id,
                TradeModel.type == "exit",
                TradeModel.pnl.isnot(None),
            ).all()
            if closed:
                self._pnl_day   = _current_day_start_utc()
                self._daily_pnl = sum(t.pnl for t in closed
                                      if t.timestamp and t.timestamp >= self._pnl_day)
                self._wins      = sum(1 for t in closed if t.pnl > 0)
                self._losses    = sum(1 for t in closed if t.pnl <= 0)
                log.info("[Bot %d] Recuperado do banco: P&L do dia %.2f (histórico %dW/%dL)",
                         self.config.id, self._daily_pnl, self._wins, self._losses)
                if self._daily_pnl <= self.config.stop_loss_usd and not self._halted:
                    self._halted = True
                    self._halt_kind = "daily_loss"
                    log.warning("[Bot %d] Circuit breaker diário ainda ativo após restart.", self.config.id)

            # Usa o registro de ENTRADA (type="entry") como fonte da verdade para recuperação.
            # Antes usávamos order_by(id.desc()) que retornava o registro TP1 — cujo sl_price
            # era copiado do entry no momento do trigger e nunca atualizado posteriormente.
            # O entry record tem sl_price atualizado pelo _persist_trailing_stop_level.
            last_trade = db.query(TradeModel).filter(
                TradeModel.bot_id == self.config.id,
                TradeModel.exit_price.is_(None),
                TradeModel.type == "entry",
            ).order_by(TradeModel.id.desc()).first()
            
            if last_trade:
                self._direction    = 1 if last_trade.direction == "LONG" else -1
                self._entry_price  = last_trade.entry_price or 0.0
                self._sz           = last_trade.size or 0.0
                self._sz_remaining = self._sz
                self._tp1_price    = last_trade.tp1_price or 0.0
                self._sl_price     = last_trade.sl_price or 0.0
                self._current_trade_id = last_trade.id
                # A ordem real será sincronizada com a OKX no próximo ciclo fechado.
                self._tp1_done          = bool(last_trade.tp1_done)
                self._sl_algo_id        = None
                self._ts_algo_id        = None
                self._ts_callback_ratio = 0.005
                if self._sl_price > 0 and self._ts_callback_ratio > 0:
                    if self._direction == 1:
                        self._peak_price = self._sl_price / (1 - self._ts_callback_ratio)
                    else:
                        self._peak_price = self._sl_price / (1 + self._ts_callback_ratio)
                # Marca o nível já persistido para evitar re-escrita desnecessária
                self._last_persisted_sl = self._sl_price
                log.info("[Bot %d] Estado recuperado do banco: %s @ %.2f (Trade ID: %d) — aguardando sync OKX (sl=%.4f peak=%.4f tp1_done=%s)",
                         self.config.id, last_trade.direction, self._entry_price,
                         self._current_trade_id, self._sl_price, self._peak_price,
                         self._tp1_done)
        except Exception as e:
            log.error("[Bot %d] Falha ao recuperar estado: %s", self.config.id, e)
        finally:
            db.close()

    def _save_signal_log(self, result, bar) -> Optional[int]:
        db = SessionLocal()
        try:
            from datetime import timezone as _tz
            ts = datetime.fromtimestamp(bar.epoch / 1000, tz=_tz.utc).replace(tzinfo=None)
            entry = SignalLogModel(
                bot_id=self.config.id,
                timestamp=ts,
                signal=result.signal.value,
                hold_reason=result.hold_reason or None,
                indicators=self._to_json(dict(result.indicators)) if result.indicators else {},
                meta=self._to_json(dict(result.metadata)) if result.metadata else {},
                candle_open=bar.open,
                candle_high=bar.high,
                candle_low=bar.low,
                candle_close=bar.close,
                candle_volume=bar.volume,
            )
            db.add(entry)
            db.commit()
            db.refresh(entry)
            return entry.id
        except Exception as e:
            log.error("Erro ao salvar signal_log: %s", e)
            db.rollback()
            return None
        finally:
            db.close()

    def _link_signal_to_trade(self, signal_log_id: int, trade_id: int):
        db = SessionLocal()
        try:
            entry = db.get(SignalLogModel, signal_log_id)
            if entry:
                entry.resulted_in_trade_id = trade_id
                db.commit()
        except Exception as e:
            log.error("Erro ao linkar signal_log→trade: %s", e)
            db.rollback()
        finally:
            db.close()
