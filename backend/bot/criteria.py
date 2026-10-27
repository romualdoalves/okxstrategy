"""
Critérios de ordem (O1–O11), helpers de saldo spot e confirmação de fills.
"""

from __future__ import annotations

import asyncio

from ..exchanges.base import BaseExchange
from ..strategies.base import Signal
from ..feeds.economic_calendar import calendar_feed
from .common import log


class OrderGateMixin:
    @staticmethod
    def _is_spot_symbol(symbol: str) -> bool:
        s = (symbol or "").upper()
        return "-" in s and not (s.endswith("-SWAP") or s.endswith("-FUTURES"))

    def _baseline_qty(self) -> float:
        """Holdings do usuário anteriores ao bot (só spot). Não são posição do bot."""
        if not self._is_spot_symbol(self.config.symbol):
            return 0.0
        return abs(float(getattr(self.config, "baseline_balance", 0.0) or 0.0))

    def _net_size(self, pos) -> float:
        """Tamanho da posição que é DO BOT: em spot, `get_position` devolve o saldo TOTAL da moeda, então
        desconta o baseline. Em derivativos é o próprio tamanho."""
        if not pos:
            return 0.0
        return max(0.0, abs(float(getattr(pos, "size", 0.0) or 0.0)) - self._baseline_qty())

    def _exchange_has_bot_position(self, pos) -> bool:
        """True se a OKX mostra uma posição que precisa ser tratada antes de uma nova entrada. Com posição
        local aberta, qualquer saldo conta; com o bot flat, só o que passa do baseline (saldo apenas do
        usuário NÃO bloqueia a entrada)."""
        if not pos or float(getattr(pos, "size", 0.0) or 0.0) == 0.0:
            return False
        return self._direction != 0 or self._net_size(pos) > 1e-9

    def _position_notional(self, pos, reference_price: float | None = None) -> float:
        price = float(reference_price or getattr(pos, "avg_price", 0.0) or 0.0)
        size = abs(float(getattr(pos, "size", 0.0) or 0.0))
        return size * price if price > 0 and size > 0 else 0.0

    def _can_adopt_spot_position(self, pos, *, context: str) -> bool:
        if not pos or not self._is_spot_symbol(self.config.symbol):
            return True

        total_size   = abs(float(getattr(pos, "size", 0.0) or 0.0))
        baseline_qty = abs(float(getattr(self.config, "baseline_balance", 0.0) or 0.0))
        net_size     = max(0.0, total_size - baseline_qty)

        if net_size <= 1e-9:
            log.debug(
                "[Bot %d] Saldo spot %s=%.8f inteiramente coberto pelo baseline=%.8f — ignorado.",
                self.config.id, self.config.symbol, total_size, baseline_qty,
            )
            return False

        price    = abs(float(getattr(pos, "avg_price", 0.0) or 0.0))
        notional = net_size * price if price > 0 else 0.0

        msg = (
            f"Aviso: Saldo spot encontrado na OKX em {self.config.symbol} (líquido: {net_size:.8f} units, ~${notional:.2f}). "
            f"O bot foi bloqueado para entrada automática a fim de proteger seus fundos de hold. "
            f"Se deseja que o bot opere, venda este saldo manualmente na OKX ou ajuste o baseline_balance do bot."
        )
        self._hold_reason = msg
        self._last_order_error = {
            "kind": "spot_orphan_notional_mismatch",
            "message": msg,
            "symbol": self.config.symbol,
            "context": context,
            "okx_notional": round(notional, 2),
            "expected_notional": getattr(self.config, "stake_usd", 0.0),
        }
        log.warning("[Bot %d] %s", self.config.id, msg)
        return False

    async def _close_size_from_exchange(self, exchange: BaseExchange) -> float:
        local_size = abs(float(self._sz or 0.0))
        if self._is_spot_symbol(self.config.symbol) and self._direction > 0:
            try:
                pos = await exchange.get_position(self.config.symbol)
                if pos and pos.side == "long" and pos.size > 0:
                    real_size = abs(float(pos.size))
                    baseline = abs(float(getattr(self.config, "baseline_balance", 0.0) or 0.0))
                    net_size = max(0.0, real_size - baseline)
                    if local_size > 0 and abs(net_size - local_size) > max(1e-8, local_size * 0.001):
                        log.warning(
                            "[Bot %d] SPOT size desync em %s: app=%.8f OKX líquido=%.8f (real=%.8f). Fechando líquido.",
                            self.config.id, self.config.symbol, local_size, net_size, real_size,
                        )
                    return net_size
            except Exception as exc:
                log.warning("[Bot %d] Falha ao obter saldo spot real para fechamento: %s", self.config.id, exc)
        return local_size

    async def _entry_size_from_exchange(self, exchange: BaseExchange, fallback_size: float) -> float:
        fallback_size = abs(float(fallback_size or 0.0))
        if self._is_spot_symbol(self.config.symbol) and self._direction >= 0:
            try:
                pos = await exchange.get_position(self.config.symbol)
                real_size = self._net_size(pos)
                if pos and pos.side == "long" and real_size > 0:
                    if fallback_size > 0 and abs(real_size - fallback_size) > max(1e-8, fallback_size * 0.001):
                        log.warning(
                            "[Bot %d] Entrada SPOT ajustada pelo saldo líquido OKX: fill=%.8f saldo líquido=%.8f.",
                            self.config.id, fallback_size, real_size,
                        )
                    return real_size
            except Exception as exc:
                log.warning("[Bot %d] Falha ao obter saldo spot real da entrada: %s", self.config.id, exc)
        return fallback_size

    async def _sync_entry_fill_from_exchange(
        self,
        exchange: BaseExchange,
        ord_id: str,
        fallback_price: float,
        fallback_size: float,
    ) -> tuple[float, float]:
        fill_price = fallback_price
        fill_size = fallback_size
        for _ in range(12):
            await asyncio.sleep(0.5)
            try:
                order = await exchange.get_order(ord_id)
                if order and order.get("status") == "filled":
                    fill_price = float(order.get("filled_avg_price") or fill_price)
                    fill_size = float(order.get("filled_size") or fill_size)
                    break
            except Exception:
                pass

        for _ in range(12):
            try:
                pos = await exchange.get_position(self.config.symbol)
                real_size = self._net_size(pos)
                if pos and real_size > 0:
                    # Em spot o avg_price da OKX é o ÚLTIMO preço (não o custo): o preço de entrada é o do fill.
                    real_price = fill_price if self._is_spot_symbol(self.config.symbol) else float(pos.avg_price or fill_price)
                    if abs(real_size - fill_size) > max(1e-8, fill_size * 0.001):
                        log.warning(
                            "[Bot %d] Entrada SPOT ajustada pelo saldo líquido OKX: fill=%.8f saldo líquido=%.8f.",
                            self.config.id, fill_size, real_size,
                        )
                    return real_price, real_size
            except Exception as exc:
                log.warning("[Bot %d] Falha ao validar posição pós-entrada na OKX: %s", self.config.id, exc)
            await asyncio.sleep(0.5)

        return fill_price, fill_size

    async def _confirm_flat_then_close(self, price: float, size: float, exchange: BaseExchange) -> bool:
        for _ in range(12):
            try:
                pos = await exchange.get_position(self.config.symbol)
                net = self._net_size(pos)
                if pos is None or net <= 1e-9:      # só o baseline do usuário sobrou = a posição do bot zerou
                    await self._on_position_closed(price, size, exchange)
                    return True
                self._sz = net
                self._sz_remaining = self._sz
            except Exception:
                pass
            await asyncio.sleep(0.5)
        log.warning(
            "[Bot %d] Fechamento não confirmado: OKX ainda mostra posição em %s. App não será marcado como flat.",
            self.config.id, self.config.symbol,
        )
        return False

    @staticmethod
    def _criterion(
        *,
        id: str,
        label: str,
        status: str,
        detail: str,
        value: str = "",
        blocking: bool = True,
    ) -> dict:
        return {
            "id": id,
            "label": label,
            "sub": detail,
            "value": value,
            "status": status,
            "detail": detail,
            "blocking": blocking,
        }

    @classmethod
    def _empty_order_criteria(cls) -> dict:
        items = [
            cls._criterion(
                id="signal",
                label="O1 Sinal",
                value="HOLD",
                status="yellow",
                detail="Aguardando BUY/SELL da estratégia.",
            )
        ]
        return cls._pack_order_criteria(items)

    @staticmethod
    def _pack_order_criteria(items: list[dict]) -> dict:
        active = [c for c in items if c.get("status") != "none"]
        blocking = [c for c in active if c.get("blocking", True)]
        ok = [c for c in blocking if c.get("status") == "green"]
        blocked = [c for c in blocking if c.get("status") == "red"]
        waiting = [c for c in blocking if c.get("status") == "yellow"]
        inapplicable = [c for c in blocking if c.get("status") == "grey"]
        ready = bool(blocking) and len(ok) == len(blocking)
        first_blocker = inapplicable[0] if inapplicable else blocked[0] if blocked else waiting[0] if waiting else None
        return {
            "ready": ready,
            "ok": len(ok),
            "total": len(blocking),
            "items": items,
            "blocker": first_blocker,
            "reason": "" if ready else (first_blocker or {}).get("detail", ""),
        }

    async def _evaluate_order_criteria(
        self,
        *,
        result=None,
        direction: str | None = None,
        price: float = 0.0,
        exchange: BaseExchange | None = None,
        check_exchange_position: bool = False,
    ) -> dict:
        """
        Gates de execução (O1–O8). Só cobrem o que a estratégia não sabe: se dá para enviar a
        ordem com segurança. Sem BUY/SELL, O2–O8 ficam dormentes (status "none").
        Status "grey" = sinal inaplicável neste mercado (ex.: SELL em spot) — não é falha.
        """
        signal_value = getattr(getattr(result, "signal", Signal.HOLD), "value", Signal.HOLD.value)
        direction = direction or ("long" if signal_value == Signal.BUY.value else "short" if signal_value == Signal.SELL.value else None)
        symbol_upper = (self.config.symbol or "").upper()
        is_deriv = "-SWAP" in symbol_upper or "-FUTURES" in symbol_upper
        items: list[dict] = []

        items.append(self._criterion(
            id="signal",
            label="O1 Sinal executável",
            value=signal_value.upper(),
            status="green" if direction in ("long", "short") else "yellow",
            detail="Estratégia emitiu BUY/SELL." if direction else "Aguardando sinal BUY/SELL da estratégia.",
        ))

        if direction not in ("long", "short"):
            dormant = [
                ("runtime", "O2 Execução", self.status.upper(), "Aguardando sinal para validar bot, conexão OKX e posição local."),
                ("market_direction", "O3 Direção", "—", "Aguardando sinal para validar direção permitida."),
                ("circuit_breaker", "O4 Circuit Breaker", "—", "Aguardando sinal para validar circuit breaker."),
                ("calendar", "O5 Calendário", "—", "Aguardando sinal para validar janela macro."),
                ("sizing", "O6 Tamanho", "—", "Aguardando sinal para calcular tamanho."),
                ("risk", "O7 SL/TP", "—", "Aguardando sinal para validar SL/TP."),
                ("exchange_flat", "O8 Posição OKX", "—", "Será validado imediatamente antes do envio da ordem."),
            ]
            for id_, label, value, detail in dormant:
                items.append(self._criterion(
                    id=id_,
                    label=label,
                    value=value,
                    status="none",
                    detail=detail,
                    blocking=False,
                ))
            packed = self._pack_order_criteria(items)
            self._last_order_criteria = packed
            if not packed["ready"] and packed.get("reason"):
                self._hold_reason = packed["reason"]
            return packed

        # O2 — bot em execução, exchange disponível, sem posição local nem entrada em envio
        if self.status != "running" or self._maintenance:
            exec_status, exec_value, exec_detail = "red", self.status.upper(), (self._maintenance or "Bot não está em execução.")
        elif exchange is None:
            exec_status, exec_value, exec_detail = "red", "SEM EXCHANGE", "Exchange não inicializada."
        elif self._direction != 0:
            exec_status, exec_value, exec_detail = "red", "LONG" if self._direction > 0 else "SHORT", "Bot já está em operação."
        elif self._entry_inflight:
            exec_status, exec_value, exec_detail = "yellow", "ENVIANDO", "Uma entrada já está sendo enviada."
        else:
            exec_status, exec_value, exec_detail = "green", "OK", "Bot em execução, OKX conectada e sem posição local."
        items.append(self._criterion(
            id="runtime",
            label="O2 Execução",
            value=exec_value,
            status=exec_status,
            detail=exec_detail,
        ))

        spot_short_blocked = direction == "short" and not is_deriv
        items.append(self._criterion(
            id="market_direction",
            label="O3 Direção",
            value="DERIV" if is_deriv else "SPOT",
            status="grey" if spot_short_blocked else "green",
            detail="Direção permitida para este mercado." if not spot_short_blocked else "SELL não se aplica: mercado SPOT permite somente entradas compradas.",
        ))

        items.append(self._criterion(
            id="circuit_breaker",
            label="O4 Circuit breaker",
            value="OK" if not self._halted else "PAUSADO",
            status="green" if not self._halted else "red",
            detail="Stop diário não foi atingido." if not self._halted else "Circuit breaker ativo por stop diário.",
        ))

        in_window, event_name = calendar_feed.is_high_impact_window(self.config.strategy_id)
        items.append(self._criterion(
            id="calendar",
            label="O5 Calendário",
            value="LIVRE" if not in_window else "BLOQUEADO",
            status="green" if not in_window else "red",
            detail="Fora de janela macro de alto impacto." if not in_window else f"Calendário: {event_name}",
        ))

        sz = 0.0
        if exchange and price > 0:
            try:
                sz = float(exchange.num_contracts(
                    self.config.symbol,
                    price,
                    self.config.stake_usd,
                    self.config.leverage,
                ) or 0.0)
            except Exception as exc:
                items.append(self._criterion(
                    id="sizing",
                    label="O6 Tamanho",
                    value="ERRO",
                    status="red",
                    detail=f"Falha ao calcular tamanho da ordem: {exc}",
                ))
            else:
                items.append(self._criterion(
                    id="sizing",
                    label="O6 Tamanho",
                    value=f"{sz:.6g}",
                    status="green" if sz > 0 else "red",
                    detail="Tamanho da ordem calculado." if sz > 0 else "Tamanho calculado é zero.",
                ))
        else:
            items.append(self._criterion(
                id="sizing",
                label="O6 Tamanho",
                value="—",
                status="yellow",
                detail="Aguardando preço e exchange para calcular tamanho.",
            ))

        if result and price > 0:
            atr = self._get_dynamic_atr(result, price * 0.005)
            sl_px = result.metadata.get("sl_price", price - atr * 1.5 if direction == "long" else price + atr * 1.5)
            tp1_px = result.metadata.get("tp1_price", price * 1.02 if direction == "long" else price * 0.98)
            risk_ok = (
                sl_px > 0 and tp1_px > 0 and
                ((direction == "long" and sl_px < price and tp1_px > price) or
                 (direction == "short" and sl_px > price and tp1_px < price))
            )
            items.append(self._criterion(
                id="risk",
                label="O7 SL/TP",
                value="OK" if risk_ok else "INVÁLIDO",
                status="green" if risk_ok else "red",
                detail="Stop Loss e TP1 coerentes com a direção." if risk_ok else "SL/TP inválidos para a direção do sinal.",
            ))
        else:
            items.append(self._criterion(
                id="risk",
                label="O7 SL/TP",
                value="—",
                status="yellow",
                detail="Aguardando sinal para validar SL/TP.",
            ))

        # O8 — posição OKX *líquida*: em spot, o baseline (holdings do usuário anteriores ao bot)
        # não conta como posição do bot.
        if check_exchange_position and exchange:
            try:
                pos = await exchange.get_position(self.config.symbol)
                qty = abs(float(getattr(pos, "size", 0.0) or 0.0)) if pos else 0.0
                baseline = abs(float(getattr(self.config, "baseline_balance", 0.0) or 0.0)) if self._is_spot_symbol(self.config.symbol) else 0.0
                net = max(0.0, qty - baseline)
                if net > 1e-9:
                    o8 = ("ABERTA", "red", f"OKX já possui posição aberta neste ativo (líquido: {net:.8f}).")
                elif qty > 1e-9:
                    o8 = ("FLAT", "green", f"Saldo spot {qty:.8f} coberto pelo baseline ({baseline:.8f}) — holdings do usuário ignorados.")
                else:
                    o8 = ("FLAT", "green", "OKX sem posição aberta neste ativo.")
                items.append(self._criterion(
                    id="exchange_flat",
                    label="O8 Posição OKX",
                    value=o8[0],
                    status=o8[1],
                    detail=o8[2],
                ))
            except Exception as exc:
                items.append(self._criterion(
                    id="exchange_flat",
                    label="O8 Posição OKX",
                    value="ERRO",
                    status="red",
                    detail=f"Falha ao validar posição na OKX: {exc}",
                ))
        else:
            items.append(self._criterion(
                id="exchange_flat",
                label="O8 Posição OKX",
                value="—",
                status="yellow",
                detail="Será validado imediatamente antes do envio da ordem.",
            ))

        packed = self._pack_order_criteria(items)
        self._last_order_criteria = packed
        if not packed["ready"] and packed.get("reason"):
            self._hold_reason = packed["reason"]
        return packed
