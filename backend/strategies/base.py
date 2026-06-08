"""
strategies/base.py — Contrato base para todas as estratégias.

Cada estratégia implementa:
  - info()    → metadados (nome, descrição, parâmetros)
  - compute() → recebe candles, devolve sinal + indicadores
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Signal(str, Enum):
    BUY  = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class StrategyResult:
    signal:     Signal
    indicators: dict[str, float]        # valores a plotar no gráfico
    metadata:   dict[str, Any] = field(default_factory=dict)
    hold_reason: str = ""               # por que está em HOLD (vazio se BUY/SELL)
    criteria_met: int = 0
    criteria_total: int = 0


@dataclass
class ParamDef:
    """Define um parâmetro editável pelo usuário."""
    type:        str            # "int" | "float" | "bool"
    default:     Any
    description: str
    min:         Any = None
    max:         Any = None
    step:        Any = None


@dataclass
class StrategyInfo:
    id: str
    name: str
    description: str
    params: dict[str, ParamDef]
    recommended_timeframe: str = "15m"
    recommended_symbol: str = ""
    tags: list[str] = field(default_factory=list)
    extra_timeframes: list[str] = field(default_factory=list)
    criteria: list[dict] = field(default_factory=list)


class BaseStrategy(ABC):
    """
    Classe abstrata que toda estratégia deve estender.

    Exemplo mínimo:
        class MyStrategy(BaseStrategy):
            @classmethod
            def info(cls) -> StrategyInfo: ...
            def compute(self, candles) -> Optional[StrategyResult]: ...
            def set_params(self, params: dict): ...
    """

    @classmethod
    @abstractmethod
    def info(cls) -> StrategyInfo:
        """Retorna metadados estáticos da estratégia."""

    @abstractmethod
    def compute(self, candles: list) -> Optional[StrategyResult]:
        """
        Processa a lista de CandleBar e devolve um sinal.
        Retorna None se não houver dados suficientes.
        """

    @abstractmethod
    def set_params(self, params: dict) -> None:
        """Aplica parâmetros personalizados (vindos da UI)."""

    @classmethod
    def extra_timeframes(cls) -> list[str]:
        """
        Lista de barcodes OKX extras a pré-buscar (ex: ['1H', '4H']).
        Estratégias multi-TF sobrescrevem este método.
        O bot_manager lê este atributo e popula context['extra_candles'].
        """
        return []

    def compute_with_context(
        self,
        candles: list,
        context: dict | None = None,
    ) -> Optional[StrategyResult]:
        """
        Ponto de entrada para o bot_manager. Estratégias simples não precisam
        sobrescrever — a implementação padrão apenas delega para compute().
        Estratégias multi-TF sobrescrevem este método e leem
        context['extra_candles'] = { '1H': [...], '4H': [...] }.
        """
        return self.compute(candles)
