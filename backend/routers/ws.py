"""
WebSocket de tempo real: repassa os eventos dos bots (indicadores, ordens, P&L) ao frontend.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..bot_manager import manager

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    manager.register_ws(queue)
    try:
        while True:
            msg = await queue.get()
            await ws.send_text(msg)
    except WebSocketDisconnect:
        pass
    finally:
        manager.unregister_ws(queue)
