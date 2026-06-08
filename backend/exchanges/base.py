"""
exchanges/base.py — Contrato base para todas as exchanges.

Permite adicionar Binance, Bybit, etc. sem alterar o BotManager.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class CandleBar:
    epoch:  int     # ms
    open:   float
    high:   float
    low:    float
    close:  float
    volume: float

    @property
    def hlc3(self) -> float:
        return (self.high + self.low + self.close) / 3


@dataclass
class Position:
    symbol:          str
    side:            str      # "long" | "short"
    size:            float    # contratos (ou quantidade asset base em spot)
    avg_price:       float
    unrealized_pnl:  float
    unrealized_plpc: float = 0.0   # % não realizado informado pela exchange
    cost_basis:      float = 0.0
    change_today:    float = 0.0


class BaseExchange(ABC):

    # ── Mercado ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def fetch_candles(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> list[CandleBar]:
        """Retorna candles históricos em ordem cronológica."""

    @abstractmethod
    async def get_ticker(self, symbol: str) -> dict:
        """Retorna last, bid, ask, 24h vol."""

    # ── Conta ────────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_balance(self, currency: str = "USDT") -> float:
        """Saldo disponível."""

    @abstractmethod
    async def get_position(self, symbol: str) -> Optional[Position]:
        """Posição aberta ou None."""

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> None:
        """Configura alavancagem."""

    # ── Ordens ───────────────────────────────────────────────────────────────

    @abstractmethod
    async def market_order(
        self, symbol: str, side: str, size: float, reduce_only: bool = False
    ) -> Optional[str]:
        """Ordem a mercado. Retorna order_id ou None se falhar."""

    @abstractmethod
    async def place_stop_loss(
        self, symbol: str, side: str, size: float, trigger_price: float
    ) -> Optional[str]:
        """Cria ordem de stop-loss. Retorna algo_id."""

    @abstractmethod
    async def place_trailing_stop(
        self, symbol: str, side: str, size: float, callback_ratio: float,
        activation_price: float = 0.0
    ) -> Optional[str]:
        """Cria trailing stop nativo. Retorna algo_id."""

    @abstractmethod
    async def cancel_algo(self, symbol: str, algo_id: str) -> None:
        """Cancela uma ordem algo (SL ou trailing)."""

    async def cancel_all_algos(self) -> int:
        """Cancela todas as ordens algo pendentes na exchange. Retorna o número de ordens canceladas."""
        return 0

    async def close_all_positions(self) -> int:
        """Fecha todas as posições abertas e cancela ordens na exchange. Retorna o número de posições liquidadas."""
        return 0

    async def get_order(self, order_id: str) -> Optional[dict]:
        """Busca dados de uma ordem pelo ID. Retorna None se não suportado."""
        return None

    async def liquidate_position(self, symbol: str) -> Optional[str]:
        """Liquida a posição aberta na exchange. Retorna o ID da ordem de encerramento ou None se falhar."""
        return None

    async def get_clock(self) -> dict:
        """Estado do mercado: is_open, next_open, next_close. Retorna {} se não suportado."""
        return {}

    async def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        """Ordens abertas. Retorna [] se não suportado."""
        return []

    async def get_all_positions(self) -> list[dict]:
        """Todas as posições abertas. Retorna [] se não suportado."""
        return []

    # ── Helpers ──────────────────────────────────────────────────────────────

    @abstractmethod
    def get_contract_size(self, symbol: str) -> float:
        """Retorna o valor do multiplicador do contrato (1.0 para spot)."""

    @abstractmethod
    def num_contracts(self, symbol: str, price: float, stake_usd: float, leverage: int) -> float:
        """Calcula número de contratos ou quantidade para o stake/leverage dados."""
