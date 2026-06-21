"""OKX exchange adapter — implementa BaseExchange usando a REST API v5 da OKX."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import os
from urllib.parse import urlencode
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import aiohttp

from .base import BaseExchange, CandleBar, Position

OKX_BASE = "https://www.okx.com"

# ── Cache de informações de instrumento (público, sem auth) ───────────────────
# Evita chamar /public/instruments a cada ordem. Populado em warmup_instrument().
_INST_CACHE: dict[str, dict] = {}


def _inst_type(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith("-SWAP"):     return "SWAP"
    if s.endswith("-FUTURES"):  return "FUTURES"
    return "SPOT"


def _td_mode(symbol: str) -> str:
    """Modo de trading correto para cada tipo de instrumento OKX."""
    t = _inst_type(symbol)
    return "isolated" if t in ("SWAP", "FUTURES") else "cash"


async def _fetch_inst_info(symbol: str) -> dict:
    """Busca ctVal, lotSz e minSz do instrumento na API pública OKX (sem auth).
    Retorna defaults seguros se a chamada falhar."""
    if symbol in _INST_CACHE:
        return _INST_CACHE[symbol]
    inst_t = _inst_type(symbol)
    try:
        url = (f"{OKX_BASE}/api/v5/public/instruments"
               f"?instType={inst_t}&instId={symbol}")
        async with aiohttp.ClientSession() as s:
            async with s.get(url) as resp:
                data = await resp.json()
        items = data.get("data", [])
        if items:
            info = {
                "instType": inst_t,
                "ctVal":    float(items[0].get("ctVal")  or 1.0),
                "lotSz":    float(items[0].get("lotSz")  or 1.0),
                "minSz":    float(items[0].get("minSz")  or 1.0),
            }
            _INST_CACHE[symbol] = info
            return info
    except Exception:
        pass
    default = {"instType": inst_t, "ctVal": 1.0, "lotSz": 1.0, "minSz": 0.0}
    _INST_CACHE[symbol] = default
    return default

# ── Credenciais ───────────────────────────────────────────────────────────────

def _get_credentials() -> tuple[str, str, str, bool]:
    """Retorna credenciais OKX exclusivamente do banco settings."""
    from ..database import SessionLocal, SettingsModel
    from ..crypto_utils import safe_decrypt

    db = SessionLocal()
    try:
        key_row  = db.query(SettingsModel).filter_by(key="okx_api_key").first()
        sec_row  = db.query(SettingsModel).filter_by(key="okx_api_secret").first()
        phr_row  = db.query(SettingsModel).filter_by(key="okx_passphrase").first()
        k = safe_decrypt(key_row.value) if key_row else ""
        s = safe_decrypt(sec_row.value) if sec_row else ""
        p = safe_decrypt(phr_row.value) if phr_row else ""
        if not (k and s and p):
            raise RuntimeError("Credenciais OKX não configuradas no banco. Use Conectar OKX.")
        demo = os.getenv("OKX_DEMO", "true").lower() in ("1", "true", "yes")
        return k, s, p, demo
    finally:
        db.close()


# ── Assinatura HMAC-SHA256 ────────────────────────────────────────────────────

def _sign(secret: str, timestamp: str, method: str, path: str, body: str = "") -> str:
    message = timestamp + method.upper() + path + body
    mac = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("utf-8")


def _auth_headers(method: str, path: str, body: str = "") -> dict:
    key, secret, passphrase, demo = _get_credentials()
    now = datetime.now(timezone.utc)
    ts  = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
    headers = {
        "OK-ACCESS-KEY":        key,
        "OK-ACCESS-SIGN":       _sign(secret, ts, method, path, body),
        "OK-ACCESS-TIMESTAMP":  ts,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type":         "application/json",
    }
    if demo:
        headers["x-simulated-trading"] = "1"
    return headers


# ── Helpers ───────────────────────────────────────────────────────────────────

_TF_MAP = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H", "12h": "12H",
    "1D": "1D", "1W": "1W",
}

# Mapa inverso: canal OKX → timeframe interno
_TF_MAP_INV = {v: k for k, v in _TF_MAP.items()}


def _tf_to_okx(tf: str) -> str:
    return _TF_MAP.get(tf, "15m")


def _parse_candle(row: list) -> CandleBar:
    # OKX: [ts_ms, open, high, low, close, vol, volCcy, volCcyQuote, confirm]
    return CandleBar(
        epoch=int(row[0]),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
    )


def _check(data: dict, op: str = "") -> dict:
    if str(data.get("code", "0")) != "0":
        raise RuntimeError(f"OKX {op} erro {data.get('code')}: {data.get('msg', '?')}")
    return data


def _iso_to_ms(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return str(int(dt.timestamp() * 1000))
    except Exception:
        return ""


def _ms_to_iso(ms_val) -> str:
    try:
        dt = datetime.fromtimestamp(int(ms_val) / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except Exception:
        return ""


# ── OKXExchange ───────────────────────────────────────────────────────────────

class OKXExchange(BaseExchange):

    def __init__(self):
        self.last_order_error: Optional[dict] = None
        self._order_symbols: dict[str, str] = {}

    async def warmup_instrument(self, symbol: str) -> None:
        """Pré-aquece o cache de instrumento para que num_contracts() seja preciso desde o início."""
        await _fetch_inst_info(symbol)

    async def _request(self, method: str, path: str, *, body: dict | list | None = None) -> dict:
        payload = json.dumps(body) if body is not None else ""
        headers = _auth_headers(method, path, payload)
        async with aiohttp.ClientSession() as s:
            async with s.request(method, f"{OKX_BASE}{path}", headers=headers, data=payload or None) as resp:
                return await resp.json()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        query = urlencode(params or {})
        full_path = f"{path}?{query}" if query else path
        data = await self._request("GET", full_path)
        return _check(data, path)

    # ── Mercado ───────────────────────────────────────────────────────────────

    async def fetch_candles(self, symbol: str, timeframe: str, limit: int = 100) -> list[CandleBar]:
        bar = _tf_to_okx(timeframe)
        all_rows = []
        after = ""
        while len(all_rows) < limit:
            chunk_size = min(limit - len(all_rows), 300)
            path = f"/api/v5/market/candles?instId={symbol}&bar={bar}&limit={chunk_size}"
            if after:
                path += f"&after={after}"
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{OKX_BASE}{path}") as resp:
                    data = await resp.json()
            _check(data, "fetch_candles")
            rows = data.get("data", [])
            if not rows:
                break
            all_rows.extend(rows)
            after = rows[-1][0] # ts of the oldest candle in chunk
            if len(rows) < chunk_size:
                break # no more data available
        # OKX retorna do mais recente para o mais antigo — invertemos para ordem cronológica
        return [_parse_candle(r) for r in reversed(all_rows)]

    async def get_ticker(self, symbol: str) -> dict:
        path = f"/api/v5/market/ticker?instId={symbol}"
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{OKX_BASE}{path}") as resp:
                data = await resp.json()
        _check(data, "get_ticker")
        items = data.get("data", [])
        if not items:
            return {"last": None, "bid": None, "ask": None, "volume": None}
        t = items[0]
        return {
            "last":   float(t["last"])   if t.get("last")   else None,
            "bid":    float(t["bidPx"])  if t.get("bidPx")  else None,
            "ask":    float(t["askPx"])  if t.get("askPx")  else None,
            "volume": float(t["vol24h"]) if t.get("vol24h") else None,
        }

    # ── Conta ─────────────────────────────────────────────────────────────────

    async def get_balance(self, currency: str = "USDT") -> float:
        path = f"/api/v5/account/balance?ccy={currency}"
        headers = _auth_headers("GET", path)
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{OKX_BASE}{path}", headers=headers) as resp:
                data = await resp.json()
        _check(data, "get_balance")
        for item in data.get("data", []):
            for detail in item.get("details", []):
                if detail.get("ccy") == currency:
                    return float(detail.get("availEq") or detail.get("availBal") or 0)
        return 0.0

    async def get_all_balances(self, demo_override=None) -> list[dict]:
        path = "/api/v5/account/balance"
        headers = _auth_headers("GET", path)
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{OKX_BASE}{path}", headers=headers) as resp:
                data = await resp.json()
        _check(data, "get_all_balances")
        result = []
        for item in data.get("data", []):
            total_eq = float(item.get("totalEq") or 0)
            for detail in item.get("details", []):
                total = float(detail.get("eq") or detail.get("cashBal") or 0)
                if total > 0:
                    result.append({
                        "ccy":       detail.get("ccy"),
                        "available": float(detail.get("availEq") or detail.get("availBal") or 0),
                        "total":     total,
                        "total_usd": total_eq,
                    })
        return result

    async def get_position(self, symbol: str) -> Optional[Position]:
        # 1. Tenta posições alavancadas (margin/swap)
        path = f"/api/v5/account/positions?instId={symbol}"
        headers = _auth_headers("GET", path)
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{OKX_BASE}{path}", headers=headers) as resp:
                data = await resp.json()
        if str(data.get("code", "0")) == "0":
            for pos in data.get("data", []):
                size = float(pos.get("pos") or 0)
                if abs(size) < 1e-9:
                    continue
                return Position(
                    symbol=symbol,
                    side="long" if size > 0 else "short",
                    size=abs(size),
                    avg_price=float(pos.get("avgPx") or 0),
                    unrealized_pnl=float(pos.get("upl") or 0),
                    unrealized_plpc=float(pos.get("uplRatio") or 0),
                    cost_basis=float(pos.get("notionalUsd") or 0),
                )

        # 2. Para spot: verifica saldo da moeda base (SWAP e FUTURES não têm saldo spot)
        _sym_upper = symbol.upper()
        _is_deriv  = _sym_upper.endswith("-SWAP") or _sym_upper.endswith("-FUTURES")
        if "-" in symbol and not _is_deriv:
            base = symbol.split("-")[0]
            path2 = f"/api/v5/account/balance?ccy={base}"
            headers2 = _auth_headers("GET", path2)
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{OKX_BASE}{path2}", headers=headers2) as resp:
                    data2 = await resp.json()
            if str(data2.get("code", "0")) == "0":
                for item in data2.get("data", []):
                    for detail in item.get("details", []):
                        if detail.get("ccy") == base:
                            total = float(detail.get("eq") or detail.get("cashBal") or 0)
                            if total > 1e-9:
                                ticker = await self.get_ticker(symbol)
                                price  = float(ticker.get("last") or 0)
                                if price <= 0 or total * price < 0.01:
                                    return None
                                return Position(
                                    symbol=symbol,
                                    side="long",
                                    size=total,
                                    avg_price=price,
                                    unrealized_pnl=0.0,
                                )
        return None

    async def get_all_positions(self) -> list[dict]:
        path = "/api/v5/account/positions"
        headers = _auth_headers("GET", path)
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{OKX_BASE}{path}", headers=headers) as resp:
                data = await resp.json()
        if str(data.get("code", "0")) != "0":
            return []
        return [p for p in data.get("data", []) if abs(float(p.get("pos") or 0)) > 1e-9]

    async def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        inst_t = _inst_type(symbol) if symbol else "ANY"
        path = f"/api/v5/trade/orders-pending?instType={inst_t}"
        if symbol:
            path += f"&instId={symbol}"
        headers = _auth_headers("GET", path)
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{OKX_BASE}{path}", headers=headers) as resp:
                data = await resp.json()
        if str(data.get("code", "0")) != "0":
            return []
        return data.get("data", [])

    # ── Compatibilidade spot-only ─────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        if _inst_type(symbol) == "SPOT":
            return
        body = json.dumps({
            "instId": symbol,
            "mgnMode": "isolated",
            "lever": str(leverage),
        })
        path = "/api/v5/account/set-leverage"
        headers = _auth_headers("POST", path, body)
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{OKX_BASE}{path}", headers=headers, data=body) as resp:
                data = await resp.json()
        if str(data.get("code", "0")) != "0":
            # OKX Code 51006 means leverage is already set to the same level.
            if str(data.get("code", "0")) != "51006":
                _check(data, "set_leverage")

    # ── Ordens ────────────────────────────────────────────────────────────────

    async def _guard_spot_sell_close(self, symbol: str, side: str, size: float, *, close_intent: bool) -> bool:
        """Bloqueia venda SPOT que não seja fechamento de saldo já comprado."""
        if _inst_type(symbol) != "SPOT":
            return True
        side_norm = (side or "").lower()
        if side_norm != "sell":
            return True
        if not close_intent:
            self.last_order_error = {
                "kind": "spot_sell_blocked",
                "message": "Mercado SPOT permite venda somente para fechar saldo comprado existente.",
            }
            return False
        pos = await self.get_position(symbol)
        available = abs(float(pos.size or 0.0)) if pos and pos.side == "long" else 0.0
        requested = abs(float(size or 0.0))
        if requested <= 0 or available <= 0:
            self.last_order_error = {
                "kind": "spot_sell_without_balance",
                "message": "Venda SPOT bloqueada: não há saldo comprado para fechar.",
                "available": available,
                "requested": requested,
            }
            return False
        if requested > available * 1.001:
            self.last_order_error = {
                "kind": "spot_sell_size_exceeds_balance",
                "message": "Venda SPOT bloqueada: quantidade maior que o saldo comprado disponível.",
                "available": available,
                "requested": requested,
            }
            return False
        return True

    async def market_order(self, symbol: str, side: str, size: float, reduce_only: bool = False) -> Optional[str]:
        self.last_order_error = None
        if _inst_type(symbol) == "SPOT":
            if not await self._guard_spot_sell_close(symbol, side, size, close_intent=reduce_only):
                return None
        body_dict = {
            "instId":  symbol,
            "tdMode":  _td_mode(symbol),
            "side":    side,
            "ordType": "market",
            "sz":      str(size),
        }
        if _inst_type(symbol) == "SPOT":
            # Mantém contrato do app consistente: sz sempre é quantidade da moeda base.
            # Sem tgtCcy, market buy spot pode ser interpretado pela OKX como valor em USDT.
            body_dict["tgtCcy"] = "base_ccy"
        body = json.dumps(body_dict)
        path = "/api/v5/trade/order"
        headers = _auth_headers("POST", path, body)
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{OKX_BASE}{path}", headers=headers, data=body) as resp:
                data = await resp.json()
        if str(data.get("code", "0")) != "0":
            items = data.get("data", [{}])
            msg = (items[0].get("sMsg") if items else None) or data.get("msg", "unknown")
            self.last_order_error = {"kind": f"okx_{data.get('code')}", "message": f"OKX: {msg}", "raw": data}
            return None
        items = data.get("data", [])
        ord_id = items[0].get("ordId") if items else None
        if ord_id:
            self._order_symbols[ord_id] = symbol
        return ord_id

    async def place_stop_loss(self, symbol: str, side: str, size: float, trigger_price: float) -> Optional[str]:
        self.last_order_error = None
        if _inst_type(symbol) == "SPOT":
            if (side or "").lower() != "sell":
                self.last_order_error = {
                    "kind": "spot_stop_side_blocked",
                    "message": "Stop Loss SPOT só pode vender saldo comprado existente.",
                }
                return None
            if not await self._guard_spot_sell_close(symbol, side, size, close_intent=True):
                return None
        body = json.dumps({
            "instId":          symbol,
            "tdMode":          _td_mode(symbol),
            "side":            side,
            "ordType":         "conditional",
            "sz":              str(size),
            "slTriggerPx":     str(round(trigger_price, 8)),
            "slOrdPx":         "-1",
            "slTriggerPxType": "last",
        })
        path = "/api/v5/trade/order-algo"
        headers = _auth_headers("POST", path, body)
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{OKX_BASE}{path}", headers=headers, data=body) as resp:
                data = await resp.json()
        if str(data.get("code", "0")) != "0":
            items = data.get("data", [{}])
            msg = (items[0].get("sMsg") if items else None) or data.get("msg", "unknown")
            self.last_order_error = {"kind": f"okx_{data.get('code')}", "message": f"OKX SL: {msg}", "raw": data}
            return None
        items = data.get("data", [])
        return items[0].get("algoId") if items else None

    async def place_trailing_stop(self, symbol: str, side: str, size: float, callback_ratio: float, activation_price: float = 0.0) -> Optional[str]:
        self.last_order_error = None
        if _inst_type(symbol) == "SPOT":
            if (side or "").lower() != "sell":
                self.last_order_error = {
                    "kind": "spot_trailing_side_blocked",
                    "message": "Trailing stop SPOT só pode vender saldo comprado existente.",
                }
                return None
            if not await self._guard_spot_sell_close(symbol, side, size, close_intent=True):
                return None
        body_dict: dict = {
            "instId":        symbol,
            "tdMode":        _td_mode(symbol),
            "side":          side,
            # OKX API v5 native trailing stop: order-algo + move_order_stop.
            "ordType":       "move_order_stop",
            "sz":            str(size),
            "callbackRatio": str(round(callback_ratio, 4)),
        }
        if activation_price > 0:
            body_dict["activePx"] = str(round(activation_price, 8))
        body = json.dumps(body_dict)
        path = "/api/v5/trade/order-algo"
        headers = _auth_headers("POST", path, body)
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{OKX_BASE}{path}", headers=headers, data=body) as resp:
                data = await resp.json()
        if str(data.get("code", "0")) != "0":
            items = data.get("data", [{}])
            msg = (items[0].get("sMsg") if items else None) or data.get("msg", "unknown")
            self.last_order_error = {"kind": f"okx_{data.get('code')}", "message": f"OKX TS: {msg}", "raw": data}
            return None
        items = data.get("data", [])
        return items[0].get("algoId") if items else None

    async def get_algo_order(self, symbol: str, algo_id: str) -> Optional[dict]:
        params = {
            "instType": _inst_type(symbol),
            "instId": symbol,
            "algoId": algo_id,
            "ordType": "conditional,move_order_stop",
        }
        try:
            data = await self._get("/api/v5/trade/orders-algo-pending", params)
            for item in data.get("data", []):
                if item.get("algoId") == algo_id:
                    return item
        except Exception:
            return None
        return None

    async def cancel_algo(self, symbol: str, algo_id: str) -> bool:
        """Cancela ordem algorítmica na exchange. Retorna True se sucesso, False se falha."""
        try:
            body = json.dumps([{"algoId": algo_id, "instId": symbol}])
            path = "/api/v5/trade/cancel-algos"
            headers = _auth_headers("POST", path, body)
            async with aiohttp.ClientSession() as s:
                async with s.post(f"{OKX_BASE}{path}", headers=headers, data=body) as resp:
                    response_data = await resp.json()
                    
                    # Valida resposta OKX: code "0" = sucesso
                    code = str(response_data.get("code", "1"))
                    if code == "0":
                        log.info("[OKX] Algo %s cancelado com sucesso em %s", algo_id, symbol)
                        return True
                    else:
                        error_msg = response_data.get("msg", f"Erro desconhecido (code: {code})")
                        log.warning("[OKX] Falha ao cancelar algo %s em %s: %s", algo_id, symbol, error_msg)
                        self.last_order_error = {"code": code, "message": error_msg}
                        return False
        except Exception as e:
            log.error("[OKX] Exceção ao cancelar algo %s em %s: %s", algo_id, symbol, e)
            self.last_order_error = {"code": "500", "message": str(e)}
            return False

    async def cancel_all_algos(self) -> int:
        path = "/api/v5/trade/orders-algo-pending?ordType=conditional,move_order_stop&instType=ANY"
        headers = _auth_headers("GET", path)
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{OKX_BASE}{path}", headers=headers) as resp:
                data = await resp.json()
        algos = data.get("data", []) if str(data.get("code", "0")) == "0" else []
        if not algos:
            return 0
        cancel_body = json.dumps([{"algoId": a["algoId"], "instId": a["instId"]} for a in algos])
        path2 = "/api/v5/trade/cancel-algos"
        headers2 = _auth_headers("POST", path2, cancel_body)
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{OKX_BASE}{path2}", headers=headers2, data=cancel_body) as resp:
                await resp.read()
        return len(algos)

    async def close_all_positions(self) -> int:
        # 1. Cancela TODAS as ordens algo pendentes (trailing stops, SL, TP)
        cancelled_algos = await self.cancel_all_algos()
        if cancelled_algos:
            log.info("OKX: %d ordens algo pendentes canceladas.", cancelled_algos)

        # 2. Fecha todas as posições abertas
        positions = await self.get_all_positions()
        count = 0
        for pos in positions:
            inst_id = pos.get("instId", "")
            if not inst_id:
                continue
            if await self.liquidate_position(inst_id):
                count += 1
        return count

    async def get_order(self, order_id: str) -> Optional[dict]:
        symbol = self._order_symbols.get(order_id)
        if not symbol:
            return None
        path = f"/api/v5/trade/order?instId={symbol}&ordId={order_id}"
        headers = _auth_headers("GET", path)
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{OKX_BASE}{path}", headers=headers) as resp:
                data = await resp.json()
        if str(data.get("code", "0")) != "0":
            return None
        items = data.get("data", [])
        if not items:
            return None
        order = items[0]
        return {
            **order,
            "status": order.get("state"),
            "filled_avg_price": order.get("avgPx") or order.get("fillPx"),
            "filled_size": order.get("accFillSz") or order.get("fillSz"),
        }

    async def liquidate_position(self, symbol: str) -> Optional[str]:
        self.last_order_error = None
        pos = await self.get_position(symbol)
        if pos is None:
            return None
        side = "sell" if pos.side == "long" else "buy"
        body_dict = {
            "instId":  symbol,
            "tdMode":  "cash",
            "side":    side,
            "ordType": "market",
            "sz":      str(pos.size),
        }
        if _inst_type(symbol) == "SPOT":
            body_dict["tgtCcy"] = "base_ccy"
        body = json.dumps(body_dict)
        path = "/api/v5/trade/order"
        headers = _auth_headers("POST", path, body)
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{OKX_BASE}{path}", headers=headers, data=body) as resp:
                data = await resp.json()
        if str(data.get("code", "0")) != "0":
            self.last_order_error = {"kind": "okx_liquidate", "message": f"OKX: {data.get('msg', '?')}", "raw": data}
            return None
        items = data.get("data", [])
        ord_id = items[0].get("ordId") if items else None
        if ord_id:
            self._order_symbols[ord_id] = symbol
        return ord_id

    async def get_clock(self) -> dict:
        # Cripto opera 24/7
        return {"is_open": True, "next_open": None, "next_close": None}

    # ── Atividades / relatório ────────────────────────────────────────────────

    async def get_activities(
        self,
        date: str | None = None,
        after: str | None = None,
        until: str | None = None,
        activity_type: str = "FILL",
    ) -> list[dict]:
        """Retorna fills históricos da OKX (GET /api/v5/trade/fills)."""
        path = "/api/v5/trade/fills?instType=ANY"
        if after:
            ms = _iso_to_ms(after)
            if ms:
                path += f"&begin={ms}"
        if until:
            ms = _iso_to_ms(until)
            if ms:
                path += f"&end={ms}"
        headers = _auth_headers("GET", path)
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{OKX_BASE}{path}", headers=headers) as resp:
                data = await resp.json()
        if str(data.get("code", "0")) != "0":
            return []
        result = []
        for f in data.get("data", []):
            result.append({
                "symbol":           f.get("instId", ""),
                "side":             f.get("side", ""),
                "price":            f.get("fillPx", ""),
                "qty":              f.get("fillSz", ""),
                "transaction_time": _ms_to_iso(f.get("ts", "")),
                "id":               f.get("tradeId", ""),
                "order_id":         f.get("ordId", ""),
                "fee":              f.get("fee", ""),
                "fee_currency":     f.get("feeCcy", ""),
            })
        return result

    async def get_account_summary(self) -> dict:
        path = "/api/v5/account/balance"
        headers = _auth_headers("GET", path)
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{OKX_BASE}{path}", headers=headers) as resp:
                data = await resp.json()
        _check(data, "get_account_summary")
        items = data.get("data", [])
        if not items:
            return {}
        total_eq = float(items[0].get("totalEq") or 0)
        return {
            "equity":        total_eq,
            "last_equity":   total_eq,
            "cash":          total_eq,
            "unrealized_pl": 0.0,
            "currency":      "USDT",
        }

    async def get_portfolio_history(self, period: str = "1D", timeframe: str = "1H") -> dict:
        return {}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def get_contract_size(self, symbol: str) -> float:
        """ctVal do instrumento: 1.0 para spot."""
        return _INST_CACHE.get(symbol, {}).get("ctVal", 1.0)

    def num_contracts(self, symbol: str, price: float, stake_usd: float, leverage: int) -> float:
        """Calcula o número de contratos (ou unidades spot) para a posição.
        Para spot: retorna quantidade em moeda base.
        """
        if price <= 0:
            return 0.0
        info   = _INST_CACHE.get(symbol, {})
        ct_val = info.get("ctVal", 1.0)   # unidades base por contrato
        lot_sz = info.get("lotSz", 1.0)   # múltiplo mínimo de contratos
        
        # A posição nocional operada é a margem (stake) vezes a alavancagem
        qty = (stake_usd * leverage) / (price * ct_val)
        
        # Arredonda para baixo ao múltiplo de lotSz (com precaução para imprecisão float)
        if lot_sz > 0:
            qty = math.floor(round(qty / lot_sz, 8)) * lot_sz
        return max(0.0, round(qty, 8))


# ── Streams ───────────────────────────────────────────────────────────────────

class OKXStream:
    """Polling-based public stream para OKX (candles via REST)."""

    _POLL_SECONDS = {
        "1m": 60, "5m": 60, "15m": 60,
        "1H": 120, "4H": 300, "1D": 300,
    }

    def __init__(self, channel: str, symbol: str, demo: bool = True):
        self._symbol = symbol
        self._bar    = channel
        self._poll   = self._POLL_SECONDS.get(channel, 60)
        # canal OKX → timeframe interno
        self._tf_internal = _TF_MAP_INV.get(channel, "15m")

    async def iter(self) -> AsyncIterator[tuple[CandleBar, bool]]:
        last_epoch: Optional[int] = None
        while True:
            try:
                ex = OKXExchange()
                candles = await ex.fetch_candles(self._symbol, self._tf_internal, limit=3)
                for candle in candles:
                    if last_epoch is None or candle.epoch > last_epoch:
                        last_epoch = candle.epoch
                        yield candle, True
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(self._poll)


class OKXPrivateStream:
    """WebSocket private stream para fills da OKX (channel: orders)."""

    def __init__(self, exchange: OKXExchange):
        _, _, _, demo = _get_credentials()
        self._ws_url = (
            "wss://wspap.okx.com:8443/ws/v5/private"
            if demo
            else "wss://ws.okx.com:8443/ws/v5/private"
        )

    def iter(self):
        ws_url = self._ws_url

        async def _aiter():
            import websockets as _ws
            import json as _js
            import inspect as _inspect

            key, secret, passphrase, _ = _get_credentials()
            ts   = str(int(datetime.now(timezone.utc).timestamp()))
            sign = _sign(secret, ts, "GET", "/users/self/verify")
            headers = {
                "User-Agent": "OKXStrategy/1.0",
                "Origin": "https://www.okx.com",
            }
            connect_kwargs = {
                "ping_interval": 20,
                "ping_timeout": 20,
                "open_timeout": 15,
            }
            params = _inspect.signature(_ws.connect).parameters
            if "additional_headers" in params:
                connect_kwargs["additional_headers"] = headers
            elif "extra_headers" in params:
                connect_kwargs["extra_headers"] = headers
            if "user_agent_header" in params:
                connect_kwargs["user_agent_header"] = headers["User-Agent"]
            if "origin" in params:
                connect_kwargs["origin"] = headers["Origin"]

            async with _ws.connect(ws_url, **connect_kwargs) as ws:
                await ws.send(_js.dumps({
                    "op": "login",
                    "args": [{"apiKey": key, "passphrase": passphrase, "timestamp": ts, "sign": sign}],
                }))
                login_raw = await ws.recv()
                login_msg = _js.loads(login_raw)
                if login_msg.get("event") != "login":
                    raise RuntimeError(f"OKX WS login falhou: {login_raw}")

                await ws.send(_js.dumps({
                    "op": "subscribe",
                    "args": [{"channel": "orders", "instType": "ANY"}],
                }))

                async for raw in ws:
                    try:
                        msg = _js.loads(raw)
                        if msg.get("event") in ("subscribe", "login", "error"):
                            continue
                        for order in msg.get("data", []):
                            state = order.get("state", "")
                            if state not in ("filled", "partially_filled"):
                                continue
                            yield {
                                "state":  "filled",
                                "instId": order.get("instId", ""),
                                "side":   order.get("side", ""),
                                "ordId":  order.get("ordId", ""),
                                "algoId": order.get("algoId"),
                                "source": order.get("source"),
                                "fillPx": order.get("fillPx") or order.get("avgPx"),
                                "fillSz": order.get("accFillSz") or order.get("fillSz"),
                                "avgPx":  order.get("avgPx"),
                                "sz":     order.get("accFillSz"),
                            }
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass

        return _aiter()
