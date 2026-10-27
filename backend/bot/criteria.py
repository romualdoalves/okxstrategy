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
                if pos and pos.side == "long" and pos.size > 0:
                    real_size = abs(float(pos.size))
                    if fallback_size > 0 and abs(real_size - fallback_size) > max(1e-8, fallback_size * 0.001):
                        log.warning(
                            "[Bot %d] Entrada SPOT ajustada pelo saldo líquido OKX: fill=%.8f saldo=%.8f.",
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
                if pos and pos.size > 0:
                    real_size = abs(float(pos.size))
                    real_price = float(pos.avg_price or fill_price)
                    if abs(real_size - fill_size) > max(1e-8, fill_size * 0.001):
                        log.warning(
                            "[Bot %d] Entrada SPOT ajustada pelo saldo OKX: fill=%.8f saldo=%.8f.",
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
                if pos is None or abs(float(pos.size or 0.0)) <= 1e-9:
                    await self._on_position_closed(price, size, exchange)
                    return True
                self._sz = abs(float(pos.size))
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
        ready = bool(blocking) and len(ok) == len(blocking)
        first_blocker = blocked[0] if blocked else waiting[0] if waiting else None
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
                ("runtime", "O2 Runtime", self.status.upper(), "Aguardando sinal para validar runtime."),
                ("circuit_breaker", "O3 Circuit Breaker", "—", "Aguardando sinal para validar circuit breaker."),
                ("local_flat", "O4 Posição local", "—", "Aguardando sinal para validar posição local."),
                ("inflight", "O5 Entrada pendente", "—", "Aguardando sinal para validar ordens pendentes."),
                ("market_direction", "O6 Direção", "—", "Aguardando sinal para validar direção permitida."),
                ("calendar", "O7 Calendário", "—", "Aguardando sinal para validar janela macro."),
                ("exchange", "O8 Conexão OKX", "—", "Aguardando sinal para validar conexão OKX."),
                ("sizing", "O9 Tamanho", "—", "Aguardando sinal para calcular tamanho."),
                ("risk", "O10 SL/TP", "—", "Aguardando sinal para validar SL/TP."),
                ("exchange_flat", "O11 Posição OKX", "—", "Será validado imediatamente antes do envio da ordem."),
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

        items.append(self._criterion(
            id="runtime",
            label="O2 Runtime",
            value=self.status.upper(),
            status="green" if self.status == "running" and not self._maintenance else "red",
            detail="Bot em execução e exchange fora de manutenção." if self.status == "running" and not self._maintenance else (self._maintenance or "Bot não está em execução."),
        ))
        items.append(self._criterion(
            id="circuit_breaker",
            label="O3 Circuit breaker",
            value="OK" if not self._halted else "PAUSADO",
            status="green" if not self._halted else "red",
            detail="Stop diário não foi atingido." if not self._halted else "Circuit breaker ativo por stop diário.",
        ))
        items.append(self._criterion(
            id="local_flat",
            label="O4 Posição local",
            value="FLAT" if self._direction == 0 else ("LONG" if self._direction > 0 else "SHORT"),
            status="green" if self._direction == 0 else "red",
            detail="Bot está sem posição local." if self._direction == 0 else "Bot já está em operação.",
        ))
        items.append(self._criterion(
            id="pending_order",
            label="O5 Ordem pendente",
            value="LIVRE" if not self._entry_inflight else "ENVIANDO",
            status="green" if not self._entry_inflight else "yellow",
            detail="Nenhuma entrada está em envio." if not self._entry_inflight else "Uma entrada já está sendo enviada.",
        ))
        spot_short_blocked = direction == "short" and not is_deriv
        items.append(self._criterion(
            id="market_direction",
            label="O6 Mercado/direção",
            value="DERIV" if is_deriv else "SPOT",
            status="red" if spot_short_blocked else "green",
            detail="Direção permitida para este mercado." if not spot_short_blocked else "Mercado SPOT permite somente entradas compradas.",
        ))

        in_window, event_name = calendar_feed.is_high_impact_window(self.config.strategy_id)
        items.append(self._criterion(
            id="calendar",
            label="O7 Calendário",
            value="LIVRE" if not in_window else "BLOQUEADO",
            status="green" if not in_window else "red",
            detail="Fora de janela macro de alto impacto." if not in_window else f"Calendário: {event_name}",
        ))

        exchange_ok = exchange is not None
        items.append(self._criterion(
            id="exchange",
            label="O8 Conexão OKX",
            value="OK" if exchange_ok else "SEM EXCHANGE",
            status="green" if exchange_ok else "red",
            detail="Adaptador OKX disponível para envio." if exchange_ok else "Exchange não inicializada.",
        ))

        sz = 0.0
        if exchange and direction and price > 0:
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
                    label="O9 Tamanho",
                    value="ERRO",
                    status="red",
                    detail=f"Falha ao calcular tamanho da ordem: {exc}",
                ))
            else:
                items.append(self._criterion(
                    id="sizing",
                    label="O9 Tamanho",
                    value=f"{sz:.6g}",
                    status="green" if sz > 0 else "red",
                    detail="Tamanho da ordem calculado." if sz > 0 else "Tamanho calculado é zero.",
                ))
        else:
            items.append(self._criterion(
                id="sizing",
                label="O9 Tamanho",
                value="—",
                status="yellow",
                detail="Aguardando preço, direção e exchange para calcular tamanho.",
            ))

        risk_ok = False
        if result and direction and price > 0:
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
                label="O10 SL/TP",
                value="OK" if risk_ok else "INVÁLIDO",
                status="green" if risk_ok else "red",
                detail="Stop Loss e TP1 coerentes com a direção." if risk_ok else "SL/TP inválidos para a direção do sinal.",
            ))
        else:
            items.append(self._criterion(
                id="risk",
                label="O10 SL/TP",
                value="—",
                status="yellow",
                detail="Aguardando sinal para validar SL/TP.",
            ))

        if check_exchange_position and exchange and direction:
            try:
                pos = await exchange.get_position(self.config.symbol)
                qty = abs(float(getattr(pos, "size", 0.0) or 0.0)) if pos else 0.0
                items.append(self._criterion(
                    id="exchange_flat",
                    label="O11 Posição OKX",
                    value="FLAT" if qty <= 0 else "ABERTA",
                    status="green" if qty <= 0 else "red",
                    detail="OKX sem posição aberta neste ativo." if qty <= 0 else "OKX já possui posição aberta neste ativo.",
                ))
            except Exception as exc:
                items.append(self._criterion(
                    id="exchange_flat",
                    label="O11 Posição OKX",
                    value="ERRO",
                    status="red",
                    detail=f"Falha ao validar posição na OKX: {exc}",
                ))
        else:
            items.append(self._criterion(
                id="exchange_flat",
                label="O11 Posição OKX",
                value="—",
                status="yellow" if direction else "none",
                detail="Será validado imediatamente antes do envio da ordem.",
            ))

        packed = self._pack_order_criteria(items)
        self._last_order_criteria = packed
        if not packed["ready"] and packed.get("reason"):
            self._hold_reason = packed["reason"]
        return packed
