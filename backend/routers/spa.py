"""
Frontend estático (build do Vite) e fallback de SPA. Deve ser o ÚLTIMO router incluído:
o catch-all `/{full_path:path}` captura qualquer rota não atendida antes.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

STATIC_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "static_frontend"
STATIC_INDEX = STATIC_FRONTEND_DIR / "index.html"
STATIC_ASSETS_DIR = STATIC_FRONTEND_DIR / "assets"

router = APIRouter()


@router.get("/{full_path:path}", include_in_schema=False)
def serve_spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(404, "Not found")
    if STATIC_INDEX.exists():
        return FileResponse(STATIC_INDEX)
    raise HTTPException(404, "Frontend build not found")
