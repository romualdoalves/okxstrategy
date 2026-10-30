"""
Frontend estático (build do Vite) e fallback de SPA. Deve ser o ÚLTIMO router incluído:
o catch-all `/{full_path:path}` captura qualquer rota não atendida antes.
"""
from __future__ import annotations

from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from ..version import get_commit_info

STATIC_FRONTEND_DIR = Path(__file__).resolve().parents[1] / "static_frontend"
STATIC_INDEX = STATIC_FRONTEND_DIR / "index.html"
STATIC_ASSETS_DIR = STATIC_FRONTEND_DIR / "assets"

router = APIRouter()


def render_index(html: str, version: str) -> str:
    """Insere `<meta name="app-version">` no index.html: é a versão com que a aba foi carregada."""
    tag = f'<meta name="app-version" content="{escape(version, quote=True)}">'
    return html.replace("</head>", tag + "</head>", 1) if "</head>" in html else tag + html


@router.get("/{full_path:path}", include_in_schema=False)
def serve_spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(404, "Not found")
    if STATIC_INDEX.exists():
        html = render_index(STATIC_INDEX.read_text(encoding="utf-8"), get_commit_info()["version"])
        # Sem cache: depois de um deploy a aba precisa receber o index novo (os assets têm hash no nome).
        return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})
    raise HTTPException(404, "Frontend build not found")
