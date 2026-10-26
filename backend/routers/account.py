"""
Rotas de conta OKX: credenciais, reset, saldo, snapshot e rejeições de ordem.
"""

from __future__ import annotations
from fastapi import APIRouter
import logging

import asyncio
import os
from typing import Optional

from pydantic import BaseModel
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..database import AiAnalysisLogModel, BotModel, BotSnapshotModel, OrderRejectionModel, SettingsModel, SignalLogModel, TradeModel, TradeReportModel, get_db
from ..exchanges.factory import build_exchange, get_default_demo_mode, get_exchange_provider
from ..bot_manager import manager
from ..common import _norm_symbol

router = APIRouter()
log = logging.getLogger("main")


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

@router.post("/api/account/reset-data")
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

@router.post("/api/account/reset")
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
    from ..crypto_utils import encrypt
    from ..database import SettingsModel

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

@router.get("/api/account/connection")
async def account_connection(db: Session = Depends(get_db)):
    """Status oficial da conexão OKX configurada via banco."""
    return await okx_status(db)

@router.post("/api/account/connect")
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

@router.post("/api/account/disconnect")
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

@router.get("/api/settings/okx-status")
async def okx_status(db: Session = Depends(get_db)):
    """Retorna se as credenciais OKX estão configuradas e o saldo da conta."""
    from ..database import SettingsModel
    from ..crypto_utils import safe_decrypt

    key_row = db.query(SettingsModel).filter_by(key="okx_api_key").first()
    configured = bool(key_row and safe_decrypt(key_row.value or ""))
    demo = os.getenv("OKX_DEMO", "true").lower() in ("1", "true", "yes")

    result: dict = {"configured": configured, "demo": demo, "equity": None, "currency": "USDT"}
    if not configured:
        return result

    try:
        from ..exchanges.okx import OKXExchange
        ex = OKXExchange()
        summary = await ex.get_account_summary()
        result["equity"] = round(float(summary.get("equity") or 0), 2)
    except Exception:
        pass
    return result

@router.get("/api/order-rejections")
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

@router.patch("/api/order-rejections/{rejection_id}")
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

@router.post("/api/settings/okx-credentials")
async def set_okx_credentials(body: OkxCredentialsRequest, db: Session = Depends(get_db)):
    """
    Valida e armazena as credenciais OKX criptografadas no banco.
    Substitui as credenciais anteriores sem afetar bots nem trades.
    """
    import datetime as _dt
    import base64 as _b64, hashlib as _sha, hmac as _hmac
    import aiohttp as _aio
    from ..crypto_utils import encrypt
    from ..database import SettingsModel

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

@router.post("/api/account/cancel-algos")
async def cancel_pending_algos():
    """Cancela APENAS as ordens algo pendentes (SL/TS) sem fechar posições."""
    import aiohttp
    async with aiohttp.ClientSession() as session:
        ex = build_exchange(session)
        cancelled = await ex.cancel_all_algos()
    return {"ok": True, "cancelled": cancelled}

@router.get("/api/account/balance")
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

@router.get("/api/account/snapshot")
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
            active_bot_status = []
            for bot in db.query(BotModel).all():
                status = manager.get_status(bot.id)
                if status and int(status.get("direction", 0) or 0) != 0:
                    active_symbols.append(bot.symbol)
                    active_bot_status.append((bot.symbol, status))
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

            # A OKX não expõe P&L não-realizado para saldos SPOT (app SPOT-only, sem
            # margem/derivativos) — get_account_summary() sempre retorna 0 para isso.
            # Calcula o não-realizado real a partir do próprio estado do bot (mesma
            # fórmula do P&L realizado, com o preço atual no lugar do preço de saída).
            computed_unreal = 0.0
            for symbol, status in active_bot_status:
                entry_price = float(status.get("entry_price") or 0.0)
                last_price  = float(status.get("last_price") or 0.0)
                size        = float(status.get("size") or 0.0)
                direction   = int(status.get("direction") or 0)
                if entry_price > 0 and last_price > 0 and size > 0 and direction != 0:
                    ct_size = ex.get_contract_size(symbol)
                    computed_unreal += size * ct_size * (last_price - entry_price) * direction
            unreal_pl = computed_unreal

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
