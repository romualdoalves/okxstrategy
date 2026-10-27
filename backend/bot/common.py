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
    "TF001": ["C1 EMA", "C2 VWAP", "C3 ATR"],
    "TF002": ["C1 RSI", "C2 MA"],
    "TF003": ["C1 MACD", "C2 Histograma", "C3 Tendência MA"],
    "TF004": ["C1 SuperTrend", "C2 RSI", "C3 Distância ST"],
    "TF005": ["C1 Tendência", "C2 Setup", "C3 Gatilho"],
    "TF006": ["C1 ADX Semanal", "C2 Donchian 52W", "C3 Recuo EMA 21", "C4 Gatilho"],
    "TF007": ["C1 4H Bias", "C2 1H Trend", "C3 Gatilho"],
    "TF008": ["C1 Ciclo Macro", "C2 Suporte", "C3 Proximidade"],
    "TF010": ["C1 Stoch K/D"],
    "TF011": ["C1 Regime", "C2 EMA", "C3 Volume", "C4 RSI Slope"],
    "TF012": ["C1 Tendência", "C2 Liquidez", "C3 Gatilho"],
    "TF013": ["C1 Posição BB", "C2 Largura BB", "C3 Volume"],
    "PA001": ["C1 Tendência 1-2-3", "C2 Zona EMA+S/R", "C3 Candle de rejeição"],
    "PA002": ["C1 AB≈CD", "C2 EMA21"],
    "PA003": ["C1 Zona", "C2 Range/ATR", "C3 Onda", "C4 Gatilho", "C5 R:R"],
    "PA004": ["C1 3LB Pattern", "C2 EMA200", "C3 RSI", "C4 Volume", "C5 Risco"],
    "PA005": ["C1 Suporte", "C2 Sweep", "C3 CHoCH"],
    "PA006": ["C1 Sweep HTF", "C2 Rejeição", "C3 CHoCH", "C4 Fibonacci", "C5 Liquidez"],
    "PA007": ["C1 Viés", "C2 Volume", "C3 Zona", "C4 CHoCH"],
    "PA008": ["C1 ORB", "C2 Rompimento", "C3 Reteste"],
    "MR001": ["C1 Zona", "C2 Cruzamento"],
    "MR002": ["C1 Banda 4σ", "C2 Banda 2σ", "C3 Zona R/S"],
    "MR003": ["C1 Exaustão", "C2 RSI", "C3 MACD", "C4 Gatilho"],
    "MR004": ["C1 RSI 50", "C2 Momentum", "C3 Volume", "C4 Gap"],
    "MR005": ["C1 RSI", "C2 Posição BB", "C3 SMA 20"],
    "SC001": ["C1 Manipulação", "C2 Padrão Rejeição"],
    "SC002": ["C1 Viés VWAP", "C2 Rejeição", "C3 Força", "C4 Confirmação", "C5 Players", "C6 Volume", "C7 Risco"],
    "SC003": ["C1 Zona Inst.", "C2 Delta", "C3 Clímax"],
    "IF001": ["C1 Whale score"],
    "IF002": ["C1 Spread", "C2 Net Spread", "C3 Liquidez", "C4 Dados"],
    "IF003": ["C1 Liquidez", "C2 Atemporal", "C3 Agressão", "C4 Confirmação"],
    "RG001": ["C1 Sinal", "C2 Regime", "C3 Persistência", "C4 Estacionário"],
    "RG002": ["C1 Regime", "C2 Confiança"],
    "RG003": ["C1 Regime GEX", "C2 Nível OI", "C3 Volume", "C4 ATR"],
    "RG004": ["C1 Localização", "C2 Frequência", "C3 Absorção", "C4 Markov"],
    "NW001": ["C1 Líder", "C2 Seguidor", "C3 Impulso", "C4 RSI Seguidor"],
    "T000": ["C1 Pipeline"],
}


class _LazyManager:
    """Acesso ao singleton `manager` sem import circular: bot/manager.py importa bot/instance.py,
    que importa os mixins; estes só precisam do manager em tempo de execução."""

    def __getattr__(self, name):
        from .manager import manager as _manager
        return getattr(_manager, name)


manager = _LazyManager()
