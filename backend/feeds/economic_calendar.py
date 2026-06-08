"""
feeds/economic_calendar.py — Economic Calendar Feed.

Dois papéis simultâneos:
  1. Gate universal  — suspende entradas durante janelas de eventos High-impact
                       (protege especialmente Bollinger Breakout e Graph Regime)
  2. Surprise score  — alimenta o slot update_sentiment() do Graph Regime com
                       o desvio normalizado entre actual e consensus

Campos relevantes da API esperada:
  calId, date, region/country, event,
  actual, forecast/predict, previous, importance (3=High, 2=Medium, 1=Low)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import websockets

log = logging.getLogger("calendar_feed")

_REST_BASE  = os.getenv("CALENDAR_REST_BASE", "").strip()
_REST_PATH  = os.getenv("CALENDAR_REST_PATH", "").strip()
_WS_URL     = os.getenv("CALENDAR_WS_URL", "").strip()

# Valor padrão para high-impact; aceita também variantes em texto
_HIGH = {"3", "High", "high", "HIGH"}

# Janelas de protecção por estratégia (minutos antes, minutos depois)
_WINDOWS: dict[str, tuple[int, int]] = {
    "bollinger_breakout": (60, 45),  # mais agressivo — falsos breakouts são perigosos
    "graph_regime":       (60, 30),
    "_default":           (60, 30),
}


class EconomicCalendarFeed:
    """
    Singleton que agrega e expõe dados do calendário económico.

    Deve ser iniciado uma vez no startup da aplicação:
        await calendar_feed.start()
    """

    def __init__(self):
        self._events:           list[dict]        = []   # ordenados por _ts (UTC)
        self._latest_surprise:  Optional[dict]    = None # último evento divulgado
        self._started:          bool              = False
        self._ws_task:          Optional[asyncio.Task] = None
        self._poll_task:        Optional[asyncio.Task] = None

    # ── Ciclo de vida ─────────────────────────────────────────────────────────

    async def start(self):
        if self._started:
            return
        if not _REST_BASE or not _REST_PATH:
            log.info("EconomicCalendarFeed desativado: endpoints de calendário não configurados para o provider atual.")
            self._started = True
            return
        self._started = True
        await self._fetch_rest()
        if _WS_URL:
            self._ws_task = asyncio.create_task(self._ws_loop(), name="calendar_ws")
        self._poll_task = asyncio.create_task(self._poll_loop(), name="calendar_poll")
        log.info("EconomicCalendarFeed iniciado — %d eventos High-impact.", len(self._events))

    async def stop(self):
        for task in (self._ws_task, self._poll_task):
            if task:
                task.cancel()
        self._started = False

    # ── API pública ───────────────────────────────────────────────────────────

    def is_high_impact_window(
        self,
        strategy_id: str = "_default",
    ) -> tuple[bool, str]:
        """
        Retorna (True, event_name) se o momento actual está dentro da janela de
        protecção de algum evento High-impact.
        (False, "") caso contrário.
        """
        before_m, after_m = _WINDOWS.get(strategy_id, _WINDOWS["_default"])
        now = datetime.now(timezone.utc)

        for ev in self._events:
            if not self._is_high(ev):
                continue
            ts = ev.get("_ts")
            if ts is None:
                continue
            delta_m = (ts - now).total_seconds() / 60   # negativo = já ocorreu
            if -after_m <= delta_m <= before_m:
                return True, ev.get("event", "Evento macro High-impact")

        return False, ""

    def get_surprise_score(self) -> Optional[float]:
        """
        Score [-1, +1] do evento High-impact mais recente com divulgação real.

        Cálculo: tanh( (actual - forecast) / |forecast| × 3 )
          • Compressão suave para [-1, +1]
          • Magnitude > 0.6 activa o bloqueio no Graph Regime (threshold existente)

        Retorna None se: sem evento recente, parsing falhou, ou forecast = 0.
        """
        if not self._latest_surprise:
            return None

        actual   = _parse_float(self._latest_surprise.get("actual"))
        forecast = _parse_float(
            self._latest_surprise.get("forecast") or
            self._latest_surprise.get("predict")  or
            self._latest_surprise.get("consensus")
        )

        if actual is None or forecast is None or forecast == 0:
            return None

        raw   = (actual - forecast) / abs(forecast)
        score = round(math.tanh(raw * 3), 3)
        return score

    def get_upcoming_events(self, n: int = 5) -> list[dict]:
        """Próximos N eventos High-impact formatados para a API REST."""
        now = datetime.now(timezone.utc)
        out = []
        for ev in self._events:
            if not self._is_high(ev):
                continue
            ts = ev.get("_ts")
            if ts and ts >= now:
                out.append({
                    "event":      ev.get("event", ""),
                    "region":     ev.get("region") or ev.get("country", ""),
                    "time_utc":   ts.isoformat(),
                    "importance": ev.get("importance", ""),
                    "forecast":   ev.get("forecast") or ev.get("predict"),
                    "previous":   ev.get("previous"),
                    "actual":     ev.get("actual"),
                })
        return out[:n]

    @property
    def latest_surprise(self) -> Optional[dict]:
        if not self._latest_surprise:
            return None
        ev = self._latest_surprise
        return {
            "event":    ev.get("event", ""),
            "region":   ev.get("region") or ev.get("country", ""),
            "actual":   ev.get("actual"),
            "forecast": ev.get("forecast") or ev.get("predict"),
            "score":    self.get_surprise_score(),
            "time_utc": ev["_ts"].isoformat() if ev.get("_ts") else None,
        }

    # ── REST ──────────────────────────────────────────────────────────────────

    async def _fetch_rest(self):
        """Busca e armazena eventos High-impact via REST."""
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    _REST_BASE + _REST_PATH,
                    params={"importance": "3"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as r:
                    data = await r.json()

            raw = data.get("data", [])
            if not isinstance(raw, list):
                raw = []

            enriched = [self._enrich(e) for e in raw]
            valid    = [e for e in enriched if e.get("_ts")]
            valid.sort(key=lambda e: e["_ts"])
            self._events = valid
            log.debug("Calendar REST: %d eventos carregados.", len(valid))

        except Exception as exc:
            log.warning("Calendar REST falhou (%s) — a operar sem dados de calendário.", exc)

    async def _poll_loop(self):
        """Atualiza via REST a cada 30 min (redundância ao WS)."""
        while True:
            await asyncio.sleep(30 * 60)
            await self._fetch_rest()

    # ── WebSocket ─────────────────────────────────────────────────────────────

    async def _ws_loop(self):
        """Loop de reconexão ao WS de calendário económico."""
        backoff = 5
        while True:
            try:
                async with websockets.connect(
                    _WS_URL,
                    ping_interval=25,
                    ping_timeout=10,
                    open_timeout=15,
                ) as ws:
                    await ws.send(json.dumps({
                        "op":   "subscribe",
                        "args": [{"channel": "economic-calendar"}],
                    }))
                    log.debug("Calendar WS subscrito.")
                    backoff = 5
                    async for raw in ws:
                        try:
                            self._handle_ws_message(json.loads(raw))
                        except Exception:
                            pass
            except asyncio.CancelledError:
                return
            except Exception as exc:
                log.debug("Calendar WS desconectado: %s — reconecta em %ds.", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)

    def _handle_ws_message(self, msg: dict):
        if msg.get("event") in ("subscribe", "error"):
            return

        items = msg.get("data", [])
        if not isinstance(items, list):
            return

        for ev in items:
            enriched = self._enrich(ev)
            if not enriched.get("_ts"):
                continue

            # Chave de identificação do evento (remove duplicado se existir)
            key = ev.get("calId") or ev.get("id") or str(enriched["_ts"].isoformat())
            self._events = [
                e for e in self._events
                if (e.get("calId") or e.get("id") or str(e.get("_ts", "").isoformat() if e.get("_ts") else "")) != key
            ]
            self._events.append(enriched)
            self._events.sort(key=lambda e: e["_ts"])

            # Se evento High-impact com divulgação real → registar surpresa
            if self._is_high(ev) and ev.get("actual"):
                self._latest_surprise = enriched
                score = self.get_surprise_score()
                log.info(
                    "Calendar — evento divulgado: '%s' | actual=%s forecast=%s | score=%s",
                    ev.get("event", "?"),
                    ev.get("actual"),
                    ev.get("forecast") or ev.get("predict"),
                    f"{score:+.3f}" if score is not None else "N/A",
                )

    # ── Helpers estáticos ─────────────────────────────────────────────────────

    @staticmethod
    def _is_high(ev: dict) -> bool:
        imp = str(ev.get("importance") or ev.get("impact") or "")
        return imp in _HIGH

    @classmethod
    def _enrich(cls, ev: dict) -> dict:
        ev = dict(ev)
        ev["_ts"] = _parse_ts(ev)
        return ev


# ── Funções auxiliares de parsing ─────────────────────────────────────────────

def _parse_ts(ev: dict) -> Optional[datetime]:
    """Tenta extrair um datetime UTC a partir dos campos típicos do provider."""
    for field in ("date", "time", "ts", "pubTime", "eventTime"):
        raw = ev.get(field)
        if not raw:
            continue
        # Timestamp em milissegundos (string ou int)
        try:
            ms = int(raw)
            return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        except (ValueError, TypeError):
            pass
        # ISO 8601 com ou sem 'Z'
        try:
            s = str(raw).replace("Z", "+00:00")
            return datetime.fromisoformat(s).astimezone(timezone.utc)
        except Exception:
            pass
    return None


def _parse_float(s) -> Optional[float]:
    """Extrai float de strings como '5.25%', '250K', '1.2M', etc."""
    if s is None:
        return None
    try:
        text = str(s).strip()
        multiplier = 1.0
        upper = text.upper()
        if upper.endswith("K"):
            multiplier = 1_000.0
            text = text[:-1]
        elif upper.endswith("M"):
            multiplier = 1_000_000.0
            text = text[:-1]
        elif upper.endswith("B"):
            multiplier = 1_000_000_000.0
            text = text[:-1]
        text = text.rstrip("%").strip()
        return float(text) * multiplier
    except (ValueError, TypeError):
        return None


# Singleton global — importado por bot_manager e main
calendar_feed = EconomicCalendarFeed()
