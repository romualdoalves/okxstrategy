"""Modo "Meta 1% Hoje!" do bot: acompanha a meta líquida da sessão e encerra o bot sozinho.

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
    # estado (inicializado em BotInstance.__init__): _goal_loaded, _goal_done, _goal_closing, _goal_realized_pct,
    # _goal_had_order, _goal_outcome, _goal_retry_at

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

    # ── P&L da sessão (líquido) ──────────────────────────────────────────────────────────────────────────────────
    def _goal_trade_net_pct(self, row) -> float:
        """% líquido de uma operação fechada, sobre o stake: P&L bruto menos a corretagem (a real, se já sincronizada;
        senão a estimada com a taxa por lado nas duas pernas)."""
        if row.pnl is None:
            return 0.0
        stake = float(self.config.stake_usd or 100.0)
        fee = row.fee
        if fee is None:
            entry, exit_ = float(row.entry_price or 0.0), float(row.exit_price or 0.0)
            fee = (stake + (stake * exit_ / entry if entry > 0 else stake)) * fee_rate()
        return (float(row.pnl) - float(fee)) / stake * 100

    def _goal_load(self) -> None:
        """Recalcula do banco o líquido realizado e se já houve ordem na janela (bloqueante: rodar em thread)."""
        started = self.config.goal_started_at
        with SessionLocal() as db:
            exits = (db.query(TradeModel)
                     .filter(TradeModel.bot_id == self.config.id, TradeModel.type == "exit",
                             TradeModel.closed_at >= started)
                     .all())
            orders = (db.query(TradeModel)
                      .filter(TradeModel.bot_id == self.config.id, TradeModel.timestamp >= started)
                      .count())
        self._goal_realized_pct = sum(self._goal_trade_net_pct(r) for r in exits)
        self._goal_had_order = orders > 0 or self._direction != 0
        self._goal_loaded = True

    def _goal_open_net_pct(self, price: float) -> float:
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
            realized_net_pct=self._goal_realized_pct, in_position=in_position,
            open_net_pct=self._goal_open_net_pct(price), had_order=self._goal_had_order or in_position,
        )
        if action == "stop":
            await self._goal_finish(why)
        elif action == "take_profit":
            if time.monotonic() < self._goal_retry_at:
                return
            self._goal_closing = True
            try:
                log.info("[Bot %d] META: lucro líquido aberto cobre a meta (%.2f%%) — vendendo a mercado.",
                         self.config.id, pct)
                if not await self.manual_liquidate(exchange):
                    self._goal_retry_at = time.monotonic() + 30      # falhou: tenta de novo em 30 s, não a cada tick
            finally:
                self._goal_closing = False

    async def _goal_after_close(self, closed_net_pct: float) -> None:
        """Logo depois de fechar uma posição: meta atingida, saída com prejuízo (stop loss) ou prazo esgotado."""
        if not self._goal_enabled():
            return
        await asyncio.to_thread(self._goal_load)             # já inclui a operação que acabou de fechar
        pct, window, started = self._goal_params()
        action, why = G.decide_after_close(
            now=self._utcnow(), started_at=started, window_hours=window, goal_pct=pct,
            realized_net_pct=self._goal_realized_pct, closed_net_pct=closed_net_pct,
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
        log.warning("[Bot %d] META ENCERRADA: %s | líquido da sessão %+.2f%%", self.config.id, label,
                    self._goal_realized_pct)
        self._hold_reason = label
        try:
            await self._broadcast({"type": "goal", "bot_id": self.config.id, "outcome": outcome, "message": label})
        except Exception:
            pass
        try:
            icon = "🎯" if outcome == "reached" else "⏹️"
            asyncio.create_task(self._notifier.send(
                f"{icon} <b>META 1% HOJE — {label.upper()}</b>\n\n"
                f"🤖 Bot: {self.config.name} ({self.config.symbol})\n"
                f"📊 Líquido da sessão: {self._goal_realized_pct:+.2f}% (meta {float(self.config.goal_pct):.2f}%)\n"
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
        )
        if view is None:
            return None
        realized = round(self._goal_realized_pct, 2) if self._goal_loaded else None
        open_net = round(self._goal_open_net_pct(price), 2) if self._direction != 0 else 0.0
        view.update(
            realized_net_pct=realized, open_net_pct=open_net,
            session_net_pct=round(realized + open_net, 2) if realized is not None else None,
            in_position=self._direction != 0, done=bool(self._goal_done or outcome),
        )
        return view
