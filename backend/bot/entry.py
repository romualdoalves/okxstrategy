"""
Avaliação do sinal, entrada a mercado e proteção (Stop Loss nativo, fechamento emergencial).
"""

from __future__ import annotations

import asyncio
from typing import Optional
from datetime import datetime, timezone

from ..exchanges.base import BaseExchange
from ..strategies.base import Signal
from ..notifications import build_entry_msg, build_order_failed_msg
from ..feeds.economic_calendar import calendar_feed
from ..risk import guard as risk_guard, compute_stake
from ..stop_policy import cap_initial_stop
from .common import log, manager


class EntryMixin:
    async def _place_stop_loss_with_retry(
        self,
        exchange: BaseExchange,
        side: str,
        size: float,
        trigger_price: float,
        reference_price: float,
    ) -> tuple[Optional[str], float]:
        """Cria SL nativo com pequenos ajustes de distância antes de desistir."""
        trigger_price = float(trigger_price or 0.0)
        reference_price = float(reference_price or trigger_price or 0.0)
        if trigger_price <= 0 or size <= 0:
            return None, trigger_price

        candidates = [trigger_price]
        if reference_price > 0:
            if side == "sell":
                candidates.extend([
                    min(trigger_price, reference_price * 0.995),
                    min(trigger_price, reference_price * 0.990),
                    min(trigger_price, reference_price * 0.985),
                ])
            else:
                candidates.extend([
                    max(trigger_price, reference_price * 1.005),
                    max(trigger_price, reference_price * 1.010),
                    max(trigger_price, reference_price * 1.015),
                ])

        seen: set[float] = set()
        for candidate in candidates:
            candidate = round(float(candidate), 8)
            if candidate <= 0 or candidate in seen:
                continue
            seen.add(candidate)
            algo_id = await exchange.place_stop_loss(self.config.symbol, side, size, candidate)
            if algo_id:
                if abs(candidate - trigger_price) > max(1e-12, trigger_price * 0.00001):
                    log.warning(
                        "[Bot %d] SL inicial aceito pela OKX após ajuste: %.8f -> %.8f",
                        self.config.id,
                        trigger_price,
                        candidate,
                    )
                return algo_id, candidate
            err = getattr(exchange, "last_order_error", None) or {}
            log.warning(
                "[Bot %d] Tentativa de Stop Loss recusada em %.8f: %s",
                self.config.id,
                candidate,
                err.get("message", err),
            )
        return None, trigger_price

    async def _emergency_close_unprotected_entry(
        self,
        exchange: BaseExchange,
        side: str,
        size: float,
        reason: str,
    ) -> bool:
        """Fecha a posição recém-aberta quando a OKX recusa todas as proteções."""
        try:
            ord_id = await exchange.market_order(self.config.symbol, side, size, reduce_only=True)
            if not ord_id:
                return False
            for _ in range(12):
                try:
                    pos = await exchange.get_position(self.config.symbol)
                    if pos is None or self._net_size(pos) <= 1e-9:
                        log.critical(
                            "[Bot %d] Entrada sem proteção encerrada a mercado. Ordem: %s Motivo: %s",
                            self.config.id,
                            ord_id,
                            reason,
                        )
                        if self._direction != 0:
                            close_price = self._candles[-1].close if self._candles else (self._entry_price or 0.0)
                            await self._on_position_closed(close_price, size, exchange)
                        else:
                            self._reset()
                        return True
                except Exception:
                    pass
                await asyncio.sleep(0.5)
            log.critical(
                "[Bot %d] Encerramento emergencial enviado, mas OKX não confirmou FLAT no polling.",
                self.config.id,
            )
            return False
        except Exception as exc:
            log.critical("[Bot %d] Falha no encerramento emergencial sem SL: %s", self.config.id, exc)
            return False

    async def _sync_protective_algo_from_exchange(self, exchange: BaseExchange) -> bool:
        close_side = "sell" if self._direction == 1 else "buy"
        try:
            algo = await exchange.find_protective_algo(self.config.symbol, close_side)
        except Exception as exc:
            log.warning("[Bot %d] Falha ao buscar proteção pendente na OKX: %s", self.config.id, exc)
            return False
        if not algo:
            return False

        algo_id = algo.get("algoId")
        ord_type = algo.get("ordType")
        if ord_type == "move_order_stop":
            self._ts_algo_id = algo_id
            self._sl_algo_id = None
        else:
            self._sl_algo_id = algo_id
            if not self._tp1_done:
                self._ts_algo_id = None

        move_px = float(algo.get("moveTriggerPx") or 0)
        sl_px = float(algo.get("slTriggerPx") or 0)
        real_sl = move_px if move_px > 0 else sl_px
        if real_sl > 0:
            self._sl_price = real_sl
            self._last_persisted_sl = real_sl
        cb_ratio = float(algo.get("callbackRatio") or 0)
        if cb_ratio > 0:
            self._ts_callback_ratio = cb_ratio
        log.info(
            "[Bot %d] Proteção OKX sincronizada: %s %s stop=%s",
            self.config.id,
            ord_type,
            algo_id,
            real_sl or "-",
        )
        return True

    async def force_sync_trailing(self, exchange: BaseExchange):
        """Força a busca do ID e do preço de stop real na exchange imediatamente."""
        # Se não há posição, não há o que sincronizar
        if self._direction == 0:
            return
        # Bloco de trailing stop por software foi descontinuado

        try:
            await self._sync_protective_algo_from_exchange(exchange)

            if not self._tp1_done and not self._sl_algo_id:
                close_side = "sell" if self._direction == 1 else "buy"
                sl_id, sl_px = await self._place_stop_loss_with_retry(
                    exchange,
                    close_side,
                    self._sz,
                    self._sl_price,
                    self._candles[-1].close if self._candles else self._entry_price,
                )
                if sl_id:
                    self._sl_algo_id = sl_id
                    self._sl_price = sl_px
                    self._persist_trailing_stop_level()
                    log.critical(
                        "[AUDITORIA Bot %d] SL ausente recriado na OKX: %s @ %.4f",
                        self.config.id,
                        sl_id,
                        sl_px,
                    )
                    return
                err = getattr(exchange, "last_order_error", None) or {}
                self._record_order_rejection(
                    side=close_side,
                    order_type="stop_loss_resync",
                    reason=err.get("message", "Stop Loss ausente não pôde ser recriado."),
                    status="critical",
                    raw_payload=err,
                )
                if await self._emergency_close_unprotected_entry(
                    exchange,
                    close_side,
                    self._sz,
                    err.get("message", "Stop Loss ausente não pôde ser recriado."),
                ):
                    return
                self._halted = True
                self._halt_kind = "critical"
                self._hold_reason = "CRÍTICO: posição aberta sem Stop Loss confirmado na OKX."
                return

            # ⚠️ RECUPERAÇÃO AGRESSIVA: Se TP1 foi acionado mas nenhuma proteção existe, força recuperação
            if self._tp1_done and not self._ts_algo_id and not self._sl_algo_id:
                log.critical("[AUDITORIA Bot %d] ⚠️ TP1 ACIONADO MAS SEM PROTEÇÃO! Recuperando agressivamente...", self.config.id)
                current_price = self._candles[-1].close if self._candles else self._entry_price
                cb = self._trailing_callback(current_price)
                
                new_ts_id = await exchange.place_trailing_stop(
                    self.config.symbol,
                    "sell" if self._direction == 1 else "buy",
                    self._sz,
                    cb,
                    current_price
                )
                if new_ts_id:
                    self._ts_algo_id = new_ts_id
                    self._ts_callback_ratio = cb
                    log.critical("[AUDITORIA Bot %d] ✅ Proteção de emergência criada: %s @ %.2f%%", 
                                self.config.id, new_ts_id, cb*100)
                else:
                    log.error("[AUDITORIA Bot %d] ❌ Proteção de emergência FALHOU. Tentando fallback SL...", self.config.id)
                    fallback_stop = (
                        max(self._entry_price, current_price * (1 - cb))
                        if self._direction == 1
                        else min(self._entry_price, current_price * (1 + cb))
                    )
                    fallback_id = await exchange.place_stop_loss(
                        self.config.symbol,
                        "sell" if self._direction == 1 else "buy",
                        self._sz,
                        fallback_stop
                    )
                    if fallback_id:
                        self._sl_algo_id = fallback_id
                        self._sl_price = fallback_stop
                        log.critical("[AUDITORIA Bot %d] ✅ Fallback SL de emergência criado: %s @ %.4f", 
                                    self.config.id, fallback_id, fallback_stop)

            # 1. Recupera o ID do trailing se não tivermos um — só faz sentido procurar
            # depois do TP1: antes disso, _ts_algo_id vazio é o estado CORRETO (a proteção
            # é o SL fixo em _sl_algo_id, não um move_order_stop). Sem esse guard, todo bot
            # ainda no SL fixo (a maior parte da vida de qualquer trade) loga "nenhuma ordem
            # encontrada" a cada ciclo — ruído falso que mascara alarmes reais.
            if self._tp1_done and (not self._ts_algo_id or self._ts_algo_id == "recovered_after_restart"):
                params = {"ordType": "move_order_stop", "instId": self.config.symbol}
                data = await exchange._get("/api/v5/trade/orders-algo-pending", params)
                orders = data.get("data", [])
                if orders:
                    self._ts_algo_id = orders[0]["algoId"]
                    log.info("[AUDITORIA Bot %d] ID recuperado: %s", self.config.id, self._ts_algo_id)
                else:
                    log.warning("[AUDITORIA Bot %d] TP1 já ocorreu mas nenhuma ordem de trailing encontrada na exchange. Verificando necessidade de criar nova...", self.config.id)
                    # Não damos return aqui. Seguimos para a lógica de reset/criação abaixo.

            # 2. Sincronização: busca o stop real na exchange e rastreia se a ordem existe
            pnl_pct = (self._candles[-1].close - self._entry_price) / self._entry_price * self._direction if self._candles else 0
            order_confirmed_on_exchange = False
            is_unprotected = True

            if self._ts_algo_id and self._ts_algo_id != "recovered_after_restart":
                algo = await exchange.get_algo_order(self.config.symbol, self._ts_algo_id)
                if algo:
                    order_confirmed_on_exchange = True
                    # move_order_stop usa moveTriggerPx (preço corrente do trailing)
                    # conditional usa slTriggerPx (SL fixo) — fallback para compatibilidade
                    move_px = float(algo.get("moveTriggerPx") or 0)
                    sl_px   = float(algo.get("slTriggerPx") or 0)
                    real_sl = move_px if move_px > 0 else sl_px
                    if real_sl > 0:
                        self._sl_price = real_sl
                        log.info("[AUDITORIA Bot %d] Stop oficial sincronizado: %.4f", self.config.id, real_sl)
                    cb_ratio = float(algo.get("callbackRatio") or 0)
                    if cb_ratio > 0:
                        self._ts_callback_ratio = cb_ratio
                        log.info("[AUDITORIA Bot %d] Campos do stop: moveTriggerPx=%s slTriggerPx=%s callbackRatio=%s activePx=%s state=%s",
                             self.config.id,
                             algo.get("moveTriggerPx"), algo.get("slTriggerPx"),
                             algo.get("callbackRatio"), algo.get("activePx"), algo.get("state"))

                    # CONDIÇÃO DE PROTEÇÃO: só recria se a exchange confirma que não há ordem ativa.
            # NÃO cancela com base em _sl_price local — evita loop de cancel/recriação.
                    is_unprotected = not order_confirmed_on_exchange

            if self._tp1_done and is_unprotected:
                log.critical("[AUDITORIA Bot %d] PROTEÇÃO EXIGIDA! Lucro %.2f%% sem ordem ativa ou com stop travado.",
                             self.config.id, pnl_pct*100)
                
                # Cancela qualquer lixo que tenha sobrado
                if self._ts_algo_id and self._ts_algo_id != "recovered_after_restart":
                    try:
                        cancel_ok = await exchange.cancel_algo(self.config.symbol, self._ts_algo_id)
                        if cancel_ok:
                            log.info("[AUDITORIA Bot %d] Trailing anterior cancelado antes de criar novo.", self.config.id)
                        else:
                            log.warning("[AUDITORIA Bot %d] Falha ao cancelar trailing anterior, mesmo assim criando novo.", self.config.id)
                    except Exception as e:
                        log.error("[AUDITORIA Bot %d] Exceção ao cancelar: %s", self.config.id, e)
                
                # Cria a nova ordem de proteção
                current_price = self._candles[-1].close if self._candles else self._entry_price
                cb = self._trailing_callback(current_price)
                
                new_id = await exchange.place_trailing_stop(
                    self.config.symbol, "sell" if self._direction == 1 else "buy", 
                    self._sz, cb, current_price)
                
                if new_id:
                    self._ts_algo_id = new_id
                    self._ts_callback_ratio = cb
                    log.critical("[AUDITORIA Bot %d] PROTEÇÃO CRIADA! Nova ordem: %s", self.config.id, new_id)
                else:
                    err = getattr(exchange, "last_order_error", None) or {}
                    self._record_order_rejection(
                        side="sell" if self._direction == 1 else "buy",
                        order_type="trailing_stop_resync",
                        reason=err.get("message", "Exchange recusou recriação do trailing stop."),
                        status="fallback_attempted",
                        raw_payload=err,
                    )
                    fallback_stop = (
                        max(self._entry_price, current_price * (1 - cb))
                        if self._direction == 1
                        else min(self._entry_price, current_price * (1 + cb))
                    )
                    fallback_id = await exchange.place_stop_loss(
                        self.config.symbol,
                        "sell" if self._direction == 1 else "buy",
                        self._sz,
                        fallback_stop,
                    )
                    if fallback_id:
                        self._sl_algo_id = fallback_id
                        self._sl_price = fallback_stop
                        self._persist_trailing_stop_level()
                        log.critical(
                            "[AUDITORIA Bot %d] FALLBACK SL FIXO CRIADO: %s @ %.4f",
                            self.config.id,
                            fallback_id,
                            fallback_stop,
                        )
                    else:
                        fallback_err = getattr(exchange, "last_order_error", None) or {}
                        self._record_order_rejection(
                            side="sell" if self._direction == 1 else "buy",
                            order_type="stop_loss_fallback_resync",
                            reason=fallback_err.get("message", "Fallback de Stop Loss também recusado."),
                            status="critical",
                            raw_payload=fallback_err,
                        )
                        log.error("[AUDITORIA Bot %d] ERRO FATAL: trailing e fallback SL foram recusados.", self.config.id)
        except Exception as e:
            log.warning("[Bot %d] Erro no Sync Manual: %s", self.config.id, e)

    async def _process_strategy(self, bar, exchange: BaseExchange, session=None):
        # Atualiza grafo se a estratégia precisar
        if getattr(self.strategy, 'needs_graph_context', False) and session:
            await self._update_graph_context(session)
            # Carimba o epoch da vela fechada — frontend usa para habilitar o botão
            if self._graph_state:
                self._graph_state['last_candle_ts'] = bar.epoch

            # Alimenta o slot de sentiment com a surpresa do calendário económico.
            if hasattr(self.strategy, 'update_sentiment'):
                surprise = calendar_feed.get_surprise_score()
                if surprise is not None:
                    self.strategy.update_sentiment(surprise)
                    log.info("[Bot %d] Calendar sentiment aplicado ao grafo: %+.3f",
                             self.config.id, surprise)

        # Atualiza candles extras para estratégias multi-TF
        if self.strategy.__class__.extra_timeframes():
            await self._update_extra_candles(exchange)

        if getattr(self.strategy, 'needs_onchain_context', False) and session:
            await self._update_onchain_context(session)

        if getattr(self.strategy, 'needs_dex_context', False) and session:
            await self._update_dex_context(session, exchange)

        if getattr(self.strategy, 'needs_gex_context', False) and session:
            await self._update_gex_context(session)

        if getattr(self.strategy, 'needs_market_players_context', False) and session:
            await self._update_market_players_context(session)

        ctx    = self._strategy_context()
        result = self.strategy.compute_with_context(self._candles, ctx)
        if result is None:
            return

        self._current_atr     = result.indicators.get("atr", self._current_atr)
        self._hold_reason     = result.hold_reason
        self._last_indicators = self._runtime_indicators(result)

        # Persiste snapshot completo do sinal para análise posterior
        self._last_signal_log_id = self._save_signal_log(result, bar)

        signal_direction = (
            "long" if result.signal == Signal.BUY
            else "short" if result.signal == Signal.SELL
            else None
        )
        order_criteria = await self._evaluate_order_criteria(
            result=result,
            direction=signal_direction,
            price=bar.close,
            exchange=exchange,
            check_exchange_position=signal_direction is not None,
        )

        # Atualiza o pico de preço favorável desde a entrada — independe de já estar em
        # modo sombra/trailing, é a base da métrica de efetividade (% do movimento
        # favorável capturado na saída).
        if self._direction != 0:
            if self._direction == 1:
                if self._peak_price == 0 or bar.close > self._peak_price:
                    self._peak_price = bar.close
            else:
                if self._peak_price == 0 or bar.close < self._peak_price:
                    self._peak_price = bar.close

        # Cálculo de Lucro Garantido (Sombra) — só existe depois que o trailing ativa
        guaranteed_pnl = 0.0
        if self._direction != 0 and self._ts_algo_id:
            # Cálculo do lucro garantido baseado no stop real confirmado pela exchange
            if self._sl_price > 0:
                diff = (self._sl_price - self._entry_price) / self._entry_price * self._direction
                guaranteed_pnl = max(0, diff) * 100

        # Broadcast de indicadores
        await self._broadcast({
            "type":        "indicators",
            "bot_id":      self.config.id,
            "indicators":  self._last_indicators,
            "signal":      result.signal,
            "hold_reason": result.hold_reason,
            "criteria_met": getattr(result, "criteria_met", 0),
            "criteria_total": getattr(result, "criteria_total", 0),
            "order_criteria": order_criteria,
            "guaranteed_pnl": round(guaranteed_pnl, 2),
            "execution_cycle": {
                "timeframe": self.config.timeframe,
                "closed_candles": self._closed_candle_count,
                "last_closed_at": self._last_closed_candle_at,
            },
            "ts":          bar.epoch,
        })

        if self._halted:
            self._hold_reason = "Circuit Breaker: Bot pausado (Stop Loss atingido)"
            return
        if self._direction != 0:
            # Contrato (spot long-only): SELL da estratégia = sair da posição long.
            # Mesmo comportamento do backtest estrito (fecha no candle do sinal).
            if self._direction == 1 and result.signal == Signal.SELL:
                self._hold_reason = "Saída por sinal SELL da estratégia"
                log.info("[Bot %d] SINAL SELL = SAÍDA | %s", self.config.id, result.indicators)
                await self._sw_exit(bar.close, exchange)
                return
            self._hold_reason = f"Em operação: { 'LONG' if self._direction == 1 else 'SHORT' }"
            return

        if signal_direction and not order_criteria.get("ready"):
            log.info(
                "[Bot %d] SINAL %s BLOQUEADO POR CRITÉRIO DE ORDEM: %s",
                self.config.id,
                signal_direction.upper(),
                order_criteria.get("reason", "bloqueio operacional"),
            )
            return

        if result.signal == Signal.BUY:
            log.info("[Bot %d] SINAL LONG | %s", self.config.id, result.indicators)
            await self._enter("long", bar.close, result, exchange,
                              self._last_signal_log_id)
        elif result.signal == Signal.SELL:
            log.info("[Bot %d] SINAL SHORT | %s", self.config.id, result.indicators)
            await self._enter("short", bar.close, result, exchange,
                              self._last_signal_log_id)

    # ── Entrada ───────────────────────────────────────────────────────────────
    async def _enter(self, direction: str, price: float, result, exchange: BaseExchange,
                     signal_log_id: Optional[int] = None):
        order_gate = await self._evaluate_order_criteria(
            result=result,
            direction=direction,
            price=price,
            exchange=exchange,
            check_exchange_position=True,
        )
        if not order_gate.get("ready"):
            log.info(
                "[Bot %d] Entrada %s não enviada — critério de ordem bloqueou: %s",
                self.config.id,
                direction.upper(),
                order_gate.get("reason", "bloqueio operacional"),
            )
            await self._broadcast({
                "type":        "indicators",
                "bot_id":      self.config.id,
                "indicators":  self._runtime_indicators(result),
                "signal":      "hold",
                "hold_reason": order_gate.get("reason", self._hold_reason),
                "criteria_met": getattr(result, "criteria_met", 0),
                "criteria_total": getattr(result, "criteria_total", 0),
                "order_criteria": order_gate,
                "execution_cycle": {
                    "timeframe": self.config.timeframe,
                    "closed_candles": self._closed_candle_count,
                    "last_closed_at": self._last_closed_candle_at,
                },
                "ts":          int(datetime.now(timezone.utc).timestamp() * 1000),
            })
            return

        self._entry_inflight = True
        # Bloqueia Short em SPOT (não há margem/borrow no modo cash)
        is_deriv = ("-SWAP" in self.config.symbol.upper() or "-FUTURES" in self.config.symbol.upper())
        if direction == "short" and not is_deriv:
            self._hold_reason = "Mercado SPOT: Apenas sinais de Compra permitidos."
            self._entry_inflight = False
            log.warning("[Bot %d] Bloqueio SPOT: Tentativa de Short em mercado à vista.", self.config.id)
            await self._broadcast({
                "type":        "indicators",
                "bot_id":      self.config.id,
                "indicators":  self._runtime_indicators(result),
                "signal":      "hold",
                "hold_reason": self._hold_reason,
                "criteria_met": 2, 
                "criteria_total": 3,
                "order_criteria": self._last_order_criteria,
                "execution_cycle": {
                    "timeframe": self.config.timeframe,
                    "closed_candles": self._closed_candle_count,
                    "last_closed_at": self._last_closed_candle_at,
                },
                "ts":          int(datetime.now(timezone.utc).timestamp() * 1000),
            })
            return
        # ── Gate de risco global da conta (backend/risk.py) ───────────────────
        # Síncrono e sem await desde o `_entry_inflight = True` acima: dois bots não
        # passam juntos pelo mesmo limite. Só bloqueia ENTRADAS; saídas nunca passam aqui.
        sizing = risk_guard.sizing_limits()
        # No modo "risk" o stake real só é conhecido depois do SL; o gate usa o teto (pior caso).
        gate_stake = sizing.max_stake_usd if sizing.sizing_mode == "risk" else self.config.stake_usd
        self._planned_stake_usd = gate_stake
        decision = risk_guard.check_entry(
            manager._instances, self.config.id, gate_stake, self.config.leverage, symbol=self.config.symbol)
        if not decision.allowed:
            log.warning("[Bot %d] ENTRADA BLOQUEADA — %s", self.config.id, decision.reason)
            self._hold_reason = decision.reason
            self._entry_inflight = False
            if decision.first_time:
                asyncio.create_task(self._notifier.send(
                    f"⛔ *Risco global* — entrada bloqueada\n{decision.reason}\n"
                    f"Bot: {self.config.name} ({self.config.symbol})"))
            await self._broadcast({
                "type":        "indicators",
                "bot_id":      self.config.id,
                "indicators":  self._runtime_indicators(result),
                "signal":      "hold",
                "hold_reason": self._hold_reason,
                "order_criteria": self._last_order_criteria,
                "ts":          int(datetime.now(timezone.utc).timestamp() * 1000),
            })
            return
        # ── Gate do calendário económico ──────────────────────────────────────
        # Suspende entradas nas estratégias de prioridade crítica durante janelas
        # de eventos macro High-impact (Bollinger Breakout e Graph Regime são as
        # mais expostas a fake-outs gerados por anúncios do Fed, CPI, Payroll, etc.)
        in_window, event_name = calendar_feed.is_high_impact_window(self.config.strategy_id)
        if in_window:
            log.info("[Bot %d] ENTRADA BLOQUEADA — calendário: '%s'",
                     self.config.id, event_name)
            self._hold_reason = f"Calendário: {event_name}"
            self._entry_inflight = False
            await self._broadcast({
                "type":        "indicators",
                "bot_id":      self.config.id,
                "indicators":  self._last_indicators,
                "signal":      Signal.HOLD,
                "hold_reason": self._hold_reason,
                "order_criteria": self._last_order_criteria,
                "ts":          int(datetime.now(timezone.utc).timestamp() * 1000),
            })
            return
        # ─────────────────────────────────────────────────────────────────────

        atr = self._get_dynamic_atr(result, price * 0.005)
        sz  = exchange.num_contracts(
            self.config.symbol, price, self.config.stake_usd, self.config.leverage)

        sl_px  = result.metadata.get("sl_price",
            price - atr * 1.5 if direction == "long" else price + atr * 1.5)
        tp1_px = result.metadata.get("tp1_price",
            price * 1.02 if direction == "long" else price * 0.98)

        # Proteção Estatística: O stop não pode ser menor que 1.5x o ATR atual (para evitar ruído)
        min_sl_dist_abs = atr * 1.5
        current_sl_dist = abs(price - sl_px)
        
        if current_sl_dist < min_sl_dist_abs:
            log.warning(f"[Bot {self.config.id}] SL Original apertado ({current_sl_dist/price*100:.2f}%). Ajustando para o mínimo dinâmico baseado em ATR.")
            if direction == "long":
                sl_px = price - min_sl_dist_abs
            else:
                sl_px = price + min_sl_dist_abs
                
            original_tp_dist = abs(tp1_px - price)
            original_rr = original_tp_dist / current_sl_dist if current_sl_dist > 0 else 2.0
            new_tp_dist = min_sl_dist_abs * original_rr
            
            if direction == "long":
                tp1_px = price + new_tp_dist
            else:
                tp1_px = price - new_tp_dist

        # Proteção Extra: teto do SL inicial. O piso acima evita SL apertado demais, mas
        # nada limitava o outro lado — uma estratégia com ATR alto (ou um ativo genuinamente
        # volátil, ex.: DOGE) podia abrir com risco de vários % do preço, e nenhum ajuste de
        # trailing posterior resolve um risco que já nasceu grande. Teto adaptativo: o mais
        # apertado entre 4x o ATR atual e 5% do preço.
        sl_cut, tp1_cut, capped = cap_initial_stop(price, sl_px, tp1_px, atr, 1 if direction == "long" else -1)
        if capped:
            log.warning(f"[Bot {self.config.id}] SL Original largo ({abs(price - sl_px)/price*100:.2f}%). "
                        f"Limitando ao teto de {abs(price - sl_cut)/price*100:.2f}%.")
            sl_px, tp1_px = sl_cut, tp1_cut

        sl_px = round(sl_px, 4)
        tp1_px = round(tp1_px, 4)

        # Dimensionamento: stake fixo do bot (padrão) ou risco fixo por trade até o Stop Loss (opcional).
        stake_usd = self.config.stake_usd
        if sizing.sizing_mode == "risk":
            balance = None
            try:
                balance = await exchange.get_balance("USDT")
            except Exception as exc:
                log.warning("[Bot %d] Saldo indisponível para dimensionar pelo risco: %s", self.config.id, exc)
            plan = compute_stake(sizing, self.config.stake_usd, price, sl_px, balance)
            if plan["blocked"]:
                self._hold_reason = plan["blocked"]
                self._entry_inflight = False
                log.warning("[Bot %d] ENTRADA BLOQUEADA — %s", self.config.id, plan["blocked"])
                return
            stake_usd = plan["stake"]
            sz = exchange.num_contracts(self.config.symbol, price, stake_usd, self.config.leverage)
            log.info(
                "[Bot %d] Dimensionamento por risco: arrisca $%s com SL a %.2f%% -> stake $%.2f%s",
                self.config.id, plan["risk_usd"], (plan["sl_pct"] or 0) * 100, stake_usd,
                f" (limitado por {plan['limited_by']})" if plan["limited_by"] else "",
            )
        self._planned_stake_usd = stake_usd

        # Proteção Extra: Verifica se já existe posição aberta na exchange antes de entrar
        # Isso evita entradas múltiplas se o estado local estiver dessincronizado
        try:
            pos = await exchange.get_position(self.config.symbol)
            # Saldo spot apenas do usuário (coberto pelo baseline) NÃO é posição do bot: segue para a entrada.
            # Antes qualquer saldo != 0 abortava a entrada em silêncio quando o bot estava flat.
            if self._exchange_has_bot_position(pos):
                if self._direction != 0:
                    self._hold_reason = "Bloqueio: Posição já aberta na exchange."
                    log.warning("[Bot %d] Bloqueio de reentrada: Posição detectada na exchange (%.4f %s @ %f).", 
                                self.config.id, pos.size, pos.side, pos.avg_price)
                    self._direction = 1 if pos.side == "long" else -1
                    self._sz = self._net_size(pos)          # sem o baseline do usuário
                    self._sz_remaining = self._sz
                    if not self._is_spot_symbol(self.config.symbol) or not self._entry_price:
                        self._entry_price = pos.avg_price   # em spot o avg_price da OKX é o último preço, não o custo
                    self._save_trade(
                        {"size": self._sz, "entry_price": self._entry_price},
                        update_id=self._current_trade_id,
                    )
                else:
                    # Banco FLAT mas OKX tem posição — adotar agora para não ficar bloqueado.
                    if not self._can_adopt_spot_position(pos, context="entry_precheck"):
                        self._entry_inflight = False
                        return
                    adopted_dir = 1 if pos.side == "long" else -1
                    _baseline   = abs(float(getattr(self.config, "baseline_balance", 0.0) or 0.0))
                    adopted_sz  = max(0.0, abs(pos.size) - _baseline)
                    adopted_px  = pos.avg_price or 0.0
                    fallback_sl = round(
                        adopted_px * (1 - 0.05) if adopted_dir == 1
                        else adopted_px * (1 + 0.05), 6
                    )
                    self._direction    = adopted_dir
                    self._sz           = adopted_sz
                    self._sz_remaining = adopted_sz
                    self._entry_price  = adopted_px
                    self._sl_price     = fallback_sl
                    # Calcula TP1 para posição órfã (2% de lucro como padrão conservador)
                    orphan_tp1 = adopted_px * (1.02 if adopted_dir == 1 else 0.98)
                    self._tp1_price    = orphan_tp1
                    self._hold_reason  = "Posição órfã adotada — gerenciando."
                    log.warning(
                        "[Bot %d] Posição órfã adotada em _enter: %.4f %s @ %.4f. SL conservador %.4f. TP1 em %.4f.",
                        self.config.id, adopted_sz, pos.side, adopted_px, fallback_sl, orphan_tp1,
                    )
                    trade_id = self._save_trade({
                        "type":        "entry",
                        "event":       "ORPHAN_ADOPTED",
                        "direction":   "LONG" if adopted_dir == 1 else "SHORT",
                        "size":        adopted_sz,
                        "entry_price": adopted_px,
                        "sl_price":    fallback_sl,
                        "tp1_price":   orphan_tp1,
                        "source":      "orphan_recovery",
                    })
                    self._current_trade_id = trade_id
                self._entry_inflight = False
                return
        except Exception as e:
            log.error("[Bot %d] Erro ao validar posição na exchange: %s", self.config.id, e)

        # Verifica horário de mercado para stocks (crypto é 24/7)
        _sym_upper = self.config.symbol.upper()
        _is_crypto_sym = "/" in _sym_upper or "-" in _sym_upper
        if not _is_crypto_sym:
            try:
                clock = await exchange.get_clock()
                if not clock.get("is_open", True):
                    self._hold_reason = "Mercado fechado (fora do horário de operação)."
                    log.info("[Bot %d] Entrada bloqueada — mercado fechado. Próxima abertura: %s",
                             self.config.id, clock.get("next_open", "?"))
                    self._entry_inflight = False
                    return
            except Exception as _ce:
                log.warning("[Bot %d] Falha ao verificar clock de mercado: %s", self.config.id, _ce)

        entry_side = "buy" if direction == "long" else "sell"
        close_side = "sell" if direction == "long" else "buy"
        if self._is_spot_symbol(self.config.symbol) and direction != "long":
            self._hold_reason = "Mercado spot sem alavancagem permite apenas compra/fechamento de saldo."
            self._entry_inflight = False
            return
        try:
            # Força alavancagem de 1x (risco isolado) antes de entrar
            await exchange.set_leverage(self.config.symbol, 1)
            ord_id = await exchange.market_order(self.config.symbol, entry_side, sz)
        except Exception as exc:
            ord_id = None
            self._entry_inflight = False
            exchange.last_order_error = {
                "kind": "order_exception",
                "message": str(exc),
            }
        if not ord_id:
            err = getattr(exchange, "last_order_error", None) or {
                "kind": "order_rejected",
                "message": "Ordem recusada pela exchange.",
            }
            suggested = self.config.symbol.replace("-SWAP", "").replace("-FUTURES", "")
            self._last_order_error = {
                **err,
                "symbol": self.config.symbol,
                "suggested_symbol": suggested,
            }
            self._record_order_rejection(
                side=entry_side,
                order_type="market",
                reason=err.get("message", "Ordem recusada pela exchange."),
                status="rejected",
                raw_payload=err,
            )
            # Notifica via WebSocket (UI) com nome do bot para o toast
            await self._broadcast({
                "type":     "order_error",
                "bot_id":   self.config.id,
                "bot_name": self.config.name,
                "symbol":   self.config.symbol,
                "direction": direction,
                "error":    self._last_order_error,
            })
            # Notifica via Telegram com detalhes do erro
            asyncio.create_task(self._notifier.send(build_order_failed_msg(
                bot_name  = self.config.name,
                demo      = self.config.demo,
                symbol    = self.config.symbol,
                direction = direction,
                error_msg = err.get("message", "Erro desconhecido"),
                sz        = sz,
                price     = price,
            )))
            self._entry_inflight = False
            return
        self._last_order_error = None

        price, sz = await self._sync_entry_fill_from_exchange(exchange, ord_id, price, sz)
        if sz <= 0:
            self._record_order_rejection(
                side=entry_side,
                order_type="market",
                reason="OKX não confirmou saldo da posição após ordem de entrada.",
                status="critical",
                raw_payload={"order_id": ord_id},
            )
            self._entry_inflight = False
            return

        sl_id, sl_px = await self._place_stop_loss_with_retry(
            exchange,
            close_side,
            sz,
            sl_px,
            price,
        )
        if not sl_id:
            err = getattr(exchange, "last_order_error", None) or {}
            self._record_order_rejection(
                side=close_side,
                order_type="stop_loss",
                reason=err.get("message", "Stop Loss fixo recusado pela exchange."),
                status="critical",
                raw_payload=err,
            )
            emergency_closed = await self._emergency_close_unprotected_entry(
                exchange,
                close_side,
                sz,
                err.get("message", "Stop Loss fixo recusado pela exchange."),
            )
            self._last_order_error = {
                **err,
                "kind": err.get("kind", "stop_loss_rejected"),
                "symbol": self.config.symbol,
                "message": (
                    "OKX recusou o Stop Loss inicial. "
                    + ("A posição foi encerrada a mercado por segurança." if emergency_closed else "A tentativa de encerramento emergencial também falhou.")
                ),
            }
            await self._broadcast({
                "type":     "order_error",
                "bot_id":   self.config.id,
                "bot_name": self.config.name,
                "symbol":   self.config.symbol,
                "direction": direction,
                "error":    self._last_order_error,
            })
            asyncio.create_task(self._notifier.send(build_order_failed_msg(
                bot_name  = self.config.name,
                demo      = self.config.demo,
                symbol    = self.config.symbol,
                direction = direction,
                error_msg = self._last_order_error["message"],
                sz        = sz,
                price     = price,
            )))
            if emergency_closed:
                self._entry_inflight = False
                return

        self._direction    = 1 if direction == "long" else -1
        self._entry_price  = price
        self._sz           = sz
        self._sz_remaining = sz
        self._tp1_price    = tp1_px
        self._sl_price     = sl_px
        self._tp1_done     = False
        self._ts_pct       = float(result.metadata.get("ts_pct") or 0.0)
        self._sl_algo_id   = sl_id
        self._entry_ord_id = ord_id # Armazenamos o ID de entrada
        self._entry_inflight = False
        if not sl_id:
            self._halted = True
            self._halt_kind = "critical"
            self._hold_reason = "CRÍTICO: posição aberta sem Stop Loss confirmado na OKX."

        # Persiste no banco e linka o signal_log que originou esta entrada
        trade_id = self._save_trade({
            "type": "entry", "direction": direction.upper(),
            "size": sz, "entry_price": price,
            "sl_price": sl_px, "tp1_price": tp1_px, "atr": atr,
        })
        self._current_trade_id = trade_id
        if signal_log_id and trade_id:
            self._link_signal_to_trade(signal_log_id, trade_id)

        # Confirma o preço real de fill em background (market orders podem demorar para executar)
        asyncio.create_task(self._confirm_fill_price(ord_id, exchange))

        await self._broadcast({
            "type":      "trade",
            "bot_id":    self.config.id,
            "event":     "entry",
            "direction": direction,
            "price":     price,
            "sz":        sz,
            "sl":        sl_px,
            "tp1":       tp1_px,
        })

        asyncio.create_task(self._notifier.send(build_entry_msg(
            bot_name      = self.config.name,
            strategy_id   = self.config.strategy_id,
            symbol        = self.config.symbol,
            timeframe     = self.config.timeframe,
            leverage      = self.config.leverage,
            stake_usd     = stake_usd,
            demo          = self.config.demo,
            stop_loss_usd = self.config.stop_loss_usd,
            direction     = direction,
            entry_price   = price,
            sl_price      = sl_px,
            tp1_price     = tp1_px,
            sz            = sz,
            indicators    = result.indicators,
            daily_pnl     = self._daily_pnl,
            wins          = self._wins,
            losses        = self._losses,
        )))
