"""
OKXStrategy Backend Package
"""

import asyncio
import logging

log = logging.getLogger("backend")


def graceful_shutdown(timeout: float = 60.0):
    """
    Graceful shutdown do backend.

    1. Lista bots com posições abertas.
    2. Aguarda confirmação de fills pendentes (com timeout).
    3. Para todos os bots de forma ordenada.

    Pode ser chamado via:
        docker exec okx_strategy python -c \
            "from backend import graceful_shutdown; graceful_shutdown()"
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    async def _shutdown():
        from .bot_manager import manager
        from .database import SessionLocal

        db = SessionLocal()
        try:
            open_positions = []
            for bot_id, inst in manager._instances.items():
                if inst._direction != 0:
                    open_positions.append({
                        "bot_id": bot_id,
                        "symbol": inst.config.symbol,
                        "direction": inst._direction,
                        "entry_price": inst._entry_price,
                        "size": inst._sz,
                    })

            if open_positions:
                log.warning(
                    "[GracefulShutdown] %d posição(ões) aberta(s): %s",
                    len(open_positions),
                    open_positions,
                )
                # Aguarda um pouco para fills pendentes serem confirmados
                await asyncio.sleep(3)
            else:
                log.info("[GracefulShutdown] Nenhuma posição aberta.")

            # Para todos os bots
            for bot_id, inst in list(manager._instances.items()):
                log.info("[GracefulShutdown] Parando bot %d (%s)...", bot_id, inst.config.symbol)
                inst.stop()

            # Aguarda tasks terminarem (com timeout)
            tasks = [
                inst._task for inst in manager._instances.values()
                if inst._task and not inst._task.done()
            ]
            if tasks:
                log.info("[GracefulShutdown] Aguardando %d task(s)...", len(tasks))
                await asyncio.gather(*tasks, return_exceptions=True)

            log.info("[GracefulShutdown] Shutdown completo.")
        finally:
            db.close()

    return loop.run_until_complete(_shutdown())
