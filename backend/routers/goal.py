"""Rotas da "Meta 1% Hoje!": busca em segundo plano e estado/resultado."""
from __future__ import annotations

from fastapi import APIRouter

from .. import goal_service

router = APIRouter()


@router.post("/api/goal/scan")
async def goal_scan(force: bool = False):
    """Inicia a busca (ou devolve a em andamento / o cache recente, se `force` for falso)."""
    return await goal_service.start_scan(force=force)


@router.get("/api/goal/status")
async def goal_status():
    """Estado da busca + resultado, com cada candidato marcado se o ativo ainda está no top 5 AGORA."""
    return await goal_service.live_status()
