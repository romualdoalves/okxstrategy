"""
Rotas de estratégias e da Fábrica de Estratégias (LLM).
"""

from __future__ import annotations
from fastapi import APIRouter

import logging

from pydantic import BaseModel
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import StrategyModel as FactoryStrategyModel, SessionLocal, get_db
from ..strategies.registry import list_strategies

router = APIRouter()
log = logging.getLogger("main")


# ── Rotas: Estratégias ────────────────────────────────────────────────────────
@router.get("/api/strategies")
def get_strategies():
    """Lista todas as estratégias disponíveis com metadados e parâmetros."""
    return list_strategies()


# ── Rotas: Fábrica de Estratégias ─────────────────────────────────────────────
class FactoryPlanRequest(BaseModel):
    description: str

class FactoryGenerateRequest(BaseModel):
    plan: dict

class FactoryValidateRequest(BaseModel):
    code: str
    plan: dict

class FactoryFixRequest(BaseModel):
    code: str
    plan: dict
    errors: list[str]   # apenas os checks que falharam

class FactoryDeployRequest(BaseModel):
    code: str
    plan: dict
    source_text: str = ""
    approved: bool = False   # confirmação explícita de que o código foi revisado

@router.get("/api/strategies/{strategy_id}/study")
def strategy_study(strategy_id: str):
    """Relatório do estudo (baseline, walk-forward, por ano) exibido no (i) do bot."""
    from ..validation.reports import load_study
    return load_study(strategy_id)


@router.get("/api/strategy-factory/status")
def factory_status():
    """Verifica se a KIMI está configurada."""
    from ..strategy_factory.kimi_client import is_configured, get_model, get_base_url, get_provider
    return {
        "configured": is_configured(),
        "model":      get_model(),
        "base_url":   get_base_url(),
        "provider":   get_provider(),
    }

@router.post("/api/strategy-factory/plan")
async def factory_plan(req: FactoryPlanRequest):
    """Etapa 1 — Descrição → Plano JSON."""
    from ..strategy_factory.planner import generate_plan
    try:
        plan = await generate_plan(req.description)
        # Atribui o próximo ID disponível
        db = SessionLocal()
        try:
            used = [r[0] for r in db.query(FactoryStrategyModel.strategy_id).all()]
        finally:
            db.close()
        from ..strategy_factory.deployer import assign_next_id
        # Usa o prefixo da categoria semântica escolhida pela IA
        prefix = plan.get("category", "TF")
        if not isinstance(prefix, str) or prefix not in ("TF", "MR", "PA", "SC", "RG", "IF", "NW"):
            prefix = "TF"  # fallback seguro
        plan["id"] = assign_next_id(used, prefix=prefix)
        # Substitui placeholder pelo ID real
        placeholder = plan.get("id", "TF001")[:2] + "001"
        if isinstance(plan.get("name"), str):
            plan["name"] = plan["name"].replace(placeholder, plan["id"], 1)
        # Remove qualquer placeholder legado (não deve acontecer com IA atualizada)
        return {"plan": plan}
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        import traceback
        log.error("Erro no factory_plan:\n%s", traceback.format_exc())
        raise HTTPException(400, f"Erro fatal ao gerar plano: {type(e).__name__} - {e}")

@router.post("/api/strategy-factory/generate")
async def factory_generate(req: FactoryGenerateRequest):
    """Etapa 2 — Plano aprovado → código Python."""
    from ..strategy_factory.generator import generate_code
    import logging
    log = logging.getLogger("strategy_factory.api")
    try:
        code = await generate_code(req.plan)
        return {"code": code}
    except RuntimeError as e:
        log.error("[Fábrica] Erro na geração: %s", e)
        raise HTTPException(400, str(e))
    except Exception as e:
        log.error("[Fábrica] Erro inesperado na geração: %s", e, exc_info=True)
        raise HTTPException(500, f"Erro interno ao gerar código: {e}")

@router.post("/api/strategy-factory/fix")
async def factory_fix(req: FactoryFixRequest):
    """Corrige cirurgicamente erros específicos no código existente."""
    from ..strategy_factory.generator import fix_code
    from ..strategy_factory.validator import validate_code
    import logging
    log = logging.getLogger("strategy_factory.api")
    
    if not req.errors:
        raise HTTPException(400, "Nenhum erro fornecido para corrigir.")
    
    log.info("[Fábrica] Correção solicitada: %d erros, código=%d chars", len(req.errors), len(req.code))
    try:
        fixed_code = await fix_code(req.code, req.errors, req.plan)
        log.info("[Fábrica] Código corrigido: %d chars", len(fixed_code))
        report = validate_code(fixed_code, req.plan)
        log.info("[Fábrica] Validação após correção: passed=%s, checks=%d", report.passed, len(report.checks))
        return {"code": fixed_code, "validation": report.summary()}
    except RuntimeError as e:
        log.error("[Fábrica] Erro na correção: %s", e)
        raise HTTPException(400, str(e))
    except Exception as e:
        log.error("[Fábrica] Erro inesperado na correção: %s", e, exc_info=True)
        raise HTTPException(500, f"Erro interno ao corrigir: {e}")

@router.post("/api/strategy-factory/validate")
def factory_validate(req: FactoryValidateRequest):
    """Etapa 3 — Código gerado → ValidationReport."""
    from ..strategy_factory.validator import validate_code
    try:
        report = validate_code(req.code, req.plan)
        return report.summary()
    except Exception as e:
        raise HTTPException(500, f"Erro interno na validação: {e}")

@router.post("/api/strategy-factory/deploy")
def factory_deploy(req: FactoryDeployRequest, db: Session = Depends(get_db)):
    """Etapa 4 — Implanta a estratégia: escreve arquivo + hot-load + persiste no banco."""
    if not req.approved:
        raise HTTPException(400, "Implantação exige aprovação explícita do código (approved=true).")
    from ..strategy_factory.deployer import deploy
    from ..strategy_factory.validator import validate_code

    plan = req.plan
    strategy_id = plan.get("id")
    valid_prefixes = ("TF", "MR", "PA", "SC", "RG", "IF", "NW", "T")
    if not strategy_id or not any(strategy_id.startswith(p) for p in valid_prefixes):
        raise HTTPException(400, f"ID de estratégia inválido — deve começar com um dos prefixos: {', '.join(valid_prefixes)}")

    # Revalida antes de implantar
    report = validate_code(req.code, plan)
    if not report.passed:
        fails = [c["name"] for c in report.checks if not c["ok"]]
        raise HTTPException(400, f"Validação falhou: {fails}")

    try:
        class_name = deploy(strategy_id, req.code, plan)
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    # Persiste no banco
    from datetime import datetime, timezone
    existing = db.query(FactoryStrategyModel).filter_by(strategy_id=strategy_id).first()
    if existing:
        existing.name        = plan.get("name", strategy_id)
        existing.description = plan.get("description", "")
        existing.source_text = req.source_text
        existing.plan_json   = plan
        existing.status      = "deployed"
        existing.deployed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        existing.validation_report = report.summary()
    else:
        db.add(FactoryStrategyModel(
            strategy_id      = strategy_id,
            name             = plan.get("name", strategy_id),
            description      = plan.get("description", ""),
            source_text      = req.source_text,
            plan_json        = plan,
            status           = "deployed",
            deployed_at      = datetime.now(timezone.utc).replace(tzinfo=None),
            validation_report = report.summary(),
        ))
    db.commit()

    log.info("Fábrica: estratégia %s implantada (%s).", strategy_id, class_name)
    return {
        "strategy_id": strategy_id, "class_name": class_name, "status": "deployed",
        # O código vive só em arquivo (git é a fonte única). Enquanto não estiver no repositório, o deploy
        # do VPS é abortado para não perdê-lo: rode scripts/migrate_factory_to_git.sh.
        "versioned": False,
        "next_step": "bash scripts/migrate_factory_to_git.sh (no VPS) para levar o arquivo ao git",
    }

@router.get("/api/strategy-factory/strategies")
def factory_list(db: Session = Depends(get_db)):
    """Lista todas as estratégias criadas pela fábrica."""
    rows = db.query(FactoryStrategyModel).order_by(FactoryStrategyModel.id.desc()).all()
    return [
        {
            "id":           r.id,
            "strategy_id":  r.strategy_id,
            "name":         r.name,
            "description":  r.description,
            "status":       r.status,
            "created_at":   r.created_at.isoformat() if r.created_at else None,
            "deployed_at":  r.deployed_at.isoformat() if r.deployed_at else None,
        }
        for r in rows
    ]

@router.get("/api/strategy-factory/strategies/{strategy_id}/code")
def factory_get_code(strategy_id: str, db: Session = Depends(get_db)):
    """Retorna o código Python de uma estratégia de fábrica (lido do arquivo; code_py é legado)."""
    from ..strategy_factory.deployer import get_factory_file
    row = db.query(FactoryStrategyModel).filter_by(strategy_id=strategy_id.upper()).first()
    if not row:
        raise HTTPException(404, "Estratégia não encontrada")
    code = get_factory_file(row.strategy_id) or row.code_py
    return {"strategy_id": row.strategy_id, "code": code, "plan": row.plan_json}

@router.delete("/api/strategy-factory/strategies/{strategy_id}/permanent")
def factory_delete_permanent(strategy_id: str, db: Session = Depends(get_db)):
    """Apaga permanentemente uma estratégia de fábrica (arquivo + registry + banco)."""
    from ..strategy_factory.deployer import remove, _FACTORY_DIR
    sid = strategy_id.upper()
    row = db.query(FactoryStrategyModel).filter_by(strategy_id=sid).first()
    if not row:
        raise HTTPException(404, "Estratégia não encontrada")
    
    # Remove do REGISTRY
    removed = remove(sid)
    
    # Apaga o arquivo
    filepath = _FACTORY_DIR / f"{sid.lower()}.py"
    file_deleted = False
    if filepath.exists():
        filepath.unlink()
        file_deleted = True
    
    # Apaga do banco
    db.delete(row)
    db.commit()
    
    return {
        "strategy_id": sid,
        "removed_from_registry": removed,
        "file_deleted": file_deleted,
    }

@router.delete("/api/strategy-factory/strategies/{strategy_id}")
def factory_disable(strategy_id: str, db: Session = Depends(get_db)):
    """Desativa uma estratégia de fábrica (remove do REGISTRY, preserva arquivo)."""
    from ..strategy_factory.deployer import remove
    sid = strategy_id.upper()
    row = db.query(FactoryStrategyModel).filter_by(strategy_id=sid).first()
    if not row:
        raise HTTPException(404, "Estratégia não encontrada")
    removed = remove(sid)
    row.status = "disabled"
    db.commit()
    return {"strategy_id": sid, "removed_from_registry": removed}
