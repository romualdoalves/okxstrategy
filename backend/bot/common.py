"""
Constantes e utilitários compartilhados pelos módulos de backend/bot/.
"""

from __future__ import annotations

import logging

log = logging.getLogger("bot_manager")

# Duplicado de main.py (não importado de lá para evitar import circular —
# main.py importa `manager` deste módulo). Stake fixo $100, inviolável (ver SKILL.md).
FIXED_STAKE_USD: float = 100.0

CANONICAL_ENTRY_CRITERIA: dict[str, list[str]] = {
    "TF002-1D": ["C1 RSI", "C2 MA"],
    "TF003-1D": ["C1 MACD", "C2 Histograma", "C3 Tendência MA"],
    "TF005-1D": ["C1 Tendência", "C2 Setup", "C3 Gatilho"],
    "TF006-1D": ["C1 ADX Semanal", "C2 Donchian 52W", "C3 Recuo EMA 21", "C4 Gatilho"],
    "TF007-1D": ["C1 4H Bias", "C2 1H Trend", "C3 Gatilho"],
    "TF008-1D": ["C1 Ciclo Macro", "C2 Suporte", "C3 Proximidade"],
    "TF010-1D": ["C1 Stoch K/D"],
    "TF013-1D": ["C1 Posição BB", "C2 Largura BB", "C3 Volume"],
    "PA004-15m": ["C1 3LB Pattern", "C2 EMA200", "C3 RSI", "C4 Volume", "C5 Risco"],
    "PA005-1H": ["C1 Suporte", "C2 Sweep", "C3 CHoCH"],
    "MR001-15m": ["C1 Zona", "C2 Cruzamento"],
    "T000": ["C1 Pipeline"],
}


class _LazyManager:
    """Acesso ao singleton `manager` sem import circular: bot/manager.py importa bot/instance.py,
    que importa os mixins; estes só precisam do manager em tempo de execução."""

    def __getattr__(self, name):
        from .manager import manager as _manager
        return getattr(_manager, name)


manager = _LazyManager()
