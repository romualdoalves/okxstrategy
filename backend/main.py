"""
main.py — FastAPI: REST API + WebSocket hub — OKXStrategy.
"""

from __future__ import annotations
import aiohttp
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

try:
    from dotenv import load_dotenv
    # Carrega OKXStrategy/.env primeiro (tem EXCHANGE_PROVIDER=okx, DATABASE_URL okx_strategy)
    # override=False: não sobrescreve vars já definidas no ambiente (ex: set do .bat)
    _okx_env = Path(__file__).resolve().parents[2] / "OKXStrategy" / ".env"
    if _okx_env.exists():
        load_dotenv(_okx_env, override=False)
    # Fallback local: OKXStrategy/.env ao executar diretamente a partir do repo
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False, encoding="utf-8")
except ImportError:
    pass

from .database import (
    AiAnalysisLogModel,
    AutoScanHistoryModel,
    BotModel,
    BotSnapshotModel,
    StrategyModel as FactoryStrategyModel,  # alias para compatibilidade temporária
    OrderRejectionModel,
    SettingsModel,
    SignalLogModel,
    TradeModel,
    TradeReportModel,
    SessionLocal,
    create_tables,
    get_db,
)
from .bot_manager import manager
from .backtest_engine import BacktestEngine, backtest_recommendation, backtest_score
from .optimizer import StrategyOptimizer
from .exchanges.factory import build_exchange, get_default_demo_mode, get_exchange_provider, get_ranked_assets_universe, map_timeframe_for_history
from .strategies.registry import list_strategies, get_strategy
from .feeds.economic_calendar import calendar_feed
from .notifications import TelegramNotifier, build_balance_snapshot_msg, build_stop_msg

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
log = logging.getLogger("main")

# Offset do fuso horário local em horas (ex: -3 para BRT). Usado para converter
# datas UTC da OKX para o dia local correto.
_TZ_OFFSET: int = int(os.getenv("TZ_OFFSET", "0"))
FIXED_STAKE_USD: float = 100.0


def _local_date_to_utc_range(date_str: str) -> tuple[str, str]:
    """Converte uma data local (YYYY-MM-DD) para o intervalo UTC equivalente."""
    import datetime as _dt
    local_day = _dt.datetime.fromisoformat(date_str)
    utc_start = local_day + _dt.timedelta(hours=-_TZ_OFFSET)
    utc_end   = utc_start + _dt.timedelta(days=1)
    return utc_start.strftime("%Y-%m-%dT%H:%M:%SZ"), utc_end.strftime("%Y-%m-%dT%H:%M:%SZ")

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="OKXStrategy API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_FRONTEND_DIR = Path(__file__).resolve().parent / "static_frontend"
STATIC_INDEX = STATIC_FRONTEND_DIR / "index.html"
STATIC_ASSETS_DIR = STATIC_FRONTEND_DIR / "assets"

if STATIC_ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_ASSETS_DIR), name="assets")


def _env_truthy(name: str, default: str = "") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _norm_symbol(symbol: str) -> str:
    s = (symbol or "").strip().upper().replace("/", "-")
    if s.endswith("-SWAP"):
        s = s[:-5]
    if s.endswith("-FUTURES"):
        s = s[:-8]
    return s


def _assert_spot_only(symbol: str):
    s = (symbol or "").strip().upper()
    if s.endswith("-SWAP") or s.endswith("-FUTURES"):
        raise HTTPException(400, "Este app opera somente OKX spot. Use o par sem -SWAP/-FUTURES.")


def _find_symbol_conflict(db: Session, symbol: str, *, exclude_bot_id: int | None = None, active_only: bool = False):
    query = db.query(BotModel).filter(func.upper(BotModel.symbol) == _norm_symbol(symbol))
    if exclude_bot_id is not None:
        query = query.filter(BotModel.id != exclude_bot_id)
    if active_only:
        query = query.filter(BotModel.active == True)
    return query.first()


def _assert_symbol_available(db: Session, symbol: str, *, exclude_bot_id: int | None = None, active_only: bool = False):
    conflict = _find_symbol_conflict(
        db,
        symbol,
        exclude_bot_id=exclude_bot_id,
        active_only=active_only,
    )
    if conflict:
        scope = "em execução" if active_only else "configurado"
        raise HTTPException(
            400,
            f"Já existe um bot {scope} para o ativo {_norm_symbol(symbol)} ('{conflict.name}'). "
            "OKX mantém uma única posição por ativo — use um ativo diferente."
        )


import os
import time

_cached_commit_hash = None
_cached_commit_timestamp = None

@app.get("/api/health")
def health_check():
    global _cached_commit_hash, _cached_commit_timestamp
    if not _cached_commit_hash:
        try:
            with open('.git/HEAD', 'r') as f:
                ref = f.read().strip()
            if ref.startswith('ref: '):
                with open(f".git/{ref[5:]}", 'r') as f:
                    _cached_commit_hash = f.read().strip()[:7]
            else:
                _cached_commit_hash = ref[:7]
                
            with open('.git/logs/HEAD', 'r') as f:
                lines = f.readlines()
                if lines:
                    last_line = lines[-1].strip()
                    parts = last_line.split('\t')[0].split(' ')
                    _cached_commit_timestamp = int(parts[-2])
        except Exception:
            _cached_commit_hash = "unknown"
            _cached_commit_timestamp = None
            
    return {
        "ok": True, 
        "version": _cached_commit_hash,
        "timestamp": _cached_commit_timestamp
    }


@app.head("/api/health")
def health_check_head():
    return None


@app.get("/api/monitor")
async def monitor_bots():
    """Retorna status de todos os bots ativos para monitoramento."""
    from .bot_manager import manager
    statuses = manager.all_statuses()
    
    # Enriquece com dados do banco
    db = SessionLocal()
    try:
        bots = db.query(BotModel).all()
        bot_map = {b.id: {"name": b.name, "symbol": b.symbol, "timeframe": b.timeframe,
                          "strategy_id": b.strategy_id, "active": b.active, "stake_usd": b.stake_usd,
                          "baseline_balance": b.baseline_balance or 0.0} for b in bots}
    finally:
        db.close()
    
    async def _exchange_positions():
        if not statuses:
            return {}
        try:
            import aiohttp as _aio
            async with _aio.ClientSession() as session:
                ex = build_exchange(session)
                pos_results = await asyncio.gather(
                    *[ex.get_position(bot_map.get(st.get("bot_id"), {}).get("symbol", "")) for st in statuses],
                    return_exceptions=True,
                )
            return {st.get("bot_id"): pos for st, pos in zip(statuses, pos_results)}
        except Exception as exc:
            log.warning("Monitor: falha ao buscar posições OKX: %s", exc)
            return {st.get("bot_id"): exc for st in statuses}

    exchange_pos = await _exchange_positions()

    result = []
    for st in statuses:
        bot_id = st.get("bot_id")
        info = bot_map.get(bot_id, {})
        
        # Calcula PnL em tempo real
        direction = st.get("direction", 0)
        entry = st.get("entry_price", 0)
        last = st.get("last_price", 0)
        pnl_pct = 0.0
        pnl_usd = 0.0
        if direction != 0 and entry > 0 and last > 0:
            pnl_pct = (last - entry) / entry * direction * 100
            base_qty = float(st.get("size", 0) or 0)
            if base_qty > 0:
                pnl_usd = base_qty * (last - entry) * direction
            else:
                pnl_usd = FIXED_STAKE_USD * pnl_pct / 100

        pos = exchange_pos.get(bot_id)
        okx_size = 0.0
        app_notional = 0.0
        okx_notional = 0.0
        okx_direction = "FLAT"
        sync_status = "unknown"
        sync_detail = "OKX ainda não verificada."
        if isinstance(pos, Exception):
            sync_status = "error"
            sync_detail = f"Falha ao consultar OKX: {pos}"
        else:
            okx_size_raw = abs(float(getattr(pos, "size", 0.0) or 0.0)) if pos else 0.0
            baseline = float(info.get("baseline_balance") or 0.0)
            okx_size = max(0.0, okx_size_raw - baseline)  # desconsidera holdings pré-existentes
            okx_direction = "LONG" if pos and getattr(pos, "side", "") == "long" else ("SHORT" if pos else "FLAT")
            local_size = abs(float(st.get("size", 0.0) or 0.0))
            notional_price = float(last or 0.0)
            if notional_price <= 0 and pos:
                notional_price = float(getattr(pos, "avg_price", 0.0) or 0.0)
            if notional_price > 0:
                app_notional = local_size * notional_price
                okx_notional = okx_size * notional_price
            entry_notional_price = float(entry or notional_price or 0.0)
            okx_entry_notional = okx_size * entry_notional_price if entry_notional_price > 0 else 0.0
            notional_tolerance = max(2.0, FIXED_STAKE_USD * 0.05)
            local_open = direction != 0
            okx_open = okx_size > 1e-9
            if local_open and okx_open:
                local_direction = "LONG" if direction == 1 else "SHORT"
                if local_direction != okx_direction:
                    sync_status = "divergent"
                    sync_detail = f"Direção divergente: App {local_direction}, OKX {okx_direction}."
                elif local_size > 0 and abs(okx_size - local_size) > max(1e-8, local_size * 0.001):
                    sync_status = "divergent"
                    sync_detail = f"Tamanho divergente: App {local_size:.8f}, OKX {okx_size:.8f}."
                elif okx_entry_notional > 0 and abs(okx_entry_notional - FIXED_STAKE_USD) > notional_tolerance:
                    sync_status = "divergent"
                    sync_detail = (
                        f"Notional de entrada divergente: OKX ${okx_entry_notional:.2f}, "
                        f"esperado ${FIXED_STAKE_USD:.2f} por stake fixo."
                    )
                else:
                    sync_status = "ok"
                    sync_detail = "App e OKX com posição aberta correspondente."
            elif not local_open and not okx_open:
                sync_status = "ok"
                sync_detail = "App e OKX flat."
            elif local_open and not okx_open:
                sync_status = "divergent"
                sync_detail = "App mostra posição aberta, mas OKX está flat."
            else:
                sync_status = "divergent"
                sync_detail = "OKX mostra posição aberta, mas app está flat."
        
        result.append({
            "bot_id": bot_id,
            "name": info.get("name", "—"),
            "symbol": info.get("symbol", "—"),
            "timeframe": info.get("timeframe", "—"),
            "strategy_id": info.get("strategy_id", "—"),
            "active": info.get("active", False),
            "status": st.get("status", "—"),
            "direction": "LONG" if direction == 1 else ("SHORT" if direction == -1 else "FLAT"),
            "entry_price": round(entry, 4) if entry else None,
            "last_price": round(last, 4) if last else None,
            "sl_price": round(st.get("sl_price"), 4) if st.get("sl_price") else None,
            "tp1_price": round(st.get("tp1_price"), 4) if st.get("tp1_price") else None,
            "tp1_done": st.get("tp1_done", False),
            "size": round(float(st.get("size", 0.0) or 0.0), 8),
            "okx_direction": okx_direction,
            "okx_size": round(okx_size, 8),
            "app_notional": round(app_notional, 2),
            "okx_notional": round(okx_notional, 2),
            "okx_entry_notional": round(okx_entry_notional, 2),
            "expected_notional": FIXED_STAKE_USD if direction != 0 else 0.0,
            "sync_status": sync_status,
            "sync_detail": sync_detail,
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usd": round(pnl_usd, 2),
            "guaranteed_pnl": st.get("guaranteed_pnl", 0),
            "daily_pnl": st.get("daily_pnl", 0),
            "wins": st.get("wins", 0),
            "losses": st.get("losses", 0),
            "halted": st.get("halted", False),
            "hold_reason": st.get("hold_reason", ""),
            "last_update": st.get("last_update"),
        })
    
    return {"bots": result, "count": len(result), "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/system/ai-usage")
def system_ai_usage():
    deepseek_enabled = bool(os.getenv("DEEPSEEK_API_KEY", "").strip())
    provider = get_exchange_provider()
    return {
        "exchange_provider": provider,
        "market_data": {
            "mode": f"{provider}_rest",
            "uses_mcp": False,
            "consumes_ai_tokens": False,
        },
        "actions": {
            "asset_ranking": {
                "consumes_ai_tokens": False,
                "source": "local_market_scoring",
            },
            "strategy_optimization": {
                "consumes_ai_tokens": False,
                "source": "local_statistical_analysis",
            },
            "signal_logs_analysis": {
                "consumes_ai_tokens": False,
                "source": "local_statistical_analysis",
            },
            "bot_ai_analysis": {
                "consumes_ai_tokens": deepseek_enabled,
                "source": "deepseek" if deepseek_enabled else "disabled",
            },
            "graph_interpretation": {
                "consumes_ai_tokens": deepseek_enabled,
                "source": "deepseek" if deepseek_enabled else "rules_fallback",
            },
        },
    }

@app.get("/api/system/telegram-status")
def telegram_status():
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID",   "").strip()
    return {
        "enabled":         bool(token and chat_id),
        "token_set":       bool(token),
        "chat_id_set":     bool(chat_id),
        "token_preview":   f"{token[:6]}…" if token else None,
        "chat_id_preview": chat_id if chat_id else None,
    }


@app.post("/api/system/telegram-test")
async def telegram_test():
    import aiohttp as _aio

    n = TelegramNotifier()
    if not n.enabled:
        raise HTTPException(400, "Telegram não configurado — defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env do servidor.")
    async with _aio.ClientSession() as session:
        ex = build_exchange(session)
        try:
            account, positions = await asyncio.gather(
                ex.get_account_summary(),
                ex.get_all_positions(),
                return_exceptions=True,
            )
        except Exception as exc:
            raise HTTPException(502, f"Não foi possível obter saldos oficiais: {exc}") from exc
    if isinstance(account, Exception):
        raise HTTPException(502, f"Não foi possível obter saldos oficiais: {account}")
    if isinstance(positions, Exception):
        positions = []
    ok = await n.send(build_balance_snapshot_msg(
        account=account or {},
        positions=positions or [],
        provider=get_exchange_provider().upper(),
    ))
    if not ok:
        raise HTTPException(
            502,
            n.last_error or "Telegram configurado mas falhou ao enviar. Verifique o token e o chat_id.",
        )
    return {"ok": True, "message": "Saldos oficiais enviados com sucesso."}


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    create_tables()
    log.info("Banco de dados inicializado.")

    # Inicia o feed do calendário económico do provider atual, se configurado
    await calendar_feed.start()

    # Migração automática: remove -SWAP e força stake fixo global de 100
    db = SessionLocal()
    try:
        from sqlalchemy import text
        # Migração automática de IDs legados → taxonomia semântica
        # F001/F002 (Factory antiga) → semântico
        db.execute(text("UPDATE bots SET strategy_id = 'TF014' WHERE strategy_id = 'F001'"))
        db.execute(text("UPDATE bots SET strategy_id = 'PA007' WHERE strategy_id = 'F002'"))
        
        # Migração Global de Timeframes para os recomendados (nova taxonomia)
        tf_recommendations = {
            "TF008": "1D",
            "PA002": "4h", "IF001": "4h",
            "TF002": "1h", "TF003": "1h", "TF011": "1h", "TF007": "1h", "MR001": "1h", "PA003": "1h", "RG002": "1h",
            "TF001": "15m", "TF013": "15m", "TF004": "15m", "PA001": "15m", "TF010": "15m", "SC003": "15m", "NW001": "15m",
            "SC001": "5m",
            "PA004": "15m",
            "MR002": "15m",
            "MR005": "15m",
            "PA005": "1h",
            "IF002": "5m",
        }
        for strat_id, tf in tf_recommendations.items():
            db.execute(text("UPDATE bots SET timeframe = :tf WHERE strategy_id = :sid"), {"tf": tf, "sid": strat_id})

        db.execute(text("UPDATE bots SET symbol = REPLACE(symbol, '-SWAP', '') WHERE symbol LIKE '%-SWAP'"))
        db.execute(text("UPDATE bots SET symbol = REPLACE(symbol, '-FUTURES', '') WHERE symbol LIKE '%-FUTURES'"))
        db.execute(text("UPDATE bots SET leverage = 1 WHERE leverage IS NULL OR leverage <> 1"))
        db.execute(text("UPDATE bots SET stake_usd = 100.0 WHERE stake_usd IS NULL OR stake_usd <> 100.0"))
        db.commit()
        log.info("Migração de IDs, símbolos, stakes e TIMEFRAMES concluída.")

        active = db.query(BotModel).filter(BotModel.active == True).all()
        for bot in active:
            try:
                manager.start_bot(bot)
                log.info("Bot %d re-iniciado automaticamente.", bot.id)
            except ValueError as ve:
                log.warning("Bot %d não iniciado: %s", bot.id, ve)
            except Exception as e:
                log.error("Bot %d falhou no startup: %s", bot.id, e)
    finally:
        db.close()

    # Reconciliação periódica: detecta posições fechadas externamente na OKX
    asyncio.create_task(manager._reconcile_loop())

    # Sincronização de taxas de corretagem: preenche fee nos trades recentes sem dados
    async def _startup_fee_sync():
        await asyncio.sleep(10)   # aguarda exchange estar disponível
        db_s = SessionLocal()
        try:
            result = await _sync_trade_fees_impl(db_s, days_back=30)
            log.info("Sync de corretagem no startup: %s", result)
        except Exception as exc:
            log.warning("Sync de corretagem no startup falhou: %s", exc)
        finally:
            db_s.close()
    asyncio.create_task(_startup_fee_sync())


@app.on_event("shutdown")
async def shutdown():
    """
    Graceful shutdown: notifica Telegram, aguarda fills pendentes,
    e para todos os bots de forma ordenada.
    """
    import asyncio as _asyncio
    log.info("[Shutdown] Iniciando graceful shutdown...")

    # 1. Notifica Telegram
    notifier = TelegramNotifier()
    if notifier.enabled:
        tasks = []
        for inst in manager._instances.values():
            if inst.status in ("running", "maintenance"):
                tasks.append(notifier.send(build_stop_msg(
                    bot_name  = inst.config.name,
                    demo      = inst.config.demo,
                    symbol    = inst.config.symbol,
                    daily_pnl = inst._daily_pnl,
                    wins      = inst._wins,
                    losses    = inst._losses,
                    reason    = "Servidor desligado",
                )))
        if tasks:
            await _asyncio.gather(*tasks, return_exceptions=True)

    # 2. Aguarda confirmação de fills pendentes (3s para WS chegar)
    await _asyncio.sleep(3)

    # 3. Para todos os bots de forma ordenada
    for inst in list(manager._instances.values()):
        inst.stop()

    # 4. Aguarda tasks terminarem (máx 10s)
    pending_tasks = [
        inst._task for inst in manager._instances.values()
        if inst._task and not inst._task.done()
    ]
    if pending_tasks:
        await _asyncio.wait_for(
            _asyncio.gather(*pending_tasks, return_exceptions=True),
            timeout=10.0,
        )

    log.info("[Shutdown] Graceful shutdown completo.")


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class BotCreate(BaseModel):
    name:            str
    strategy_id:     str
    symbol:          str    = "BTC-USDT"
    timeframe:       str    = "15m"
    demo:            bool   = True
    stake_usd:       float  = FIXED_STAKE_USD
    leverage:        int    = 1
    stop_loss_usd:   float  = -50.0
    strategy_params: dict   = {}

class BotUpdate(BaseModel):
    name:            Optional[str]   = None
    symbol:          Optional[str]   = None
    timeframe:       Optional[str]   = None
    stake_usd:       Optional[float] = None
    leverage:        Optional[int]   = None
    stop_loss_usd:   Optional[float] = None
    strategy_params: Optional[dict]  = None

class BotSymbolSwitch(BaseModel):
    symbol: str = "BTC-USDT"


# ── Rotas: Estratégias ────────────────────────────────────────────────────────

@app.get("/api/strategies")
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


@app.get("/api/strategy-factory/status")
def factory_status():
    """Verifica se a KIMI está configurada."""
    from .strategy_factory.kimi_client import is_configured, get_model, get_base_url, get_provider
    return {
        "configured": is_configured(),
        "model":      get_model(),
        "base_url":   get_base_url(),
        "provider":   get_provider(),
    }


@app.post("/api/strategy-factory/plan")
async def factory_plan(req: FactoryPlanRequest):
    """Etapa 1 — Descrição → Plano JSON."""
    from .strategy_factory.planner import generate_plan
    try:
        plan = await generate_plan(req.description)
        # Atribui o próximo ID disponível
        db = SessionLocal()
        try:
            used = [r[0] for r in db.query(FactoryStrategyModel.strategy_id).all()]
        finally:
            db.close()
        from .strategy_factory.deployer import assign_next_id
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


@app.post("/api/strategy-factory/generate")
async def factory_generate(req: FactoryGenerateRequest):
    """Etapa 2 — Plano aprovado → código Python."""
    from .strategy_factory.generator import generate_code
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


@app.post("/api/strategy-factory/fix")
async def factory_fix(req: FactoryFixRequest):
    """Corrige cirurgicamente erros específicos no código existente."""
    from .strategy_factory.generator import fix_code
    from .strategy_factory.validator import validate_code
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


@app.post("/api/strategy-factory/validate")
def factory_validate(req: FactoryValidateRequest):
    """Etapa 3 — Código gerado → ValidationReport."""
    from .strategy_factory.validator import validate_code
    try:
        report = validate_code(req.code, req.plan)
        return report.summary()
    except Exception as e:
        raise HTTPException(500, f"Erro interno na validação: {e}")


@app.post("/api/strategy-factory/deploy")
def factory_deploy(req: FactoryDeployRequest, db: Session = Depends(get_db)):
    """Etapa 4 — Implanta a estratégia: escreve arquivo + hot-load + persiste no banco."""
    from .strategy_factory.deployer import deploy
    from .strategy_factory.validator import validate_code

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
        existing.code_py     = req.code
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
            code_py          = req.code,
            status           = "deployed",
            deployed_at      = datetime.now(timezone.utc).replace(tzinfo=None),
            validation_report = report.summary(),
        ))
    db.commit()

    log.info("Fábrica: estratégia %s implantada (%s).", strategy_id, class_name)
    return {"strategy_id": strategy_id, "class_name": class_name, "status": "deployed"}


@app.get("/api/strategy-factory/strategies")
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


@app.get("/api/strategy-factory/strategies/{strategy_id}/code")
def factory_get_code(strategy_id: str, db: Session = Depends(get_db)):
    """Retorna o código Python de uma estratégia de fábrica."""
    row = db.query(FactoryStrategyModel).filter_by(strategy_id=strategy_id.upper()).first()
    if not row:
        raise HTTPException(404, "Estratégia não encontrada")
    return {"strategy_id": row.strategy_id, "code": row.code_py, "plan": row.plan_json}


@app.delete("/api/strategy-factory/strategies/{strategy_id}/permanent")
def factory_delete_permanent(strategy_id: str, db: Session = Depends(get_db)):
    """Apaga permanentemente uma estratégia de fábrica (arquivo + registry + banco)."""
    from .strategy_factory.deployer import remove, _FACTORY_DIR
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


@app.delete("/api/strategy-factory/strategies/{strategy_id}")
def factory_disable(strategy_id: str, db: Session = Depends(get_db)):
    """Desativa uma estratégia de fábrica (remove do REGISTRY, preserva arquivo)."""
    from .strategy_factory.deployer import remove
    sid = strategy_id.upper()
    row = db.query(FactoryStrategyModel).filter_by(strategy_id=sid).first()
    if not row:
        raise HTTPException(404, "Estratégia não encontrada")
    removed = remove(sid)
    row.status = "disabled"
    db.commit()
    return {"strategy_id": sid, "removed_from_registry": removed}


@app.get("/api/market/regime")
async def get_market_regime():
    """
    Avalia o regime atual do mercado de crypto (BTC-USDT) via OKX.

    Retorna:
      regime      : 'trending' | 'ranging' | 'volatile'
      trend_dir   : 'up' | 'down' | 'flat'
      session     : 'ny' | 'london' | 'asia' | 'off'
      atr_pct     : ATR como % do preço (volatilidade)
      bbw_pct     : Bollinger Band Width %
      ema_slope   : inclinação da EMA20 normalizada
      updated_at  : timestamp UTC
    """
    import datetime as _dt
    import math as _math

    def _session_now():
        h = _dt.datetime.utcnow().hour
        if 13 <= h < 22:  return "ny"       # 09:00–18:00 EST
        if  8 <= h < 13:  return "london"   # 08:00–13:00 UTC
        if  0 <= h <  8:  return "asia"     # Tóquio/Cingapura
        return "off"

    get_exchange_provider()
    from .exchanges.okx import OKXExchange as _OKXEx
    _ex = _OKXEx()
    _candles = await _ex.fetch_candles("BTC-USDT", "1h", limit=30)
    if len(_candles) < 10:
        raise ValueError("dados insuficientes")
    bars = [{"c": c.close, "h": c.high, "l": c.low} for c in _candles]

    try:
        closes = [b["c"] for b in bars]
        highs  = [b["h"] for b in bars]
        lows   = [b["l"] for b in bars]
        n      = len(closes)

        # ATR (14)
        trs = []
        for i in range(1, n):
            trs.append(max(highs[i] - lows[i],
                           abs(highs[i] - closes[i-1]),
                           abs(lows[i]  - closes[i-1])))
        atr     = sum(trs[-14:]) / min(14, len(trs))
        atr_pct = atr / closes[-1] * 100

        # EMA20 slope (normalizado)
        k     = 2 / 21
        ema   = closes[0]
        for c in closes[1:]:
            ema = c * k + ema * (1 - k)
        ema_prev = closes[-5] * k + closes[-6] * (1 - k)   # proxy
        ema_slope = (ema - ema_prev) / ema_prev * 100

        # Bollinger Band Width (20, 2σ)
        win20  = closes[-20:]
        mean20 = sum(win20) / 20
        std20  = _math.sqrt(sum((x - mean20) ** 2 for x in win20) / 20)
        bbw_pct = (4 * std20) / mean20 * 100   # upper-lower / mid * 100

        # Classificação do regime
        if atr_pct > 1.5 and abs(ema_slope) > 0.3:
            regime = "volatile"
        elif abs(ema_slope) >= 0.15 and atr_pct > 0.5:
            regime = "trending"
        else:
            regime = "ranging"

        trend_dir = "up" if ema_slope > 0.05 else "down" if ema_slope < -0.05 else "flat"

    except Exception as exc:
        log.warning("get_market_regime: %s", exc)
        # Fallback neutro
        return {
            "regime":    "unknown",
            "trend_dir": "flat",
            "session":   _session_now(),
            "atr_pct":   0.0,
            "bbw_pct":   0.0,
            "ema_slope": 0.0,
            "updated_at": _dt.datetime.utcnow().isoformat() + "Z",
            "error":     str(exc),
        }

    return {
        "regime":     regime,
        "trend_dir":  trend_dir,
        "session":    _session_now(),
        "atr_pct":    round(atr_pct, 3),
        "bbw_pct":    round(bbw_pct, 3),
        "ema_slope":  round(ema_slope, 4),
        "updated_at": _dt.datetime.utcnow().isoformat() + "Z",
    }


# ── Rotas: Bots ───────────────────────────────────────────────────────────────

@app.get("/api/bots/performance")
def get_performance_ranking(db: Session = Depends(get_db)):
    from sqlalchemy import func
    # 1. Busca robôs atuais
    bots = db.query(BotModel).all()
    bot_map = {b.id: b for b in bots}
    
    # 2. Usa a mesma base do /api/trades/summary: uma linha por round-trip
    # fechado. Linhas type="exit"/"tp1" são eventos visuais e não devem
    # duplicar a contagem do ranking histórico.
    stats_rows = db.query(
        TradeModel.bot_id,
        func.sum(TradeModel.pnl).label("pnl_sum"),
        func.count(TradeModel.id).label("total_count")
    ).filter(
        TradeModel.type == "entry",
        TradeModel.exit_price.isnot(None),
        TradeModel.pnl.is_not(None),
    ).group_by(TradeModel.bot_id).all()
    
    # 3. Busca vitórias separadamente para segurança
    wins_rows = db.query(
        TradeModel.bot_id,
        func.count(TradeModel.id).label("win_count")
    ).filter(
        TradeModel.type == "entry",
        TradeModel.exit_price.isnot(None),
        TradeModel.pnl > 0,
    ).group_by(TradeModel.bot_id).all()
    wins_map = {row[0]: row[1] for row in wins_rows}
    
    ranking = []
    seen_ids = set()
    
    for bid, pnl_sum, total_count in stats_rows:
        bot = bot_map.get(bid)
        name = bot.name if bot else f"Bot Antigo (ID {bid})"
        
        pnl_val  = float(pnl_sum or 0)
        total    = int(total_count or 0)
        wins     = int(wins_map.get(bid, 0))
        win_rate = (wins / total * 100) if total > 0 else 0

        ranking.append({
            "id": bid,
            "name": name,
            "strategy_id": bot.strategy_id if bot else "N/A",
            "symbol": bot.symbol if bot else "N/A",
            "pnl": round(pnl_val, 2),
            "trades": total,
            "win_rate": round(win_rate, 1),
            "is_active": bot is not None
        })
        seen_ids.add(bid)
    
    # 4. Filtra apenas quem executou pelo menos um trade
    final_ranking = [r for r in ranking if r["trades"] > 0]
    
    return sorted(final_ranking, key=lambda x: x["pnl"], reverse=True)


@app.get("/api/bots/performance/active")
async def get_active_performance(db: Session = Depends(get_db)):
    """Retorna o ranking de bots com operações abertas (PnL não realizado).

    Usa unrealized_pl e unrealized_plpc da OKX quando disponíveis;
    fallback para cálculo local se a posição não for encontrada na exchange.
    """
    from .exchanges.base import Position as _Position
    import aiohttp as _aio_rank

    bots = db.query(BotModel).all()
    active_bots = [b for b in bots
                   if manager.get_status(b.id) and manager.get_status(b.id).get("direction", 0) != 0]

    # Busca posições da exchange em paralelo para todos os bots activos
    async with _aio_rank.ClientSession() as _sess_rank:
        exchange = build_exchange(_sess_rank)
    pos_results = await asyncio.gather(
        *[exchange.get_position(b.symbol) for b in active_bots],
        return_exceptions=True,
    )
    exchange_pos: dict[int, _Position] = {}
    for b, result in zip(active_bots, pos_results):
        if isinstance(result, _Position):
            exchange_pos[b.id] = result

    active_ranking = []

    for b in active_bots:
        status = manager.get_status(b.id)
        if not status:
            continue

        direction = status.get("direction", 0)
        if direction == 0:
            continue

        entry_price = status.get("entry_price", 0)
        last_price  = status.get("last_price", 0)
        sl_price    = status.get("sl_price", 0)

        pos = exchange_pos.get(b.id)

        if pos and pos.unrealized_plpc != 0.0:
            # Fonte de verdade: exchange
            pnl_pct = pos.unrealized_plpc * 100
            pnl_usd = pos.unrealized_pnl
        elif entry_price > 0 and last_price > 0:
            # Fallback local
            pnl_pct = ((last_price - entry_price) / entry_price) * 100 * direction
            base_qty = float(status.get("size", 0) or 0)
            if base_qty > 0:
                pnl_usd = base_qty * (last_price - entry_price) * direction
            else:
                pnl_usd = (pnl_pct / 100) * FIXED_STAKE_USD
        else:
            continue

        # PnL % Garantido (prioriza o cálculo dinâmico do robô/sombra)
        guaranteed_pnl_pct = status.get("guaranteed_pnl", 0.0)

        # Fallback se o robô não reportar (ex: robô ainda no SL fixo)
        if guaranteed_pnl_pct == 0 and sl_price > 0 and entry_price > 0:
            diff = (sl_price - entry_price) / entry_price * direction
            if diff > 0:
                guaranteed_pnl_pct = diff * 100

        guaranteed_pnl_usd = (guaranteed_pnl_pct / 100) * FIXED_STAKE_USD

        p = b.strategy_params or {}
        sts_trigger = (
            p.get("sts_activation_pct") or
            p.get("sts_trigger_pct") or
            p.get("ts_activation_pct") or
            p.get("trailing_stop_activation") or
            p.get("ts_activation") or
            0.0
        )
        sts_step = (
            p.get("sts_step_pct") or
            p.get("ts_step_pct") or
            p.get("ts_step") or
            0.0
        )
        if sts_trigger == 0:
            sts_trigger = 1.0

        active_ranking.append({
            "id": b.id,
            "name": b.name,
            "strategy_id": b.strategy_id,
            "symbol": b.symbol,
            "direction": "LONG" if direction == 1 else "SHORT",
            "entry_price": round(entry_price, 4),
            "last_price": round(last_price, 4),
            "sl_price": round(sl_price, 4),
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usd": round(pnl_usd, 2),
            "guaranteed_pnl_usd": round(guaranteed_pnl_usd, 2),
            "guaranteed_pnl_pct": round(guaranteed_pnl_pct, 2),
            "sts_trigger": sts_trigger,
            "sts_step": sts_step,
            "exchange_pnl": pos.unrealized_pnl if pos else None,
            "exchange_pnl_pct": round(pos.unrealized_plpc * 100, 2) if pos else None,
            "cost_basis": round(pos.cost_basis, 4) if pos else None,
            "change_today": round(pos.change_today * 100, 2) if pos else None,
        })
            
    # Ordena pelo maior PnL aberto
    return sorted(active_ranking, key=lambda x: x["pnl_usd"], reverse=True)


@app.get("/api/bots")
def list_bots(db: Session = Depends(get_db)):
    bots = db.query(BotModel).all()
    result = []
    for b in bots:
        status = manager.get_status(b.id)
        result.append({
            "id": b.id, "name": b.name,
            "strategy_id": b.strategy_id, "symbol": b.symbol,
            "timeframe": b.timeframe, "demo": b.demo,
            "stake_usd": FIXED_STAKE_USD, "leverage": b.leverage,
            "active": b.active, "created_at": str(b.created_at),
            "baseline_balance": b.baseline_balance or 0.0,
            "runtime": status,
        })
    return result


@app.post("/api/bots", status_code=201)
async def create_bot(payload: BotCreate, db: Session = Depends(get_db)):
    # Valida estratégia
    try:
        get_strategy(payload.strategy_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    _assert_spot_only(payload.symbol)
    payload.symbol = _norm_symbol(payload.symbol)
    payload.leverage = 1
    payload.stake_usd = FIXED_STAKE_USD
    _assert_symbol_available(db, payload.symbol)

    bot = BotModel(**payload.model_dump())
    db.add(bot)
    db.commit()
    db.refresh(bot)

    # Snapshot do saldo spot pré-existente — isola holdings do usuário das
    # divergências. Qualquer quantidade acima deste baseline é do bot.
    try:
        async with aiohttp.ClientSession() as _s:
            _ex = build_exchange(_s, demo=bot.demo)
            _pos = await _ex.get_position(bot.symbol)
            if _pos and _pos.size > 1e-9:
                bot.baseline_balance = float(_pos.size)
                db.commit()
                log.info(
                    "Bot %d: baseline=%.8f %s (holdings pré-existentes ignorados na detecção de divergências)",
                    bot.id, bot.baseline_balance, bot.symbol,
                )
    except Exception as _exc:
        log.warning("Bot %d: baseline snapshot falhou (%s) — assumindo 0.0", bot.id, _exc)

    log.info("Bot criado: %d — %s", bot.id, bot.name)
    return {"id": bot.id, "name": bot.name, "strategy_id": bot.strategy_id}


@app.get("/api/bots/{bot_id}")
def get_bot(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
    return {**bot.__dict__, "runtime": manager.get_status(bot_id)}


@app.patch("/api/bots/{bot_id}")
def update_bot(bot_id: int, payload: BotUpdate, db: Session = Depends(get_db)):
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
    if bot.active:
        raise HTTPException(400, "Pare o bot antes de editar.")
    if payload.symbol:
        _assert_spot_only(payload.symbol)
        payload.symbol = _norm_symbol(payload.symbol)
    if payload.symbol and payload.symbol != _norm_symbol(bot.symbol):
        _assert_symbol_available(db, payload.symbol, exclude_bot_id=bot_id)
    update_data = payload.model_dump(exclude_none=True)
    update_data["leverage"] = 1
    update_data["stake_usd"] = FIXED_STAKE_USD
    for k, v in update_data.items():
        setattr(bot, k, v)
    db.commit()
    return {"ok": True}


@app.post("/api/bots/{bot_id}/recapture-baseline")
async def recapture_baseline(bot_id: int, db: Session = Depends(get_db)):
    """Recaptura o saldo spot atual como baseline — limpa divergências de holdings pré-existentes."""
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
    try:
        async with aiohttp.ClientSession() as _s:
            _ex = build_exchange(_s, demo=bot.demo)
            _pos = await _ex.get_position(bot.symbol)
            new_baseline = float(_pos.size) if (_pos and _pos.size > 1e-9) else 0.0
            bot.baseline_balance = new_baseline
            db.commit()
            # Atualiza instância em memória para efeito imediato (sem reiniciar o bot)
            if bot_id in manager._instances:
                manager._instances[bot_id].config.baseline_balance = new_baseline
                manager._instances[bot_id]._last_order_error = None
                manager._instances[bot_id]._hold_reason = None
        log.info("Bot %d: baseline recapturado → %.8f %s", bot_id, new_baseline, bot.symbol)
        return {"ok": True, "baseline_balance": new_baseline, "symbol": bot.symbol}
    except Exception as exc:
        raise HTTPException(500, f"Falha ao recapturar baseline: {exc}")


@app.delete("/api/bots/{bot_id}", status_code=204)
def delete_bot(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
    manager.stop_bot(bot_id)
    # Remove registros filhos primeiro (FK constraint no PostgreSQL)
    db.query(AiAnalysisLogModel).filter(AiAnalysisLogModel.bot_id == bot_id).delete()
    db.query(SignalLogModel).filter(SignalLogModel.bot_id == bot_id).delete()
    db.query(BotSnapshotModel).filter(BotSnapshotModel.bot_id == bot_id).delete()
    db.query(TradeModel).filter(TradeModel.bot_id == bot_id).delete()
    db.delete(bot)
    db.commit()


@app.post("/api/bots/{bot_id}/start")
async def start_bot(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
    _assert_symbol_available(db, bot.symbol, exclude_bot_id=bot_id, active_only=True)
    bot.symbol = _norm_symbol(bot.symbol)
    bot.leverage = 1
    bot.active = True
    db.commit()
    try:
        manager.start_bot(bot)   # asyncio.create_task() exige contexto async
    except RuntimeError as exc:
        bot.active = False
        db.commit()
        raise HTTPException(400, str(exc)) from exc
    return {"status": "running", "bot_id": bot_id}


@app.post("/api/bots/{bot_id}/stop")
async def stop_bot(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
    bot.active = False
    db.commit()
    manager.stop_bot(bot_id)
    return {"status": "stopped", "bot_id": bot_id}


@app.post("/api/bots/{bot_id}/liquidate")
async def liquidate_bot_api(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
    await manager.liquidate_bot(bot_id)
    return {"status": "liquidated", "bot_id": bot_id}


@app.post("/api/bots/{bot_id}/switch-symbol")
async def switch_bot_symbol(
    bot_id: int,
    payload: BotSymbolSwitch,
    db: Session = Depends(get_db),
):
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")

    _assert_spot_only(payload.symbol)
    payload.symbol = _norm_symbol(payload.symbol)
    _assert_symbol_available(db, payload.symbol, exclude_bot_id=bot_id)

    was_active = bool(bot.active)
    if was_active:
        manager.stop_bot(bot_id)

    bot.symbol = payload.symbol
    db.commit()
    db.refresh(bot)

    if was_active:
        try:
            manager.start_bot(bot)
        except RuntimeError as exc:
            bot.active = False
            db.commit()
            raise HTTPException(400, str(exc)) from exc

    return {
        "ok": True,
        "bot_id": bot_id,
        "symbol": bot.symbol,
        "status": "running" if was_active else "stopped",
    }


class ResetRequest(BaseModel):
    api_key:    str
    api_secret: str
    passphrase: Optional[str] = None   # obrigatório para OKX


class AccountConnectRequest(ResetRequest):
    confirm_clear: bool = False


class OrderRejectionPatch(BaseModel):
    resolved: Optional[bool] = None
    status: Optional[str] = None
    resolution_notes: Optional[str] = None


@app.post("/api/account/reset-data")
async def reset_data_only(db: Session = Depends(get_db)):
    """
    Reset de dados apenas — limpa bots, trades, sinais e snapshots
    SEM alterar credenciais da exchange.
    """
    import datetime as _dt

    # 1. Para todos os bots e tenta liquidar posições
    try:
        await manager.liquidate_all(db)
    except Exception as le:
        log.warning("Reset dados: liquidação parcial: %s", le)

    # 2. Apaga TUDO do banco (exceto settings que contém credenciais)
    db.query(AiAnalysisLogModel).delete()
    db.query(TradeReportModel).delete()
    db.query(OrderRejectionModel).delete()
    db.query(SignalLogModel).delete()
    db.query(BotSnapshotModel).delete()
    db.query(TradeModel).delete()
    db.query(BotModel).delete()
    db.commit()

    log.info("Reset de dados executado: bots, trades, sinais e snapshots apagados.")

    return {
        "ok": True,
        "message": "Reset de dados concluído. Bots, trades, sinais e snapshots foram apagados. Credenciais preservadas.",
        "cleared": ["bots", "trades", "signal_logs", "snapshots", "ai_logs", "reports", "order_rejections"],
    }


@app.post("/api/account/reset")
async def reset_account(body: ResetRequest, db: Session = Depends(get_db)):
    """
    Reset Geral com troca de credenciais da exchange ativa (OKX):
      1. Valida as novas credenciais contra a API da exchange
      2. Para todos os bots e liquida posições (conta antiga)
      3. Apaga TUDO: bots, trades, signals, snapshots, AI logs, reports
      4. Grava as novas credenciais criptografadas no banco (tabela settings)
      5. Sincroniza e retorna snapshot da nova conta
    """
    import aiohttp as _aio
    import datetime as _dt
    from .crypto_utils import encrypt
    from .database import SettingsModel

    provider   = get_exchange_provider()
    api_key    = body.api_key.strip()
    api_secret = body.api_secret.strip()

    if not api_key or not api_secret:
        raise HTTPException(400, "API Key e Secret são obrigatórios.")

    # ── 1. Valida as novas credenciais ────────────────────────────────────────
    if provider == "okx":
        passphrase = (body.passphrase or "").strip()
        if not passphrase:
            raise HTTPException(400, "Passphrase é obrigatória para a OKX.")
        import base64 as _b64, hashlib as _sha, hmac as _hmac
        now_okx  = _dt.datetime.now(_dt.timezone.utc)
        ts_okx   = now_okx.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now_okx.microsecond // 1000:03d}Z"
        msg_okx  = ts_okx + "GET" + "/api/v5/account/balance"
        sig_okx  = _b64.b64encode(
            _hmac.new(api_secret.encode(), msg_okx.encode(), _sha.sha256).digest()
        ).decode()
        okx_demo = os.getenv("OKX_DEMO", "true").lower() in ("1", "true", "yes")
        val_headers = {
            "OK-ACCESS-KEY":        api_key,
            "OK-ACCESS-SIGN":       sig_okx,
            "OK-ACCESS-TIMESTAMP":  ts_okx,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "Content-Type":         "application/json",
        }
        if okx_demo:
            val_headers["x-simulated-trading"] = "1"
        try:
            async with _aio.ClientSession() as sess:
                async with sess.get("https://www.okx.com/api/v5/account/balance", headers=val_headers) as resp:
                    acct_raw = await resp.json()
            if str(acct_raw.get("code", "0")) != "0":
                raise HTTPException(401, f"Credenciais OKX inválidas: {acct_raw.get('msg', '?')}")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(502, f"Não foi possível conectar à OKX: {exc}")
        items     = acct_raw.get("data", [])
        total_eq  = float(items[0].get("totalEq", 0) if items else 0)
        credential_pairs = [
            ("okx_api_key",    api_key),
            ("okx_api_secret", api_secret),
            ("okx_passphrase", passphrase),
        ]
        account_snapshot = {"equity": total_eq, "cash": total_eq, "currency": "USDT"}
    else:
        # Provider desconhecido — armazena credenciais sem validação externa
        credential_pairs = [
            (f"{provider}_api_key",    api_key),
            (f"{provider}_api_secret", api_secret),
        ]
        account_snapshot = {"equity": 0.0, "cash": 0.0, "currency": "USD"}

    # ── 2. Para todos os bots e liquida posições (conta antiga) ───────────────
    try:
        await manager.liquidate_all(db)
    except Exception as le:
        log.warning("Reset: liquidação parcial: %s", le)

    # ── 3. Apaga TUDO do banco (exceto settings) ──────────────────────────────
    db.query(AiAnalysisLogModel).delete()
    db.query(TradeReportModel).delete()
    db.query(OrderRejectionModel).delete()
    db.query(SignalLogModel).delete()
    db.query(BotSnapshotModel).delete()
    db.query(TradeModel).delete()
    db.query(BotModel).delete()
    db.commit()

    # ── 4. Grava novas credenciais criptografadas ─────────────────────────────
    for setting_key, plain_value in credential_pairs:
        row = db.query(SettingsModel).filter_by(key=setting_key).first()
        if row:
            row.value      = encrypt(plain_value)
            row.updated_at = _dt.datetime.utcnow()
        else:
            db.add(SettingsModel(
                key=setting_key,
                value=encrypt(plain_value),
                updated_at=_dt.datetime.utcnow(),
            ))
    db.commit()
    log.info("Reset Geral: credenciais %s atualizadas no banco.", provider.upper())

    # ── 5. Snapshot da nova conta ─────────────────────────────────────────────
    return {
        "ok":      True,
        "message": f"Reset concluído. Credenciais {provider.upper()} validadas e armazenadas.",
        "account": {
            **account_snapshot,
            "synced_at": _dt.datetime.utcnow().isoformat() + "Z",
        },
    }


@app.get("/api/account/connection")
async def account_connection(db: Session = Depends(get_db)):
    """Status oficial da conexão OKX configurada via banco."""
    return await okx_status(db)


@app.post("/api/account/connect")
async def account_connect(body: AccountConnectRequest, db: Session = Depends(get_db)):
    """Conecta uma conta OKX e limpa o banco operacional local após confirmação."""
    if not body.confirm_clear:
        raise HTTPException(
            400,
            "Confirme que bots, trades, sinais, snapshots, relatórios e rejeições locais serão apagados."
        )
    return await reset_account(
        ResetRequest(
            api_key=body.api_key,
            api_secret=body.api_secret,
            passphrase=body.passphrase,
        ),
        db,
    )


@app.post("/api/account/disconnect")
async def account_disconnect(db: Session = Depends(get_db)):
    """Remove as credenciais OKX do banco e para todos os bots ativos."""
    import datetime as _dt

    for bot_id in list(manager._instances.keys()):
        manager.stop_bot(bot_id)
    db.query(BotModel).update({BotModel.active: False})

    removed = db.query(SettingsModel).filter(
        SettingsModel.key.in_(["okx_api_key", "okx_api_secret", "okx_passphrase"])
    ).delete(synchronize_session=False)
    db.commit()

    log.info("OKX desconectada via aplicação; %d credenciais removidas.", removed)
    return {
        "ok": True,
        "connected": False,
        "disconnected_at": _dt.datetime.utcnow().isoformat() + "Z",
        "removed": removed,
    }


@app.get("/api/settings/okx-status")
async def okx_status(db: Session = Depends(get_db)):
    """Retorna se as credenciais OKX estão configuradas e o saldo da conta."""
    from .database import SettingsModel
    from .crypto_utils import safe_decrypt

    key_row = db.query(SettingsModel).filter_by(key="okx_api_key").first()
    configured = bool(key_row and safe_decrypt(key_row.value or ""))
    demo = os.getenv("OKX_DEMO", "true").lower() in ("1", "true", "yes")

    result: dict = {"configured": configured, "demo": demo, "equity": None, "currency": "USDT"}
    if not configured:
        return result

    try:
        from .exchanges.okx import OKXExchange
        ex = OKXExchange()
        summary = await ex.get_account_summary()
        result["equity"] = round(float(summary.get("equity") or 0), 2)
    except Exception:
        pass
    return result


@app.get("/api/order-rejections")
def list_order_rejections(
    bot_id: Optional[int] = None,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    resolved: Optional[bool] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    q = db.query(OrderRejectionModel)
    if bot_id is not None:
        q = q.filter(OrderRejectionModel.bot_id == bot_id)
    if symbol:
        q = q.filter(func.upper(OrderRejectionModel.symbol) == _norm_symbol(symbol))
    if status:
        q = q.filter(OrderRejectionModel.status == status)
    if resolved is not None:
        q = q.filter(OrderRejectionModel.resolved == resolved)

    rows = q.order_by(OrderRejectionModel.created_at.desc()).limit(max(1, min(limit, 500))).all()
    return [
        {
            "id": r.id,
            "bot_id": r.bot_id,
            "bot_name": r.bot_name,
            "symbol": r.symbol,
            "side": r.side,
            "order_type": r.order_type,
            "ord_id": r.ord_id,
            "algo_id": r.algo_id,
            "status": r.status,
            "reason": r.reason,
            "raw_payload": r.raw_payload,
            "resolved": r.resolved,
            "resolution_notes": r.resolution_notes,
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
            "updated_at": r.updated_at.isoformat() + "Z" if r.updated_at else None,
        }
        for r in rows
    ]


@app.patch("/api/order-rejections/{rejection_id}")
def update_order_rejection(
    rejection_id: int,
    payload: OrderRejectionPatch,
    db: Session = Depends(get_db),
):
    import datetime as _dt

    row = db.get(OrderRejectionModel, rejection_id)
    if not row:
        raise HTTPException(404, "Rejeição não encontrada")
    patch = payload.model_dump(exclude_none=True)
    for key, value in patch.items():
        setattr(row, key, value)
    if "resolved" in patch and row.resolved and "status" not in patch:
        row.status = "resolved"
    row.updated_at = _dt.datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"ok": True, "id": row.id, "resolved": row.resolved, "status": row.status}


class OkxCredentialsRequest(BaseModel):
    api_key:    str
    api_secret: str
    passphrase: str


@app.post("/api/settings/okx-credentials")
async def set_okx_credentials(body: OkxCredentialsRequest, db: Session = Depends(get_db)):
    """
    Valida e armazena as credenciais OKX criptografadas no banco.
    Substitui as credenciais anteriores sem afetar bots nem trades.
    """
    import datetime as _dt
    import base64 as _b64, hashlib as _sha, hmac as _hmac
    import aiohttp as _aio
    from .crypto_utils import encrypt
    from .database import SettingsModel

    api_key    = body.api_key.strip()
    api_secret = body.api_secret.strip()
    passphrase = body.passphrase.strip()

    if not api_key or not api_secret or not passphrase:
        raise HTTPException(400, "API Key, Secret e Passphrase são obrigatórios.")

    # ── Valida credenciais contra a OKX ──────────────────────────────────────
    now_dt  = _dt.datetime.now(_dt.timezone.utc)
    ts      = now_dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now_dt.microsecond // 1000:03d}Z"
    msg     = ts + "GET" + "/api/v5/account/balance"
    sig     = _b64.b64encode(_hmac.new(api_secret.encode(), msg.encode(), _sha.sha256).digest()).decode()
    demo    = os.getenv("OKX_DEMO", "true").lower() in ("1", "true", "yes")

    val_headers = {
        "OK-ACCESS-KEY":        api_key,
        "OK-ACCESS-SIGN":       sig,
        "OK-ACCESS-TIMESTAMP":  ts,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type":         "application/json",
    }
    if demo:
        val_headers["x-simulated-trading"] = "1"

    try:
        async with _aio.ClientSession() as sess:
            async with sess.get("https://www.okx.com/api/v5/account/balance", headers=val_headers) as resp:
                data = await resp.json()
        if str(data.get("code", "0")) != "0":
            raise HTTPException(401, f"Credenciais OKX inválidas: {data.get('msg', 'Erro desconhecido')}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Não foi possível conectar à OKX: {exc}")

    items    = data.get("data", [])
    total_eq = float(items[0].get("totalEq", 0) if items else 0)

    # ── Armazena criptografado (upsert) ───────────────────────────────────────
    now_utc = _dt.datetime.utcnow()
    for setting_key, plain_value in [
        ("okx_api_key",    api_key),
        ("okx_api_secret", api_secret),
        ("okx_passphrase", passphrase),
    ]:
        row = db.query(SettingsModel).filter_by(key=setting_key).first()
        if row:
            row.value      = encrypt(plain_value)
            row.updated_at = now_utc
        else:
            db.add(SettingsModel(key=setting_key, value=encrypt(plain_value), updated_at=now_utc))
    db.commit()
    log.info("Credenciais OKX atualizadas no banco (criptografadas).")

    return {
        "ok":       True,
        "message":  "Credenciais OKX validadas e armazenadas com sucesso.",
        "demo":     demo,
        "equity":   round(total_eq, 2),
        "currency": "USDT",
    }


@app.get("/api/activities")
async def get_activities(date: Optional[str] = None, db: Session = Depends(get_db)):
    """
    Retorna os fills da exchange ativa para a data informada (padrão: hoje),
    correlacionados com os bots do sistema e trades internos.
    """
    import aiohttp as _aio
    from datetime import date as _date_cls
    import datetime as _dt

    # Usa data local (não UTC) como padrão para target_date
    if date:
        target_date = date
    else:
        _now_utc = _dt.datetime.utcnow()
        _now_local = _now_utc + _dt.timedelta(hours=_TZ_OFFSET)
        target_date = _now_local.strftime("%Y-%m-%d")

    async with _aio.ClientSession() as _act_sess:
        exchange = build_exchange(_act_sess)

    # 1. Descobre se há posições abertas em dias anteriores (para FIFO cross-day)
    bots_all = db.query(BotModel).all()
    open_entry = db.query(TradeModel).filter(
        TradeModel.exit_price.is_(None),
        TradeModel.type == "entry",
    ).order_by(TradeModel.timestamp.asc()).first()

    earliest_date: str | None = None
    if open_entry and open_entry.timestamp:
        d = open_entry.timestamp.date()
        if d.isoformat() < target_date:
            earliest_date = d.isoformat()

    # UTC boundaries para target_date (considerando fuso local)
    _utc_start, _utc_end = _local_date_to_utc_range(target_date)

    # 2. Dados da exchange em paralelo — fills com range se cross-day necessário
    def _fills_fetch():
        if earliest_date:
            # Pega desde o início do dia mais antigo até o fim do dia local atual
            _, _cross_end = _local_date_to_utc_range(target_date)
            return exchange.get_activities(after=earliest_date + "T00:00:00Z", until=_cross_end)
        return exchange.get_activities(after=_utc_start, until=_utc_end)

    def _cfee_fetch():
        return exchange.get_activities(after=_utc_start, until=_utc_end, activity_type="CFEE")

    try:
        all_fills, all_cfees, account, port_history, open_orders = await asyncio.gather(
            _fills_fetch(),
            _cfee_fetch(),
            exchange.get_account_summary(),
            exchange.get_portfolio_history("1D", "1H"),
            exchange.get_open_orders(),
        )
    except Exception as exc:
        raise HTTPException(502, f"Erro ao contactar exchange: {exc}")

    # 3. Mapa de bots por símbolo normalizado
    def _norm(sym: str) -> str:
        return sym.upper().replace("/", "").replace("-", "")

    bot_by_symbol: dict[str, list] = {}
    for b in bots_all:
        key = _norm(b.symbol)
        bot_by_symbol.setdefault(key, []).append({"id": b.id, "name": b.name, "strategy_id": b.strategy_id})

    # 3b. Constrói pool de taxas CFEE para matching posterior
    _cfee_pool: list[dict] = []
    for c in (all_cfees or []):
        try:
            fee_usd = abs(float(c.get("net_amount") or 0))
        except (TypeError, ValueError):
            fee_usd = 0.0
        if fee_usd < 0.0001:
            continue
        sym_c = _norm(c.get("symbol", ""))
        ts_str = c.get("transaction_time") or (c.get("date", "") + "T00:00:00Z")
        try:
            ts_c = _dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            ts_c = None
        _cfee_pool.append({"sym": sym_c, "ts": ts_c, "fee_usd": fee_usd, "used": False})

    def _match_fee(sym_norm: str, fill_ts) -> float:
        best, best_delta = None, float("inf")
        for c in _cfee_pool:
            if c["used"]:
                continue
            if c["sym"] and c["sym"] != sym_norm:
                continue
            if c["ts"] and fill_ts:
                delta = abs((c["ts"] - fill_ts).total_seconds())
                if delta < best_delta and delta <= 7200:
                    best, best_delta = c, delta
        if best:
            best["used"] = True
            return round(best["fee_usd"], 4)
        return 0.0

    # 4. Trades internos do dia para reconciliação
    day_start = _dt.datetime.fromisoformat(_utc_start.replace("Z", ""))
    day_end   = _dt.datetime.fromisoformat(_utc_end.replace("Z", ""))
    db_trades = db.query(TradeModel).filter(
        TradeModel.timestamp >= day_start,
        TradeModel.timestamp <  day_end,
    ).all()

    db_trade_list = [
        {
            "id": t.id, "bot_id": t.bot_id, "type": t.type, "direction": t.direction,
            "symbol": t.symbol, "size": t.size,
            "entry_price": t.entry_price, "exit_price": t.exit_price,
            "pnl": t.pnl,
            "timestamp": t.timestamp.isoformat() + "Z" if t.timestamp else None,
            "closed_at": t.closed_at.isoformat() + "Z" if t.closed_at else None,
            "event": t.event,
        }
        for t in db_trades
    ]

    # 5. FIFO P&L sobre TODOS os fills (inclui cross-day se earliest_date foi usado).
    #    Exibição: apenas fills de target_date; P&L totalizado só para fills de hoje.
    from collections import defaultdict, deque
    buy_queues: dict[str, deque] = defaultdict(deque)
    enriched_fills = []
    total_realized_from_fills = 0.0

    for f in all_fills:
        sym_raw   = f.get("symbol", "")
        sym_norm  = _norm(sym_raw)
        side      = f.get("side", "")
        try:
            price = float(f.get("price", 0))
            qty   = float(f.get("qty", 0))
        except (TypeError, ValueError):
            price = qty = 0.0

        matched_bots = bot_by_symbol.get(sym_norm, [])

        fill_pnl = None
        if side == "buy":
            buy_queues[sym_norm].append({"price": price, "qty": qty})
        elif side == "sell" and buy_queues[sym_norm]:
            entry = buy_queues[sym_norm].popleft()
            matched_qty = min(entry["qty"], qty)
            fill_pnl = round((price - entry["price"]) * matched_qty, 4)

        # Determina data LOCAL do fill para filtrar exibição e totalização do dia
        try:
            fill_ts = _dt.datetime.fromisoformat(
                f.get("transaction_time", "").replace("Z", "+00:00")
            ).replace(tzinfo=None)
            fill_local_date = (fill_ts + _dt.timedelta(hours=_TZ_OFFSET)).strftime("%Y-%m-%d")
        except Exception:
            fill_ts = None
            fill_local_date = f.get("transaction_time", "")[:10]

        is_today = fill_local_date == target_date
        if is_today and fill_pnl is not None:
            total_realized_from_fills += fill_pnl

        # Só inclui fills de target_date na resposta (cross-day fills servem apenas para FIFO)
        if not is_today:
            continue

        matched_db_trade = None
        if fill_ts:
            for t in db_trades:
                if t.symbol and _norm(t.symbol) == sym_norm:
                    ref_ts = t.closed_at or t.timestamp
                    if ref_ts and abs((ref_ts - fill_ts).total_seconds()) <= 300:
                        matched_db_trade = {"id": t.id, "type": t.type, "pnl": t.pnl}
                        break

        # Taxa CFEE (apenas para SELLs — onde a fee fecha o round-trip)
        fee_usd = _match_fee(sym_norm, fill_ts) if side == "sell" else 0.0
        net_pnl = round(fill_pnl - fee_usd, 4) if fill_pnl is not None else None

        enriched_fills.append({
            **f,
            "symbol_norm": sym_norm,
            "price":       price,
            "qty":         qty,
            "fill_pnl":    fill_pnl,
            "fee_usd":     fee_usd,
            "net_pnl":     net_pnl,
            "matched_bots": matched_bots,
            "matched_db_trade": matched_db_trade,
        })

    # 6. Resumo da conta
    equity      = float(account.get("equity", 0))
    last_equity = float(account.get("last_equity", equity))
    cash        = float(account.get("cash", 0))
    unreal_pl   = float(account.get("unrealized_pl") or 0)
    day_pl      = equity - last_equity

    # P&L app (apenas exits do dia)
    app_pnl_today = sum(t.pnl for t in db_trades if t.type == "exit" and t.pnl is not None)

    total_fees = round(sum(f.get("fee_usd", 0.0) for f in enriched_fills), 4)
    net_pnl_after_fees = round(total_realized_from_fills - total_fees, 4)

    # Portfólio history — último ponto do dia
    ph_timestamps = port_history.get("timestamp", [])
    ph_equity     = port_history.get("equity",    [])
    ph_pl         = port_history.get("profit_loss", [])
    history_points = [
        {"ts": ts, "equity": eq, "pl": pl}
        for ts, eq, pl in zip(ph_timestamps, ph_equity, ph_pl)
        if eq is not None
    ]

    return {
        "date": target_date,
        "account": {
            "equity":       round(equity, 2),
            "cash":         round(cash, 2),
            "last_equity":  round(last_equity, 2),
            "day_pl":       round(day_pl, 2),
            "unrealized_pl": round(unreal_pl, 2),
        },
        "reconciliation": {
            "exchange_realized_pl": round(total_realized_from_fills, 2),
            "total_fees":          round(total_fees, 4),
            "net_pnl_after_fees":  round(net_pnl_after_fees, 2),
            "app_pnl_today":       round(app_pnl_today, 2),
            "discrepancy":         round(total_realized_from_fills - app_pnl_today, 2),
            "fills_count":         len(enriched_fills),
            "db_trades_count":     len(db_trades),
            "cross_day_fifo":      earliest_date is not None,
            "fifo_since":          earliest_date,
        },
        "fills":       enriched_fills,
        "db_trades":   db_trade_list,
        "history":     history_points,
        "open_orders": open_orders,
    }


@app.get("/api/market/clock")
async def get_market_clock():
    """Retorna o estado actual do mercado: is_open, next_open, next_close."""
    import aiohttp as _aio_clk
    async with _aio_clk.ClientSession() as _sess_clk:
        exchange = build_exchange(_sess_clk)
    return await exchange.get_clock()


@app.post("/api/account/cancel-algos")
async def cancel_pending_algos():
    """Cancela APENAS as ordens algo pendentes (SL/TS) sem fechar posições."""
    import aiohttp
    async with aiohttp.ClientSession() as session:
        ex = build_exchange(session)
        cancelled = await ex.cancel_all_algos()
    return {"ok": True, "cancelled": cancelled}


@app.post("/api/bots/{bot_id}/backtest")
async def run_bot_backtest(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
    
    # 1. Busca dados históricos (limitado a 500 para performance inicial)
    import aiohttp
    import pandas as pd

    async with aiohttp.ClientSession() as session:
        ex = build_exchange(session, demo=bot.demo)
        bar = map_timeframe_for_history(bot.timeframe)
        
        candles = await ex.fetch_candles(bot.symbol, bar, limit=500)
        if not candles:
            raise HTTPException(400, "Não foi possível obter dados históricos")

    # 2. Executa Motor
    engine = BacktestEngine(bot.strategy_id, bot.strategy_params)
    results = await engine.run(candles, stake_usd=FIXED_STAKE_USD)
    
    return results
    
class AutoScanHistoryPayload(BaseModel):
    results: list

@app.post("/api/backtest/auto-scan-history", status_code=201)
def save_auto_scan_history(payload: AutoScanHistoryPayload, db: Session = Depends(get_db)):
    record = AutoScanHistoryModel(results=payload.results)
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"ok": True, "id": record.id}

@app.get("/api/backtest/auto-scan-history")
def get_auto_scan_history(db: Session = Depends(get_db)):
    records = db.query(AutoScanHistoryModel).order_by(AutoScanHistoryModel.created_at.desc()).all()
    return [{"id": r.id, "created_at": r.created_at.isoformat() + "Z", "results": r.results} for r in records]

@app.delete("/api/backtest/auto-scan-history/{history_id}", status_code=204)
def delete_auto_scan_history(history_id: int, db: Session = Depends(get_db)):
    record = db.get(AutoScanHistoryModel, history_id)
    if record:
        db.delete(record)
        db.commit()

class CategoryBacktestRequest(BaseModel):
    category: str
    symbol: str
    exclude_strategies: list[str] = []


_VERDICT_ORDER = {"INICIAR": 0, "CUIDADO": 1, "NÃO INICIAR": 2, "N/A": 3}
_CONTEXT_FLAGS = ("needs_gex_context", "needs_graph_context")


@app.post("/api/backtest/category")
async def run_category_backtest(req: CategoryBacktestRequest):
    """Executa backtest de todas as estratégias de uma categoria para um símbolo."""
    import aiohttp as _aiohttp
    from .strategies.registry import REGISTRY

    prefix = req.category.upper()
    strategy_ids = [sid for sid in REGISTRY if sid.upper().startswith(prefix) and sid not in req.exclude_strategies]
    if not strategy_ids:
        raise HTTPException(404, f"Nenhuma estratégia encontrada para categoria '{prefix}'")

    # Separa estratégias que precisam de contexto externo (não backtestáveis)
    na_ids: list[str] = []
    tf_groups: dict[str, list[str]] = {}
    for sid in sorted(strategy_ids):
        cls = REGISTRY[sid]
        if any(getattr(cls, flag, False) for flag in _CONTEXT_FLAGS):
            na_ids.append(sid)
            continue
        tf = cls.info().recommended_timeframe or "15m"
        tf_groups.setdefault(tf, []).append(sid)

    # Busca candles uma vez por timeframe único
    candle_cache: dict[str, list] = {}
    async with _aiohttp.ClientSession() as session:
        ex = build_exchange(session)
        for tf in tf_groups:
            bar = map_timeframe_for_history(tf)
            candle_cache[tf] = await ex.fetch_candles(req.symbol, bar, limit=500) or []

    # Executa backtests em paralelo
    async def _run(sid: str, tf: str) -> dict:
        candles = candle_cache.get(tf, [])
        info = REGISTRY[sid].info()
        base = {"strategy_id": sid, "strategy_name": info.name, "timeframe": tf}
        try:
            engine = BacktestEngine(sid)
            result = await engine.run(candles)
            result.update(base)
            result["recommendation"] = backtest_recommendation(result)
            result.pop("trades", None)
            result.pop("closed_trades", None)
            result.pop("assumptions", None)
            return result
        except Exception as exc:
            log.warning("Category backtest error %s: %s", sid, exc)
            return {**base, "recommendation": {
                "verdict": "N/A", "level": "na",
                "reasons": [f"Erro na execução: {exc}"],
            }}

    import asyncio as _asyncio
    tasks = [_run(sid, tf) for tf, sids in tf_groups.items() for sid in sids]
    results: list[dict] = list(await _asyncio.gather(*tasks))

    # Acrescenta entradas N/A para estratégias com contexto externo
    for sid in na_ids:
        info = REGISTRY[sid].info()
        results.append({
            "strategy_id": sid,
            "strategy_name": info.name,
            "timeframe": info.recommended_timeframe or "—",
            "recommendation": {
                "verdict": "N/A", "level": "na",
                "reasons": ["Requer contexto externo ao vivo (GEX/Graph) — backtest não suportado."],
            },
        })

    results.sort(key=lambda x: (
        _VERDICT_ORDER.get(x["recommendation"]["verdict"], 9),
        -(x["recommendation"].get("score") or 0),
    ))

    return {"category": prefix, "symbol": req.symbol, "results": results, "total": len(results)}


@app.post("/api/bots/{bot_id}/optimize")
async def optimize_bot_params(bot_id: int, db: Session = Depends(get_db)):
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
    
    # 1. Busca dados históricos (limitado a 500 para otimização)
    import aiohttp

    async with aiohttp.ClientSession() as session:
        ex = build_exchange(session, demo=bot.demo)
        bar = map_timeframe_for_history(bot.timeframe)
        candles = await ex.fetch_candles(bot.symbol, bar, limit=500)
        if not candles:
            raise HTTPException(400, "Dados insuficientes")

    # 2. Executa Otimizador
    optimizer = StrategyOptimizer(bot.strategy_id)
    best_results = await optimizer.optimize(candles, stake_usd=FIXED_STAKE_USD)
    
    return best_results

@app.post("/api/bots/sync-trailing")
async def sync_trailing_stops():
    """Força todos os robôs ativos a sincronizarem o Trailing Stop com a exchange."""
    active_ids = list(manager._instances.keys())
    count = 0
    for bid in active_ids:
        bot = manager._instances.get(bid)
        # Se o robô está em uma operação (direction != 0), ele DEVE sincronizar
        if bot and bot._direction != 0:
            if hasattr(bot, "exchange") and bot.exchange:
                try:
                    await bot.force_sync_trailing(bot.exchange)
                    count += 1
                except Exception as e:
                    log.error("Erro ao sincronizar Bot %d: %s", bid, e)
    log.info("Sincronização manual concluída para %d robôs.", count)
    return {"message": f"Sincronização concluída para {count} robôs.", "count": count}

@app.get("/api/bots/{bot_id}/status")
def bot_status(bot_id: int):
    status = manager.get_status(bot_id)
    if status is None:
        return {"status": "stopped"}
    return status


@app.get("/api/bots/{bot_id}/ai-analysis")
async def bot_ai_analysis(bot_id: int, db: Session = Depends(get_db)):
    import aiohttp as _aio
    
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
        
    status = manager.get_status(bot_id) or {}
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    
    if not api_key:
        return {"analysis": "A chave DEEPSEEK_API_KEY não está configurada no servidor."}

    direction = status.get("direction", 0)
    dir_str = "Comprado (Long)" if direction == 1 else "Vendido (Short)" if direction == -1 else "Aguardando entrada (Neutro)"
    
    active_trade = db.query(TradeModel).filter(
        TradeModel.bot_id == bot_id, 
        TradeModel.exit_price.is_(None), 
        TradeModel.type == "entry"
    ).order_by(TradeModel.id.desc()).first()
    
    trade_context = ""
    if direction != 0 and active_trade:
        import datetime
        now = datetime.datetime.utcnow()
        elapsed = now - active_trade.timestamp
        hours, rem = divmod(elapsed.total_seconds(), 3600)
        minutes, _ = divmod(rem, 60)
        
        last_price = status.get('last_price', 0)
        entry_price = active_trade.entry_price or 1
        pnl_pct = ((last_price - entry_price) / entry_price) * 100
        if direction == -1: pnl_pct = -pnl_pct
        
        trade_context = (
            f"Tempo em Operação: {int(hours)}h {int(minutes)}m\n"
            f"PnL Aberto (Evolução desde a entrada): {pnl_pct:+.2f}%\n"
        )
    
    # Prepara o prompt para a IA
    prompt = (
        "Você é um módulo de IA de diagnóstico algorítmico da plataforma CryptoIntelligence.\n"
        "Com base nos dados abaixo, forneça um relatório técnico, direto e estritamente profissional "
        "sobre o estado atual do bot em 1 parágrafo (máximo 4 frases).\n"
        "REGRAS:\n"
        "1. Vá direto ao ponto. NUNCA inicie com saudações (como 'Olá', 'Beleza', 'Aqui está').\n"
        "2. Seja estritamente descritivo e quantitativo. Não use tom conversacional nem adjetivos desnecessários.\n"
        "3. Apenas reporte o que o algoritmo está fazendo, o tempo de operação, a evolução (PnL) e as distâncias matemáticas.\n"
        "4. NUNCA dê conselhos humanos, nem mande o usuário 'ficar atento' ou 'ficar de olho'.\n\n"
        f"Robô: {bot.name} ({bot.symbol})\n"
        f"Estratégia: {bot.strategy_id}\n"
        f"Estado Atual: {dir_str}\n"
        f"Último Preço: {status.get('last_price', 'N/A')}\n"
        f"Preço de Entrada: {status.get('entry_price', 'N/A')}\n"
        f"Take Profit: {status.get('tp1_price', 'N/A')}\n"
        f"Stop Loss: {status.get('sl_price', 'N/A')}\n"
        f"{trade_context}"
    )

    try:
        async with _aio.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       "deepseek-chat",
                    "messages":    [{"role": "user", "content": prompt}],
                    "max_tokens":  350,
                    "temperature": 0.7,
                },
                timeout=_aio.ClientTimeout(total=20),
            ) as r:
                data = await r.json()
                text = data["choices"][0]["message"]["content"].strip()
                
                # Save to database
                log_entry = AiAnalysisLogModel(
                    bot_id=bot_id,
                    prompt=prompt,
                    response=text
                )
                db.add(log_entry)
                db.commit()
                
                return {"analysis": text}
    except Exception as exc:
        return {"analysis": f"Erro de conexão com a IA: {exc}"}


@app.get("/api/bots/{bot_id}/graph")
def bot_graph(bot_id: int):
    """Retorna o estado atual do grafo de correlação do bot."""
    inst = manager._instances.get(bot_id)
    if inst is None:
        return {"regime": None, "nodes": [], "edges": [], "metrics": {}}
    state = inst._graph_state
    if state is None:
        return {"regime": None, "nodes": [], "edges": [], "metrics": {}}
    return state


@app.get("/api/bots/{bot_id}/graph/interpret")
async def graph_interpret(bot_id: int, db: Session = Depends(get_db)):
    """Gera interpretação em linguagem natural do grafo via DeepSeek (com fallback por regras)."""
    import aiohttp as _aio

    inst = manager._instances.get(bot_id)
    if inst is None or inst._graph_state is None:
        return {"interpretation": None, "source": "unavailable"}

    state     = inst._graph_state
    regime    = state.get("regime",  {})
    metrics   = state.get("metrics", {})
    sentiment = state.get("sentiment")

    regime_name = regime.get("name", "neutral")
    regime_conf = regime.get("confidence", 0)
    bars        = regime.get("bars_in_regime", 0)
    density     = metrics.get("graph_density", 0)
    c_btc       = metrics.get("centrality_btc", 0)
    c_eth       = metrics.get("centrality_eth", 0)
    eth_leads   = metrics.get("eth_leads", False)
    n_comm      = metrics.get("n_communities", 0)
    n_edges     = metrics.get("n_edges", 0)

    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()

    if not api_key:
        text = _rule_based_interpretation(
            regime_name, regime_conf, bars,
            density, c_btc, c_eth, eth_leads, n_comm, n_edges, sentiment,
        )
        return {"interpretation": text, "source": "rules"}

    eth_lead_str = (
        "ETH está liderando BTC (sinal antecipado)"
        if eth_leads else
        "BTC está em posição dominante na rede"
    )
    sent_str = (
        f"Sentiment FinGPT: {sentiment:+.2f}"
        if sentiment is not None else
        "Sentiment externo: não disponível"
    )

    prompt = (
        "Você é um especialista em análise técnica e estrutura de mercado de criptoativos. "
        "Analise os dados do grafo de correlação abaixo e escreva UM parágrafo sucinto (3-5 frases) "
        "em português, explicando o que esse estado de rede significa para o BTC agora e qual o "
        "comportamento esperado do mercado. Seja objetivo, técnico mas acessível. "
        "Não use bullets nem títulos, apenas prosa corrida.\n\n"
        f"Estado do grafo:\n"
        f"- Regime: {regime_name} (confiança: {regime_conf * 100:.0f}%, há {bars} barra(s) neste regime)\n"
        f"- Densidade da rede: {density:.4f} ({n_edges} de 10 arestas possíveis ativas)\n"
        f"- Centralidade BTC: {c_btc:.4f}\n"
        f"- Centralidade ETH: {c_eth:.4f}\n"
        f"- {eth_lead_str}\n"
        f"- Comunidades detectadas: {n_comm}\n"
        f"- {sent_str}\n"
    )

    try:
        async with _aio.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       "deepseek-chat",
                    "messages":    [{"role": "user", "content": prompt}],
                    "max_tokens":  300,
                    "temperature": 0.65,
                },
                timeout=_aio.ClientTimeout(total=20),
            ) as r:
                data = await r.json()
                text = data["choices"][0]["message"]["content"].strip()
                
                # Save to database
                # Save to database — importação correta para o pacote
                from .database import AiAnalysisLogModel as AiLog
                log_entry = AiLog(
                    bot_id=bot_id,
                    prompt=prompt,
                    response=text
                )
                db.add(log_entry)
                db.commit()
                
                return {"interpretation": text, "source": "deepseek"}
    except Exception as exc:
        log.warning("DeepSeek interpret falhou: %s", exc)
        text = _rule_based_interpretation(
            regime_name, regime_conf, bars,
            density, c_btc, c_eth, eth_leads, n_comm, n_edges, sentiment,
        )
        return {"interpretation": text, "source": "rules"}


def _rule_based_interpretation(
    regime_name: str, regime_conf: float, bars: int,
    density: float, c_btc: float, c_eth: float,
    eth_leads: bool, n_comm: int, n_edges: int,
    sentiment,
) -> str:
    """Interpretação baseada em regras — fallback sem DeepSeek."""
    regime_desc = {
        "trending": (
            f"O mercado encontra-se em regime trending com {regime_conf * 100:.0f}% de confiança "
            f"({bars} barra(s)), indicando que o BTC lidera a rede de correlação e há momentum "
            "direcional consistente — condição favorável para seguir o sinal das EMAs."
        ),
        "lagging": (
            f"O regime atual é lagging ({regime_conf * 100:.0f}% de confiança, {bars} barra(s)): "
            "o ETH apresenta maior centralidade de eigenvector que o BTC neste cluster, "
            "sugerindo que o Ethereum está antecipando o movimento e o Bitcoin tende a segui-lo "
            "em 1 a 3 barras — a estratégia opera com sensibilidade aumentada."
        ),
        "neutral": (
            f"O mercado está em regime neutral ({regime_conf * 100:.0f}% de confiança, "
            f"{bars} barra(s)), sem liderança clara entre os ativos da rede. "
            "A estratégia exige crossover de EMA muito limpo antes de abrir posição."
        ),
        "transition": (
            f"O regime acaba de mudar (transição detectada há {bars} barra(s)), "
            "indicando instabilidade estrutural recente na rede de correlação. "
            "O bot mantém hold por segurança até o novo regime se consolidar."
        ),
        "chaos": (
            f"O mercado está em caos — densidade de rede {density:.2f} abaixo do limiar mínimo, "
            f"com apenas {n_edges} de 10 arestas ativas. "
            "Não há estrutura de correlação confiável; bot em modo de espera total, sem sinais emitidos."
        ),
    }.get(regime_name, "Regime indefinido — aguardando dados suficientes.")

    parts = [regime_desc]

    if density >= 0.8:
        parts.append(
            f"A rede está altamente sincronizada ({n_edges}/10 arestas ativas), "
            "refletindo correlação forte entre todos os principais ativos — "
            "típico de mercados dominados por sentimento macro global."
        )
    elif density >= 0.5:
        parts.append(
            f"A rede apresenta correlação moderada ({n_edges}/10 arestas), "
            "com alguma diversificação de comportamento entre os ativos."
        )

    if eth_leads:
        parts.append(
            "ETH lidera BTC neste cluster: historicamente, isso antecipa "
            "movimentos do Bitcoin em 1 a 3 barras — sinal antecipado ativo."
        )

    if sentiment is not None:
        if sentiment > 0.6:
            parts.append(
                f"O sentiment externo (FinGPT: {sentiment:+.2f}) confirma viés bullish, "
                "alinhado com o sinal do grafo."
            )
        elif sentiment < -0.6:
            parts.append(
                f"O sentiment externo (FinGPT: {sentiment:+.2f}) sinaliza forte pressão vendedora, "
                "potencialmente contrário ao sinal do grafo — posição bloqueada automaticamente."
            )

    return " ".join(parts)


# ── Rotas: Calendário Económico ───────────────────────────────────────────────

@app.get("/api/calendar/events")
def calendar_events():
    """Próximos eventos High-impact e estado do gate actual."""
    in_window_bollinger, ev_b = calendar_feed.is_high_impact_window("bollinger_breakout")
    in_window_graph,     ev_g = calendar_feed.is_high_impact_window("graph_regime")
    return {
        "upcoming":        calendar_feed.get_upcoming_events(8),
        "latest_surprise": calendar_feed.latest_surprise,
        "gate_active": {
            "bollinger_breakout": {"active": in_window_bollinger, "event": ev_b or None},
            "graph_regime":       {"active": in_window_graph,     "event": ev_g or None},
        },
    }


# ── Rotas: Signal Logs / Optimização ─────────────────────────────────────────

_SIGNAL_MIN = 50  # sinais mínimos para habilitar análise


@app.get("/api/bots/{bot_id}/signal-logs/readiness")
def signal_logs_readiness(bot_id: int, db: Session = Depends(get_db)):
    """Retorna se há dados suficientes para análise de optimização."""
    from sqlalchemy import func
    signal_count = db.query(func.count(SignalLogModel.id)).filter(
        SignalLogModel.bot_id == bot_id).scalar() or 0
    linked_count = db.query(func.count(SignalLogModel.id)).filter(
        SignalLogModel.bot_id == bot_id,
        SignalLogModel.resulted_in_trade_id.isnot(None),
    ).scalar() or 0
    return {
        "ready":    signal_count >= _SIGNAL_MIN,
        "signal_count":          signal_count,
        "signal_count_required": _SIGNAL_MIN,
        "linked_trades":         linked_count,
    }


@app.get("/api/bots/{bot_id}/signal-logs/analysis")
def signal_logs_analysis(bot_id: int, db: Session = Depends(get_db)):
    """Análise estatística dos sinais para sugestões de optimização."""
    import statistics as _stats
    from sqlalchemy import func

    signal_count = db.query(func.count(SignalLogModel.id)).filter(
        SignalLogModel.bot_id == bot_id).scalar() or 0
    if signal_count < _SIGNAL_MIN:
        raise HTTPException(400, f"Dados insuficientes: {signal_count}/{_SIGNAL_MIN} sinais")

    # Distribuição de sinais
    dist_rows = db.query(SignalLogModel.signal, func.count(SignalLogModel.id)).filter(
        SignalLogModel.bot_id == bot_id
    ).group_by(SignalLogModel.signal).all()
    signal_dist = {s: c for s, c in dist_rows}

    # Top motivos de HOLD
    hold_rows = db.query(SignalLogModel.hold_reason, func.count(SignalLogModel.id)).filter(
        SignalLogModel.bot_id == bot_id,
        SignalLogModel.signal == "hold",
        SignalLogModel.hold_reason.isnot(None),
    ).group_by(SignalLogModel.hold_reason).order_by(
        func.count(SignalLogModel.id).desc()
    ).limit(5).all()
    top_hold_reasons = [{"reason": r, "count": c} for r, c in hold_rows]

    # Análise de trades ligados a sinais
    linked = db.query(SignalLogModel).filter(
        SignalLogModel.bot_id == bot_id,
        SignalLogModel.resulted_in_trade_id.isnot(None),
    ).all()

    trade_analysis = None
    if linked:
        trade_ids = [s.resulted_in_trade_id for s in linked]
        trades_by_id = {t.id: t for t in db.query(TradeModel).filter(
            TradeModel.id.in_(trade_ids),
            TradeModel.pnl.isnot(None),
        ).all()}

        wins, losses = [], []
        for sig in linked:
            trade = trades_by_id.get(sig.resulted_in_trade_id)
            if trade and trade.pnl is not None:
                (wins if trade.pnl > 0 else losses).append(sig)

        # Comparação de indicadores: vencedores vs perdedores
        all_inds = set()
        for s in wins + losses:
            if s.indicators:
                all_inds.update(s.indicators.keys())

        indicator_comparison = {}
        for ind in sorted(all_inds):
            win_vals  = [s.indicators[ind] for s in wins  if s.indicators and ind in s.indicators]
            lose_vals = [s.indicators[ind] for s in losses if s.indicators and ind in s.indicators]
            if win_vals or lose_vals:
                indicator_comparison[ind] = {
                    "win_avg":   round(_stats.mean(win_vals),  4) if win_vals  else None,
                    "lose_avg":  round(_stats.mean(lose_vals), 4) if lose_vals else None,
                    "win_count": len(win_vals),
                    "lose_count": len(lose_vals),
                }

        # Win rate por regime (se meta tiver regime_state)
        regime_stats: dict = {}
        for sig in linked:
            trade = trades_by_id.get(sig.resulted_in_trade_id)
            if not (trade and sig.meta and "regime_state" in sig.meta):
                continue
            regime = sig.meta["regime_state"]
            bucket = regime_stats.setdefault(regime, {"wins": 0, "losses": 0})
            if trade.pnl and trade.pnl > 0:
                bucket["wins"] += 1
            elif trade.pnl is not None:
                bucket["losses"] += 1
        for bucket in regime_stats.values():
            total = bucket["wins"] + bucket["losses"]
            bucket["win_rate"] = round(bucket["wins"] / total * 100, 1) if total else 0
            bucket["total"] = total

        total_linked = len(wins) + len(losses)
        trade_analysis = {
            "total_linked": total_linked,
            "wins":         len(wins),
            "losses":       len(losses),
            "win_rate":     round(len(wins) / total_linked * 100, 1) if total_linked else 0,
            "indicator_comparison": indicator_comparison,
            "regime_stats": regime_stats,
        }

    suggestions = _optimization_suggestions(signal_dist, top_hold_reasons, trade_analysis, signal_count)

    return {
        "signal_count":       signal_count,
        "signal_distribution": signal_dist,
        "top_hold_reasons":   top_hold_reasons,
        "trade_analysis":     trade_analysis,
        "suggestions":        suggestions,
    }


def _optimization_suggestions(signal_dist: dict, hold_reasons: list,
                               trade_analysis: Optional[dict], signal_count: int) -> list[str]:
    tips = []
    total = sum(signal_dist.values()) or 1
    hold_pct  = signal_dist.get("hold", 0) / total * 100
    entry_pct = 100 - hold_pct

    if hold_pct > 85:
        tips.append(
            f"O bot está em HOLD {hold_pct:.0f}% do tempo — critérios de entrada muito restritivos. "
            "Considere relaxar os thresholds dos indicadores."
        )
    elif hold_pct < 30:
        tips.append(
            f"O bot entra em posição {entry_pct:.0f}% do tempo — parâmetros muito permissivos, "
            "aumentando o risco de sinais de baixa qualidade."
        )

    if hold_reasons:
        top_reason = hold_reasons[0]["reason"]
        top_count  = hold_reasons[0]["count"]
        tips.append(
            f"Motivo de HOLD mais frequente: '{top_reason}' ({top_count}×). "
            "Ajustar o parâmetro correspondente pode aumentar o número de entradas."
        )

    if trade_analysis:
        wr = trade_analysis["win_rate"]
        tl = trade_analysis["total_linked"]
        if tl < 5:
            tips.append(
                f"Apenas {tl} trade(s) vinculados a sinais — acumule mais dados antes de ajustar parâmetros."
            )
        elif wr >= 60:
            tips.append(
                f"Win rate de {wr}% é sólido. Mantenha o stake fixo de US$100 e ajuste apenas critérios/ativos."
            )
        elif wr < 40:
            tips.append(
                f"Win rate de {wr}% está abaixo de 40%. Revise os critérios de entrada ou pause o bot temporariamente."
            )

        regime_stats = trade_analysis.get("regime_stats", {})
        if regime_stats:
            best  = max(regime_stats.items(), key=lambda x: x[1].get("win_rate", 0))
            worst = min(regime_stats.items(), key=lambda x: x[1].get("win_rate", 0))
            if best[1]["total"] >= 3:
                tips.append(
                    f"Regime '{best[0]}' teve win rate de {best[1]['win_rate']}% "
                    f"({best[1]['total']} trades) — melhor contexto para operar."
                )
            if worst[1]["total"] >= 3 and worst[1]["win_rate"] < 35:
                tips.append(
                    f"Evite entradas em regime '{worst[0]}' "
                    f"(win rate {worst[1]['win_rate']}% em {worst[1]['total']} trades)."
                )

    if not tips:
        tips.append(
            f"Com {signal_count} sinais registrados os padrões ainda são inconclusivos. "
            "Continue acumulando dados para recomendações mais robustas."
        )
    return tips


# ── Rotas: Trades ─────────────────────────────────────────────────────────────

@app.get("/api/trades")
def list_trades(bot_id: Optional[int] = None,
                limit: int = 200,
                db: Session = Depends(get_db)):
    # Retorna apenas registros de SAÍDA (exit/tp1) para o histórico visual
    # O registro de entrada fica no banco apenas para o summary calcular estatísticas
    q = db.query(TradeModel).filter(TradeModel.type.in_(["exit", "tp1"]))
    if bot_id:
        q = q.filter(TradeModel.bot_id == bot_id)
    trades = q.order_by(TradeModel.timestamp.desc()).limit(limit).all()
    # Build a bot-id → name map in one query to avoid N+1
    bot_ids = {t.bot_id for t in trades}
    bot_names = {b.id: b.name for b in db.query(BotModel).filter(BotModel.id.in_(bot_ids)).all()}
    def _serialize(t):
        fee = t.fee  # None = ainda não sincronizado
        net = round(t.pnl - fee, 2) if (t.pnl is not None and fee is not None) else None
        return {
            "id": t.id, "bot_id": t.bot_id, "bot_name": bot_names.get(t.bot_id, f"Bot {t.bot_id}"),
            "type": t.type, "event": t.event, "direction": t.direction, "symbol": t.symbol,
            "size": t.size, "entry_price": t.entry_price, "exit_price": t.exit_price,
            "sl_price": t.sl_price, "tp1_price": t.tp1_price, "atr": t.atr,
            "pnl": t.pnl, "fee": fee, "net_pnl": net,
            "daily_pnl": t.daily_pnl, "source": t.source,
            "timestamp": t.timestamp.isoformat() + "Z" if t.timestamp else None,
            "closed_at": t.closed_at.isoformat() + "Z" if t.closed_at else None,
        }
    return [_serialize(t) for t in trades]


async def _sync_trade_fees_impl(db: Session, days_back: int = 30) -> dict:
    """
    Sincroniza taxas de corretagem da OKX para trades registrados.
    Busca trades sem fee preenchida e tenta calcular a partir dos dados disponíveis.
    """
    from datetime import datetime, timedelta
    
    cutoff = datetime.utcnow() - timedelta(days=days_back)
    pending = db.query(TradeModel).filter(
        TradeModel.type == "exit",
        TradeModel.fee.is_(None),
        TradeModel.timestamp >= cutoff,
    ).all()
    
    updated = 0
    for trade in pending:
        # Calcula taxa estimada: 0.08% (taker) do valor da ordem
        if trade.exit_price and trade.size:
            order_value = trade.exit_price * trade.size
            estimated_fee = order_value * 0.0008  # 0.08% taker fee OKX
            trade.fee = round(estimated_fee, 4)
            
            # Também atualiza o registro de entrada correspondente
            # para que o summary calcule corretamente
            entry_trade = db.query(TradeModel).filter(
                TradeModel.bot_id == trade.bot_id,
                TradeModel.type == "entry",
                TradeModel.exit_price == trade.exit_price,
            ).first()
            if entry_trade and entry_trade.fee is None:
                entry_trade.fee = trade.fee
            
            updated += 1
    
    db.commit()
    return {"updated": updated, "skipped": False, "reason": f"Taxas estimadas para {updated} trades"}


@app.post("/api/trades/sync-fees")
async def sync_trade_fees(days_back: int = 30, db: Session = Depends(get_db)):
    """
    Dispara manualmente a sincronização de taxas de corretagem.
    Preenche o campo `fee` nos trades sem valor registrado.
    """
    result = await _sync_trade_fees_impl(db, days_back=days_back)
    return result


@app.get("/api/trades/summary")
def trades_summary(bot_id: Optional[int] = None,
                   db: Session = Depends(get_db)):
    # Usa trades de ENTRADA fechados (type="entry" + exit_price preenchido) para evitar
    # duplicação, pois cada round-trip gera registros entry + tp1 + exit no banco.
    # O campo pnl e fee ficam no entry record.
    q = db.query(TradeModel).filter(
        TradeModel.type == "entry",
        TradeModel.exit_price.isnot(None),
        TradeModel.pnl.isnot(None),
    )
    if bot_id:
        q = q.filter(TradeModel.bot_id == bot_id)
    trades = q.all()
    if not trades:
        return {"total_trades": 0, "total_pnl": 0, "win_rate": 0,
                "total_fees": 0, "net_pnl": 0, "fees_synced": False}

    pnls       = [t.pnl for t in trades]
    fees       = [t.fee if t.fee is not None else 0.0 for t in trades]
    net_pnls   = [p - f for p, f in zip(pnls, fees)]
    fees_known = sum(1 for t in trades if t.fee is not None)

    total_gross = sum(pnls)
    total_fees  = sum(fees)
    total_net   = sum(net_pnls)

    wins_gross  = sum(1 for p in pnls     if p > 0)
    wins_net    = sum(1 for p in net_pnls if p > 0)

    return {
        "total_trades":  len(pnls),
        "total_pnl":     round(total_gross, 2),      # P&L bruto (sem taxas) — retro-compat
        "total_fees":    round(total_fees, 4),        # Corretagem acumulada
        "net_pnl":       round(total_net, 2),         # P&L líquido após taxas
        "fees_synced":   fees_known,                  # quantos trades têm fee registrada
        "fees_pending":  len(trades) - fees_known,    # ainda sem sincronização
        "wins":          wins_gross,
        "losses":        len(pnls) - wins_gross,
        "wins_net":      wins_net,
        "win_rate":      round(wins_gross / len(pnls) * 100, 1),
        "win_rate_net":  round(wins_net   / len(pnls) * 100, 1),
        "best_trade":    round(max(pnls), 2),
        "worst_trade":   round(min(pnls), 2),
        "avg_trade":     round(total_gross / len(pnls), 2),
        "avg_fee":       round(total_fees  / len(pnls), 4) if pnls else 0,
    }


@app.get("/api/bots/{bot_id}/trade-report")
async def trade_report(bot_id: int, trade_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Relatório evolutivo completo de um trade, com análise de IA (DeepSeek). Resultado persistido."""
    import aiohttp as _aio
    import datetime as _dt

    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")

    # ── 1. Escolhe o trade ──────────────────────────────────────────────────────
    # Preferimos trades de entrada (entry) fechados pois têm dados completos
    q = db.query(TradeModel).filter(
        TradeModel.bot_id == bot_id,
        TradeModel.type == "entry",
        TradeModel.exit_price.isnot(None),
    )
    trade = db.get(TradeModel, trade_id) if trade_id else q.order_by(TradeModel.timestamp.desc()).first()
    if not trade:
        raise HTTPException(404, "Nenhum trade finalizado encontrado para este bot")

    # All closed trades for this bot (for the selector)
    all_trades = q.order_by(TradeModel.timestamp.desc()).all()

    # ── 2. Busca candles via signal_logs ────────────────────────────────────────
    window_start = trade.timestamp - _dt.timedelta(days=30)
    window_end   = (trade.closed_at or _dt.datetime.utcnow()) + _dt.timedelta(days=7)
    logs = (
        db.query(SignalLogModel)
        .filter(
            SignalLogModel.bot_id == bot_id,
            SignalLogModel.timestamp >= window_start,
            SignalLogModel.timestamp <= window_end,
            SignalLogModel.candle_close.isnot(None),
        )
        .order_by(SignalLogModel.timestamp)
        .limit(300)
        .all()
    )

    # Convert to [epoch_ms, open, high, low, close, volume] (Chart.jsx format)
    candles = [
        [
            int(log.timestamp.timestamp() * 1000),
            log.candle_open or log.candle_close,
            log.candle_high or log.candle_close,
            log.candle_low  or log.candle_close,
            log.candle_close,
            log.candle_volume or 0,
        ]
        for log in logs
    ]

    # ── 3. Estatísticas do trade ────────────────────────────────────────────────
    closes    = [c[4] for c in candles]
    peak      = max(closes) if trade.direction == "LONG" else min(closes) if closes else 0
    n_candles = len(candles)
    entry_ts  = trade.timestamp
    closed_ts = trade.closed_at
    duration  = str(closed_ts - entry_ts).split(".")[0] if closed_ts else "Em aberto"
    pnl_pct   = 0.0
    if trade.entry_price and trade.exit_price:
        pnl_pct = (trade.exit_price - trade.entry_price) / trade.entry_price * 100
        if trade.direction == "SHORT":
            pnl_pct = -pnl_pct

    # ── 4. Marcadores do gráfico (entry + exit) ─────────────────────────────────
    markers = [
        {
            "type":        "entry",
            "timestamp":   entry_ts.isoformat(),
            "direction":   trade.direction,
            "entry_price": trade.entry_price,
            "sl_price":    trade.sl_price,
            "tp1_price":   trade.tp1_price,
        }
    ]
    if closed_ts and trade.exit_price:
        markers.append({
            "type":      "exit",
            "timestamp": closed_ts.isoformat(),
            "direction": trade.direction,
            "pnl":       round(trade.pnl or 0, 2),
            "event":     trade.event or "SW_TS",
        })

    # ── 5. Análise de IA (DeepSeek) — usa cache se já gerado ───────────────────
    cached = db.query(TradeReportModel).filter(TradeReportModel.trade_id == trade.id).first()
    ai_analysis = cached.ai_analysis if cached else None

    if not ai_analysis:
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if api_key:
            prompt = (
                "Você é um especialista em trading algorítmico. Analise o relatório abaixo de uma operação "
                "já finalizada e forneça uma análise técnica objetiva em 3-4 frases.\n"
                "REGRAS: 1) Nunca use saudações. 2) Seja direto e quantitativo. "
                "3) Avalie qualidade da entrada, gestão do risco, e eficiência da saída. "
                "4) Se o resultado foi positivo, explique o motivo técnico. Se negativo, aponte o erro.\n\n"
                f"Robô: {bot.name} | Estratégia: {bot.strategy_id} | Símbolo: {trade.symbol}\n"
                f"Direção: {trade.direction}\n"
                f"Entrada: ${trade.entry_price:.4f} em {entry_ts.strftime('%Y-%m-%d %H:%M UTC')}\n"
                f"Saída: ${trade.exit_price:.4f} em {closed_ts.strftime('%Y-%m-%d %H:%M UTC') if closed_ts else 'N/A'}\n"
                f"PnL: {pnl_pct:+.2f}% (${trade.pnl:.2f})\n"
                f"Duração: {duration}\n"
                f"Stop Loss Inicial: ${trade.sl_price:.4f}\n"
                f"Preço Máximo na Operação: ${peak:.4f}\n"
                f"Candles na Operação: {n_candles}\n"
            )
            try:
                async with _aio.ClientSession() as session:
                    async with session.post(
                        "https://api.deepseek.com/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                              "max_tokens": 400, "temperature": 0.5},
                        timeout=_aio.ClientTimeout(total=25),
                    ) as r:
                        data   = await r.json()
                        ai_analysis = data["choices"][0]["message"]["content"].strip()
                        report = TradeReportModel(trade_id=trade.id, bot_id=bot_id, ai_analysis=ai_analysis)
                        db.add(report)
                        db.commit()
            except Exception as exc:
                log.warning("DeepSeek trade-report falhou: %s", exc)
                ai_analysis = "Análise de IA indisponível no momento."
        else:
            ai_analysis = "DEEPSEEK_API_KEY não configurada."

    return {
        "bot": {
            "id": bot.id, "name": bot.name,
            "strategy_id": bot.strategy_id, "symbol": bot.symbol,
            "timeframe": bot.timeframe,
        },
        "trade": {
            "id":          trade.id,
            "direction":   trade.direction,
            "symbol":      trade.symbol,
            "entry_price": trade.entry_price,
            "exit_price":  trade.exit_price,
            "sl_price":    trade.sl_price,
            "tp1_price":   trade.tp1_price,
            "pnl":         round(trade.pnl or 0, 2),
            "pnl_pct":     round(pnl_pct, 2),
            "peak_price":  round(peak, 4) if peak else None,
            "n_candles":   n_candles,
            "duration":    duration,
            "entry_time":  entry_ts.isoformat(),
            "closed_at":   closed_ts.isoformat() if closed_ts else None,
        },
        "all_trades": [
            {"id": t.id, "entry_price": t.entry_price, "exit_price": t.exit_price,
             "pnl": round(t.pnl or 0, 2), "entry_time": t.timestamp.isoformat()}
            for t in all_trades
        ],
        "candles":    candles,
        "markers":    markers,
        "ai_analysis": ai_analysis,
    }


@app.get("/api/account/balance")
async def account_balance(currency: Optional[str] = None, demo: bool = False):
    import aiohttp as _aio
    # O valor default do sistema (env) continua sendo o fallback se demo não for passado
    env_demo   = get_default_demo_mode()
    final_demo = demo if demo is not None else env_demo

    async with _aio.ClientSession() as session:
        ex = build_exchange(session, demo=final_demo)
        try:
            if currency:
                bal = await ex.get_balance(currency)
                return {"currency": currency, "available": bal, "assets": [{"ccy": currency, "available": bal}]}
            else:
                assets = await ex.get_all_balances()
                # OKX usa USDT; aceita qualquer moeda base disponível
                primary = next(
                    (a for a in assets if a["ccy"] in ("USD", "USDT")), None
                )
                ccy = primary["ccy"] if primary else "USD"
                bal = primary["total"] if primary else 0.0
                return {"currency": ccy, "available": bal, "assets": assets}
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc


@app.get("/api/account/snapshot")
async def account_snapshot(demo: bool = False, db: Session = Depends(get_db)):
    """
    Snapshot rápido da conta OKX para reconciliação no Dashboard.
    Retorna: equity, cash, unrealized_pl, long_market_value, day_pnl, positions_count.
    """
    import aiohttp as _aio
    env_demo = get_default_demo_mode()
    final_demo = demo if demo is not None else env_demo

    async with _aio.ClientSession() as session:
        ex = build_exchange(session, demo=final_demo)
        try:
            account, positions = await asyncio.gather(
                ex.get_account_summary(),
                ex.get_all_positions(),
                return_exceptions=True,
            )
            if isinstance(account, Exception):
                account = {}
            if isinstance(positions, Exception):
                positions = []

            equity      = float(account.get("equity", 0))
            last_equity = float(account.get("last_equity", equity))
            cash        = float(account.get("cash", 0))
            unreal_pl   = float(account.get("unrealized_pl") or 0)
            long_mv     = float(account.get("long_market_value") or 0)
            positions_count = len(positions) if isinstance(positions, list) else 0

            # OKX /account/positions does not report cash-spot balances as
            # positions. For this spot-only app, count active bot symbols via
            # get_position(), which maps base-currency balances to LONG.
            active_symbols = []
            for bot in db.query(BotModel).all():
                status = manager.get_status(bot.id)
                if status and int(status.get("direction", 0) or 0) != 0:
                    active_symbols.append(bot.symbol)
            if active_symbols:
                spot_results = await asyncio.gather(
                    *[ex.get_position(symbol) for symbol in active_symbols],
                    return_exceptions=True,
                )
                spot_positions = [
                    pos for pos in spot_results
                    if not isinstance(pos, Exception)
                    and pos is not None
                    and abs(float(getattr(pos, "size", 0.0) or 0.0)) > 1e-9
                ]
                if spot_positions:
                    positions_count = max(positions_count, len(spot_positions))
                    spot_mv = sum(
                        abs(float(pos.size or 0.0)) * float(pos.avg_price or 0.0)
                        for pos in spot_positions
                    )
                    long_mv = max(long_mv, spot_mv)

            return {
                "equity":            round(equity, 2),
                "cash":              round(cash, 2),
                "unrealized_pl":     round(unreal_pl, 2),
                "long_market_value": round(long_mv, 2),
                "day_pnl":           round(equity - last_equity, 2),
                "positions_count":   positions_count,
            }
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc


# ── Rotas: Mercado ────────────────────────────────────────────────────────────

@app.get("/api/market/rank-assets")
async def rank_assets(strategy_id: str = "graph_regime", timeframe: str = "15m"):
    """Classifica os 5 ativos por adequação à estratégia e timeframe atuais."""
    import pandas as pd
    import pandas_ta as ta

    RANK_ASSETS = get_ranked_assets_universe()
    bar = map_timeframe_for_history(timeframe)

    ohlcv: dict[str, list] = {}
    import aiohttp as _aio
    async with _aio.ClientSession() as session:
        ex = build_exchange(session)
        for symbol in RANK_ASSETS:
            try:
                candles = await ex.fetch_candles(symbol, bar, limit=100)
                rows = [[c.epoch, c.open, c.high, c.low, c.close, c.volume] for c in candles]
                if rows:
                    ohlcv[symbol] = rows
            except Exception as exc:
                log.warning("rank_assets: falha %s: %s", symbol, exc)

    if not ohlcv:
        raise HTTPException(503, "Não foi possível buscar dados de mercado")

    results: list[dict] = []
    graph_info: Optional[dict] = None
    warning: Optional[str] = None

    # ── Graph Regime: scoring por centralidade de eigenvector ─────────────────
    if strategy_id == "graph_regime":
        from .graph import CorrelationGraphBuilder, compute_metrics, RegimeClassifier

        price_matrix = {s: [float(r[4]) for r in rows]
                        for s, rows in ohlcv.items()}
        G = CorrelationGraphBuilder().build(price_matrix, window=48, threshold=0.65)
        metrics = compute_metrics(G)
        regime_state = RegimeClassifier().classify(metrics)

        if "error" in metrics:
            warning = f"Grafo inválido: {metrics['error']} — sugestão indisponível."
            centrality_all: dict = {}
        else:
            centrality_all = metrics.get("centrality_all", {})

        for symbol in RANK_ASSETS:
            results.append({
                "symbol": symbol,
                "score":  round(centrality_all.get(symbol, 0.0), 4),
                "details": {"metric": "eigenvector_centrality"},
            })

        if not warning:
            density = metrics.get("graph_density", 0)
            graph_info = {
                "regime":      regime_state.name,
                "regime_conf": round(regime_state.confidence, 4),
                "density":     round(density, 4),
                "n_edges":     metrics.get("n_edges", 0),
            }
            if density < 0.25:
                warning = "Mercado fragmentado (chaos) — sugestão pouco confiável agora."
            elif regime_state.name == "transition":
                warning = "Regime em transição — aguarde estabilização antes de operar."

    # ── Pivot Sniper: scoring por estrutura de pivôs e proximidade à EMA50 ──────
    elif strategy_id == "pivot_sniper":
        # Os três factores que determinam a qualidade de um setup Pivot Sniper:
        #   ema50_slope    (40%) — inclinação da EMA50 em 20 barras / ATR: tendência activa
        #   pullback_prox  (40%) — distância do preço à EMA50: pullback iminente (1=na EMA)
        #   pivot_richness (20%) — nº de swing-points recentes: mais S/R potencial
        raw: dict[str, dict] = {}
        for symbol, rows in ohlcv.items():
            if len(rows) < 70:
                continue
            closes = [float(r[4]) for r in rows]
            highs  = [float(r[2]) for r in rows]
            lows   = [float(r[3]) for r in rows]
            df = pd.DataFrame({"close": closes, "high": highs, "low": lows})

            ema50 = ta.ema(df["close"], length=50)
            atr14 = ta.atr(df["high"], df["low"], df["close"], length=14)
            if ema50 is None or atr14 is None:
                continue

            import math as _math
            e_now    = float(ema50.iloc[-1])
            e_20ago  = float(ema50.iloc[-20])
            atr_now  = float(atr14.iloc[-1])
            cl_now   = closes[-1]
            if e_now == 0 or atr_now == 0 or _math.isnan(e_now) or _math.isnan(e_20ago) or _math.isnan(atr_now):
                continue

            # Inclinação da EMA50 em 20 barras, normalizada pelo ATR
            ema_slope = abs(e_now - e_20ago) / atr_now

            # Proximidade ao pullback: 1.0 = price na EMA, 0 = dist >= 2×ATR
            pullback_prox = max(0.0, 1.0 - abs(cl_now - e_now) / (atr_now * 2.0))

            # Riqueza de pivôs: contagem de swing-highs + swing-lows nas últimas 50 velas
            lb = 4
            win_h = highs[-50:];  win_l = lows[-50:]
            n_w = len(win_h)
            pivot_count = sum(
                1 for i in range(lb, n_w - lb)
                if all(win_h[j] <= win_h[i] for j in range(i - lb, i + lb + 1) if j != i)
                or all(win_l[j] >= win_l[i] for j in range(i - lb, i + lb + 1) if j != i)
            )
            raw[symbol] = {
                "ema_slope":    ema_slope,
                "pullback_prox": pullback_prox,
                "pivot_count":  pivot_count,
            }

        def _norm(vals: list[float]) -> list[float]:
            mn, mx = min(vals), max(vals)
            return [(v - mn) / (mx - mn) if mx > mn else 0.5 for v in vals]

        syms = list(raw.keys())
        if syms:
            n_slope  = _norm([raw[s]["ema_slope"]     for s in syms])
            n_pull   = [raw[s]["pullback_prox"] for s in syms]   # já em [0,1]
            n_pivots = _norm([raw[s]["pivot_count"]   for s in syms])
            for i, symbol in enumerate(syms):
                score = 0.40 * n_slope[i] + 0.40 * n_pull[i] + 0.20 * n_pivots[i]
                r = raw[symbol]
                results.append({
                    "symbol": symbol,
                    "score":  round(score, 4),
                    "details": {
                        "metric":          "pivot_sniper",
                        "ema50_slope_atr": round(r["ema_slope"],    3),
                        "pullback_prox":   round(r["pullback_prox"],3),
                        "pivot_count":     r["pivot_count"],
                    },
                })

        for symbol in RANK_ASSETS:
            if symbol not in {r["symbol"] for r in results}:
                results.append({
                    "symbol": symbol, "score": 0.0,
                    "details": {"metric": "pivot_sniper", "error": "dados insuficientes"},
                })

    # ── Three Line Bar: scoring por formação de padrão e condição de sobrevenda ─
    elif strategy_id == "three_line_bar" or strategy_id == "S005":
        raw: dict[str, dict] = {}
        for symbol, rows in ohlcv.items():
            if len(rows) < 220:
                continue
            closes = [float(r[4]) for r in rows]
            highs  = [float(r[2]) for r in rows]
            lows   = [float(r[3]) for r in rows]
            df = pd.DataFrame({"close": closes, "high": highs, "low": lows})

            ema200 = ta.ema(df["close"], length=200)
            rsi14  = ta.rsi(df["close"], length=14)
            if ema200 is None or rsi14 is None or pd.isna(ema200.iloc[-1]) or pd.isna(rsi14.iloc[-1]):
                continue

            ema200_v = float(ema200.iloc[-1])
            rsi_v    = float(rsi14.iloc[-1])
            cl       = closes[-1]
            hi       = highs[-1]
            lo       = lows[-1]
            hi2      = highs[-2]
            lo2      = lows[-2]
            hi3      = highs[-3]
            lo3      = lows[-3]

            # Verifica padrão 3LB: C1 (minima mais baixa), C2 > C1, C3 > C2
            c1_low   = lo3
            c2_high  = hi2
            c3_high  = hi

            # C1 é a mínima mais baixa dos últimos 5 candles?
            recent_lows = lows[-6:] if len(lows) >= 6 else lows
            c1_is_lowest = c1_low <= min(recent_lows)

            # C2 > C1 e C3 > C2?
            pattern_ok = c2_high > max(hi3, lo3 * 1.001) and c3_high > c2_high and c1_is_lowest

            # RSI < 30?
            rsi_oversold = rsi_v < 30

            # Preço acima ou abaixo da EMA200?
            in_uptrend = cl > ema200_v

            raw[symbol] = {
                "pattern_ok": int(pattern_ok),
                "rsi_oversold": int(rsi_oversold),
                "in_uptrend": int(in_uptrend),
                "rsi": rsi_v,
                "c3_high": c3_high,
                "c1_low": c1_low,
            }

        syms = list(raw.keys())
        if syms:
            for symbol in syms:
                r = raw[symbol]
                if r["pattern_ok"] and r["rsi_oversold"]:
                    score = 0.9 if not r["in_uptrend"] else 0.6
                elif r["pattern_ok"]:
                    score = 0.7 if not r["in_uptrend"] else 0.5
                else:
                    # Pontua parcialmente se C1-C2 se formaram
                    score = 0.2 if r["c3_high"] > 0 else 0.0

                results.append({
                    "symbol": symbol,
                    "score":  round(score, 4),
                    "details": {
                        "metric": "three_line_bar",
                        "pattern_ok": bool(r["pattern_ok"]),
                        "rsi_oversold": bool(r["rsi_oversold"]),
                        "in_uptrend": bool(r["in_uptrend"]),
                        "rsi": round(r["rsi"], 1),
                    },
                })

        for symbol in RANK_ASSETS:
            if symbol not in {r["symbol"] for r in results}:
                results.append({
                    "symbol": symbol, "score": 0.0,
                    "details": {"metric": "three_line_bar", "error": "dados insuficientes"},
                })

    # ── Stochastic Reversal: scoring por alinhamento recente e zona de pullback ─
    elif strategy_id == "stochastic_reversal":
        raw: dict[str, dict] = {}
        for symbol, rows in ohlcv.items():
            if len(rows) < 150:
                continue
            closes = [float(r[4]) for r in rows]
            highs  = [float(r[2]) for r in rows]
            lows   = [float(r[3]) for r in rows]
            df = pd.DataFrame({"close": closes, "high": highs, "low": lows})

            ema21 = ta.ema(df["close"], length=21)
            ema34 = ta.ema(df["close"], length=34)
            ema144 = ta.ema(df["close"], length=144)
            stoch = ta.stoch(df["high"], df["low"], df["close"], k=7, d=3, smooth_k=3)

            if ema144 is None or stoch is None or ema144.isna().iloc[-1]:
                continue
                
            k_col = [c for c in stoch.columns if 'STOCHk' in c][0]
            stoch_k = float(stoch[k_col].iloc[-1])

            cl = closes[-1]
            e21 = float(ema21.iloc[-1])
            e34 = float(ema34.iloc[-1])
            e144 = float(ema144.iloc[-1])
            
            aligned_long = (e21 > e34 > e144) and (cl > e144)
            aligned_short = (e21 < e34 < e144) and (cl < e144)
            
            is_aligned = aligned_long or aligned_short
            
            # Quão perto está da zona de reversão/pullback
            stoch_score = 0.0
            if aligned_long and stoch_k < 30:
                stoch_score = 1.0 - (stoch_k / 30.0) # Mais perto de 0 = maior score
            elif aligned_short and stoch_k > 70:
                stoch_score = (stoch_k - 70.0) / 30.0 # Mais perto de 100 = maior score
                
            # Proximidade à EMA 144 (queremos que esteja o mais perto possível para risco baixo)
            dist_ema144 = abs(cl - e144) / e144
            prox_score = max(0.0, 1.0 - (dist_ema144 / 0.05)) # Decai em 5%
            
            raw[symbol] = {
                "aligned": 1.0 if is_aligned else 0.0,
                "stoch_score": stoch_score,
                "prox_score": prox_score
            }

        syms = list(raw.keys())
        if syms:
            for symbol in syms:
                r = raw[symbol]
                score = (r["aligned"] * 0.4) + (r["stoch_score"] * 0.4) + (r["prox_score"] * 0.2)
                results.append({
                    "symbol": symbol,
                    "score":  round(score, 4),
                    "details": {
                        "metric": "stochastic_pullback",
                        "aligned": r["aligned"] > 0,
                        "stoch_pullback": round(r["stoch_score"], 2)
                    },
                })

        for symbol in RANK_ASSETS:
            if symbol not in {r["symbol"] for r in results}:
                results.append({
                    "symbol": symbol, "score": 0.0,
                    "details": {"metric": "stochastic_pullback", "error": "dados insuficientes"},
                })

    # ── Demais estratégias: scoring por tendência, ATR e momentum ────────────
    else:
        raw: dict[str, dict] = {}
        for symbol, rows in ohlcv.items():
            if len(rows) < 30:
                continue
            closes = [float(r[4]) for r in rows]
            highs  = [float(r[2]) for r in rows]
            lows   = [float(r[3]) for r in rows]
            df = pd.DataFrame({"close": closes, "high": highs, "low": lows})

            atr_s = ta.atr(df["high"], df["low"], df["close"], length=14)
            ema21 = ta.ema(df["close"], length=21)
            ema55 = ta.ema(df["close"], length=55)
            if atr_s is None or ema21 is None or ema55 is None:
                continue

            cl  = closes[-1]
            e21 = float(ema21.iloc[-1])
            e55 = float(ema55.iloc[-1])
            raw[symbol] = {
                "atr_pct":        float(atr_s.iloc[-1]) / cl,
                "trend_strength": abs(e21 - e55) / e55,
                "momentum":       abs(cl / closes[-20] - 1) if len(closes) >= 20 else 0.0,
            }

        def _norm(vals: list[float]) -> list[float]:
            mn, mx = min(vals), max(vals)
            return [(v - mn) / (mx - mn) if mx > mn else 0.5 for v in vals]

        syms = list(raw.keys())
        if syms:
            n_atr   = _norm([raw[s]["atr_pct"]        for s in syms])
            n_trend = _norm([raw[s]["trend_strength"]  for s in syms])
            n_mom   = _norm([raw[s]["momentum"]        for s in syms])
            for i, symbol in enumerate(syms):
                score = 0.40 * n_atr[i] + 0.40 * n_trend[i] + 0.20 * n_mom[i]
                r = raw[symbol]
                results.append({
                    "symbol": symbol,
                    "score":  round(score, 4),
                    "details": {
                        "metric":             "trend_momentum",
                        "atr_pct":            round(r["atr_pct"] * 100, 2),
                        "trend_strength_pct": round(r["trend_strength"] * 100, 2),
                        "momentum_pct":       round(r["momentum"] * 100, 2),
                    },
                })

        for symbol in RANK_ASSETS:
            if symbol not in {r["symbol"] for r in results}:
                results.append({
                    "symbol": symbol, "score": 0.0,
                    "details": {"metric": "trend_momentum", "error": "dados insuficientes"},
                })

    # ── Ranking final ─────────────────────────────────────────────────────────
    results.sort(key=lambda x: x["score"], reverse=True)
    rank_labels = {1: "Excelente", 2: "Bom", 3: "Moderado", 4: "Fraco", 5: "Fraco"}
    for i, r in enumerate(results):
        r["rank"]  = i + 1
        r["label"] = rank_labels.get(i + 1, "Fraco")

    return {
        "strategy_id": strategy_id,
        "timeframe":   timeframe,
        "assets":      results,
        "graph_info":  graph_info,
        "warning":     warning,
        "note": {
            "graph_regime":  "Ranking por centralidade de eigenvector no grafo de correlação.",
            "pivot_sniper":  "Ranking por inclinação EMA50, proximidade ao pullback e riqueza de pivôs.",
            "whale_flow_regime": "Ranking por volatilidade ATR, força de tendência e agressão de volume.",
        }.get(strategy_id, "Ranking por volatilidade ATR, força de tendência e momentum recente."),
            "three_line_bar": "Ranking por formação do padrão 3LB (C1 < C2 < C3), RSI sobrevenda e contexto de tendência.",
            "S005": "Ranking por formação do padrão 3LB (C1 < C2 < C3), RSI sobrevenda e contexto de tendência.",
    }


@app.get("/api/market/candles")
async def market_candles(symbol: str = "BTC-USDT",
                         timeframe: str = "15m",
                         limit: int = 200):
    import aiohttp as _aiohttp
    bar = map_timeframe_for_history(timeframe)
    async with _aiohttp.ClientSession() as s:
        ex = build_exchange(s)
        try:
            candles = await ex.fetch_candles(symbol, bar, limit=limit)
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc
        # Mantém compatibilidade com o formato de resposta anterior.
        data = [
            [
                str(c.epoch),
                str(c.open),
                str(c.high),
                str(c.low),
                str(c.close),
                str(c.volume),
                "0",
                "0",
                "1",
            ]
            for c in reversed(candles)
        ]
        return {"code": "0", "msg": "", "data": data}


@app.get("/api/market/ticker")
async def market_ticker(symbol: str = "BTC-USDT"):
    import aiohttp as _aiohttp
    async with _aiohttp.ClientSession() as s:
        ex = build_exchange(s)
        try:
            ticker = await ex.get_ticker(symbol)
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"code": "0", "msg": "", "data": [ticker]}


# ── WebSocket: tempo real ──────────────────────────────────────────────────────

@app.websocket("/ws")
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


@app.get("/{full_path:path}", include_in_schema=False)
def serve_spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(404, "Not found")
    if STATIC_INDEX.exists():
        return FileResponse(STATIC_INDEX)
    raise HTTPException(404, "Frontend build not found")
