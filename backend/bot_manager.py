"""
bot_manager.py — fachada de compatibilidade.

O código vive em backend/bot/ (instance.py compõe os mixins criteria, entry, position, accounting e
streams; manager.py tem o BotManager). Este módulo mantém `from backend.bot_manager import manager`.
"""

from .bot.common import CANONICAL_ENTRY_CRITERIA, FIXED_STAKE_USD, log
from .bot.instance import BotInstance
from .bot.manager import BotManager, manager

__all__ = ["BotInstance", "BotManager", "manager", "FIXED_STAKE_USD", "CANONICAL_ENTRY_CRITERIA", "log"]
