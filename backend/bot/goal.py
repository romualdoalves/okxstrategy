"""Modo "Meta 1% Bruto (24 h)" do bot: acompanha a meta BRUTA da sessão e encerra o bot sozinho.

As decisões vêm de backend/goal.py (funções puras, compartilhadas com a tela e os testes); aqui só há a cola com o bot:
ler o P&L da sessão no banco, vender a mercado ao cobrir a meta, parar o bot e avisar.

Regras (resumo; detalhes em backend/goal.py): para ao atingir a meta; para se não emitir ordem em 24 h; com posição aberta
só para por meta ou saída com prejuízo (stop loss); o trailing stop segue valendo na posição até a meta.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime

from .. import goal as G
from ..database import BotModel, SessionLocal, TradeModel
from ..profit_lock import fee_rate, net_unrealized
from .common import log


class GoalMixin:
    # estado (inicializado em BotInstance.__init__): _goal_loaded, _goal_done, _goal_closing, _goal_realized_gross_pct,
    # _goal_realized_net_pct, _goal_had_order, _goal_outcome, _goal_retry_at

    # ── configuração ─────────────────────────────────────────────────────────────────────────────────────────────
    def _goal_enabled(self) -> bool:
        """Bot em modo meta, com janela iniciada e ainda não encerrada."""
        return (
            bool(getattr(self.config, "goal_pct", None))
            and getattr(self.config, "goal_started_at", None) is not None
            and getattr(self.config, "goal_outcome", None) is None
            and not self._goal_done
        )

    def _goal_params(self) -> tuple[float, float, datetime]:
        return (
            float(self.config.goal_pct),
            float(getattr(self.config, "goal_window_hours", None) or G.WINDOW_HOURS),
            self.config.goal_started_at,
        )

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.utcnow()          # os modelos guardam UTC "ingênuo"

    # ── P&L da sessão: BRUTO (decide a meta) e LÍQUIDO (o que sobra depois das taxas) ─────────────────────────────────────
    def _goal_trade_pcts(self, row) -> tuple[float, float]:
        """(bruto %, líquido %) de uma operação fechada, sobre o stake. Bruto = P&L antes das taxas. Líquido = bruto menos a
        corretagem (a real, se já sincronizada; senão a estimada com a taxa por lado nas duas pernas)."""
        if row.pnl is None:
            return 0.0, 0.0
        stake = float(self.config.stake_usd or 100.0)
        fee = row.fee
        if fee is None:
            entry, exit_ = float(row.entry_price or 0.0), float(row.exit_price or 0.0)
            fee = (stake + (stake * exit_ / entry if entry > 0 else stake)) * fee_rate()
        return float(row.pnl) / stake * 100, (float(row.pnl) - float(fee)) / stake * 100

    def _goal_load(self) -> None:
        """Recalcula do banco o realizado (bruto e líquido) e se já houve ordem na janela (bloqueante: rodar em thread)."""
        started = self.config.goal_started_at
        with SessionLocal() as db:
            exits = (db.query(TradeModel)
                     .filter(TradeModel.bot_id == self.config.id, TradeModel.type == "exit",
                             TradeModel.closed_at >= started)
                     .all())
            orders = (db.query(TradeModel)
                      .filter(TradeModel.bot_id == self.config.id, TradeModel.timestamp >= started)
                      .count())
        pcts = [self._goal_trade_pcts(r) for r in exits]
        self._goal_realized_gross_pct = sum(g for g, _ in pcts)
        self._goal_realized_net_pct = sum(n for _, n in pcts)
        self._goal_had_order = orders > 0 or self._direction != 0
        self._goal_loaded = True

    def _goal_open_gross_pct(self, price: float) -> float:
        """Ganho BRUTO da posição aberta, em % do preço de entrada."""
        if self._direction == 0 or self._entry_price <= 0 or price <= 0:
            return 0.0
        return (price - self._entry_price) * self._direction / self._entry_price * 100

    def _goal_open_net_pct(self, price: float) -> float:
        """Ganho LÍQUIDO (taxas de entrada e saída descontadas) da posição aberta, como se fechasse agora."""
        if self._direction == 0 or self._entry_price <= 0 or price <= 0:
            return 0.0
        return net_unrealized(self._entry_price, price, abs(float(self._sz or 0.0)), self._direction)[1]

    # ── decisões ─────────────────────────────────────────────────────────────────────────────────────────────────
    async def _goal_tick(self, price: float, exchange) -> None:
        """A cada preço: vende ao cobrir a meta, ou para o bot (meta/sem ordem/prazo) quando está sem posição."""
        if not self._goal_enabled() or self._goal_closing:
            return
        if not self._goal_loaded:
            await asyncio.to_thread(self._goal_load)
        pct, window, started = self._goal_params()
        in_position = self._direction != 0
        action, why = G.decide_tick(
            now=self._utcnow(), started_at=started, window_hours=window, goal_pct=pct,
            realized_gross_pct=self._goal_realized_gross_pct, in_position=in_position,
            open_gross_pct=self._goal_open_gross_pct(price), had_order=self._goal_had_order or in_position,
        )
        if action == "stop":
            await self._goal_finish(why)
        elif action == "take_profit":
            if time.monotonic() < self._goal_retry_at:
                return
            self._goal_closing = True
            try:
                log.info("[Bot %d] META: ganho bruto aberto cobre a meta bruta (%.2f%%) — vendendo a mercado.",
                         self.config.id, pct)
                if not await self.manual_liquidate(exchange):
                    self._goal_retry_at = time.monotonic() + 30      # falhou: tenta de novo em 30 s, não a cada tick
            finally:
                self._goal_closing = False

    async def _goal_after_close(self, closed_gross_pct: float) -> None:
        """Logo depois de fechar uma posição: meta atingida, saída com prejuízo (stop loss) ou prazo esgotado."""
        if not self._goal_enabled():
            return
        await asyncio.to_thread(self._goal_load)             # já inclui a operação que acabou de fechar
        pct, window, started = self._goal_params()
        action, why = G.decide_after_close(
            now=self._utcnow(), started_at=started, window_hours=window, goal_pct=pct,
            realized_gross_pct=self._goal_realized_gross_pct, closed_gross_pct=closed_gross_pct,
        )
        if action == "stop":
            await self._goal_finish(why)

    # ── encerramento ─────────────────────────────────────────────────────────────────────────────────────────────
    def _goal_persist(self, outcome: str) -> None:
        with SessionLocal() as db:
            bot = db.get(BotModel, self.config.id)
            if bot is not None:
                bot.active = False
                bot.goal_ended_at = self._utcnow()
                bot.goal_outcome = outcome
                bot.goal_result_gross_pct = round(self._goal_realized_gross_pct, 4)
                bot.goal_result_net_pct = round(self._goal_realized_net_pct, 4)
                db.commit()

    async def _goal_finish(self, outcome: str) -> None:
        if self._goal_done:
            return
        self._goal_done = True
        self._goal_outcome = outcome
        label = G.OUTCOME_LABELS.get(outcome, outcome)
        try:
            await asyncio.to_thread(self._goal_persist, outcome)
        except Exception as exc:
            log.error("[Bot %d] META: falha ao gravar o encerramento (%s) — parando mesmo assim.", self.config.id, exc)
        gross, net = self._goal_realized_gross_pct, self._goal_realized_net_pct
        illusory = outcome == "reached" and net <= 0
        log.warning("[Bot %d] META ENCERRADA: %s | sessão: bruto %+.2f%%, líquido %+.2f%%%s", self.config.id, label,
                    gross, net, " (ILUSÃO: as taxas consumiram o ganho)" if illusory else "")
        self._hold_reason = label
        try:
            await self._broadcast({"type": "goal", "bot_id": self.config.id, "outcome": outcome, "message": label})
        except Exception:
            pass
        try:
            icon = "🎯" if outcome == "reached" else "⏹️"
            warn = ("\n⚠️ As taxas consumiram o ganho: o líquido ficou em zero ou negativo." if illusory else "")
            asyncio.create_task(self._notifier.send(
                f"{icon} <b>META 1% BRUTO — {label.upper()}</b>\n\n"
                f"🤖 Bot: {self.config.name} ({self.config.symbol})\n"
                f"📊 Sessão: bruto {gross:+.2f}% (meta {float(self.config.goal_pct):.2f}%) · líquido {net:+.2f}%{warn}\n"
                f"O bot foi parado."))
        except Exception:
            pass
        self.stop()

    # ── visão para a tela ────────────────────────────────────────────────────────────────────────────────────────
    def _goal_view(self, price: float) -> dict | None:
        outcome = self._goal_outcome or getattr(self.config, "goal_outcome", None)
        view = G.view_from_row(
            pct=getattr(self.config, "goal_pct", None), window_hours=getattr(self.config, "goal_window_hours", None),
            started_at=getattr(self.config, "goal_started_at", None), outcome=outcome, now=self._utcnow(),
            result_gross_pct=round(self._goal_realized_gross_pct, 2) if self._goal_done else None,
            result_net_pct=round(self._goal_realized_net_pct, 2) if self._goal_done else None,
        )
        if view is None:
            return None
        loaded = self._goal_loaded
        open_gross = round(self._goal_open_gross_pct(price), 2) if self._direction != 0 else 0.0
        open_net = round(self._goal_open_net_pct(price), 2) if self._direction != 0 else 0.0
        realized_gross = round(self._goal_realized_gross_pct, 2) if loaded else None
        realized_net = round(self._goal_realized_net_pct, 2) if loaded else None
        view.update(
            realized_gross_pct=realized_gross, realized_net_pct=realized_net,
            open_gross_pct=open_gross, open_net_pct=open_net,
            session_gross_pct=round(realized_gross + open_gross, 2) if loaded else None,
            session_net_pct=round(realized_net + open_net, 2) if loaded else None,
            in_position=self._direction != 0, done=bool(self._goal_done or outcome),
        )
        return view
