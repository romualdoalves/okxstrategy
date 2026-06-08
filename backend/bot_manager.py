"""
bot_manager.py — Gerencia múltiplas instâncias de bots concorrentes.

Cada bot roda numa asyncio.Task independente.
O BotManager expõe start/stop/status para a API REST.
"""

from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from .database import BotModel, TradeModel, BotSnapshotModel, OrderRejectionModel, SignalLogModel, SessionLocal
from .exchanges.base import BaseExchange
from .exchanges.factory import (
    build_exchange,
    get_exchange_maintenance,
    get_exchange_provider,
    get_private_stream_class,
    get_public_stream_class,
    map_timeframe_for_history,
    map_timeframe_for_ws_channel,
)
from .strategies.registry import get_strategy
from .strategies.base import Signal
from .feeds.economic_calendar import calendar_feed
from .feeds.onchain_monitor import onchain_monitor
from .feeds.dex_price_feed import DexPriceFeed
from .feeds.gex_feed import GexFeed
from .feeds.okx_market_players import OkxMarketPlayersFeed
from .notifications import (
    TelegramNotifier,
    build_start_msg,
    build_entry_msg,
    build_tp1_msg,
    build_exit_msg,
    build_circuit_breaker_msg,
    build_order_confirmed_msg,
    build_order_failed_msg,
)

log = logging.getLogger("bot_manager")
FIXED_STAKE_USD = 100.0

CANONICAL_ENTRY_CRITERIA: dict[str, list[str]] = {
    "TF001": ["C1 EMA", "C2 VWAP", "C3 ATR"],
    "TF002": ["C1 RSI", "C2 MA"],
    "TF003": ["C1 MACD", "C2 Histograma", "C3 Tendência MA"],
    "TF004": ["C1 SuperTrend", "C2 RSI", "C3 Distância ST"],
    "TF005": ["C1 Tendência", "C2 Setup", "C3 Gatilho"],
    "TF006": ["C1 ADX Semanal", "C2 Donchian 52W", "C3 Recuo EMA 21", "C4 Gatilho"],
    "TF007": ["C1 4H Bias", "C2 1H Trend", "C3 Gatilho"],
    "TF008": ["C1 Ciclo Macro", "C2 Suporte", "C3 Proximidade"],
    "TF010": ["C1 Stoch K/D"],
    "TF011": ["C1 Regime", "C2 EMA", "C3 Volume", "C4 RSI Slope"],
    "TF012": ["C1 Tendência", "C2 Liquidez", "C3 Gatilho"],
    "TF013": ["C1 Posição BB", "C2 Largura BB", "C3 Volume"],
    "PA001": ["C1 Tendência 1-2-3", "C2 Zona EMA+S/R", "C3 Candle de rejeição"],
    "PA002": ["C1 AB≈CD", "C2 EMA21"],
    "PA003": ["C1 Zona", "C2 Range/ATR", "C3 Onda", "C4 Gatilho", "C5 R:R"],
    "PA004": ["C1 3LB Pattern", "C2 EMA200", "C3 RSI", "C4 Volume", "C5 Risco"],
    "PA005": ["C1 Suporte", "C2 Sweep", "C3 CHoCH"],
    "PA006": ["C1 Zone", "C2 Impulse", "C3 FVG", "C4 OB", "C5 Risk"],
    "MR001": ["C1 Zona", "C2 Cruzamento"],
    "MR002": ["C1 Banda 4σ", "C2 Banda 2σ", "C3 Zona R/S"],
    "MR003": ["C1 VWAP", "C2 Volume", "C3 Pattern", "C4 RSI"],
    "MR004": ["C1 RSI", "C2 Hilega", "C3 Milega", "C4 Tendência"],
    "MR005": ["C1 RSI", "C2 Posição BB", "C3 SMA 20"],
    "SC001": ["C1 Manipulação", "C2 Padrão Rejeição"],
    "SC002": ["C1 Sessão", "C2 Range", "C3 Rompimento", "C4 VWAP", "C5 Players", "C6 Volume", "C7 Risco"],
    "SC003": ["C1 Zona Inst.", "C2 Delta", "C3 Clímax"],
    "IF001": ["C1 Whale score"],
    "IF002": ["C1 Spread", "C2 Net Spread", "C3 Liquidez", "C4 Dados"],
    "IF003": ["C1 Liquidez", "C2 Atemporal", "C3 Agressão", "C4 Confirmação"],
    "RG001": ["C1 Sinal", "C2 Regime", "C3 Persistência", "C4 Estacionário"],
    "RG002": ["C1 Regime", "C2 Confiança"],
    "RG004": ["C1 Localização", "C2 Frequência", "C3 Absorção", "C4 Markov"],
    "NW001": ["C1 Líder", "C2 Seguidor", "C3 Impulso", "C4 RSI Seguidor"],
    "T000": ["C1 Pipeline"],
}


# ── BotInstance ───────────────────────────────────────────────────────────────

class BotInstance:
    """
    Uma instância de bot em execução.
    Conecta ao WebSocket da exchange, processa candles e executa ordens.
    """

    def __init__(self, config: BotModel, ws_broadcast):
        self.config       = config
        self.config.stake_usd = FIXED_STAKE_USD
        self._broadcast   = ws_broadcast   # coroutine para enviar ao frontend
        self.strategy     = get_strategy(config.strategy_id)
        if config.strategy_params:
            self.strategy.set_params(config.strategy_params)

        self._candles     = []
        self._graph_state : dict | None = None   # último estado do grafo
        self._direction   = 0       # 0=flat 1=long -1=short
        self._entry_price = 0.0
        self._sz          = 0.0
        self._sz_remaining= 0.0
        self._tp1_price   = 0.0
        self._sl_price    = 0.0
        self._tp1_done    = False
        self._sl_algo_id  : Optional[str] = None
        self._ts_algo_id  : Optional[str] = None
        self._entry_ord_id: Optional[str] = None # ID da ordem de entrada
        self._tp1_ord_id  : Optional[str] = None # ID da ordem de TP1
        self._exit_ord_id : Optional[str] = None # ID de liquidação manual
        self._current_trade_id: Optional[int] = None # ID do registro no banco
        self.exchange     : Optional[BaseExchange] = None # Adaptador de exchange
        self._current_atr = 0.0
        self._peak_price       = 0.0  # Maior preço alcançado (Long) ou menor (Short) após gatilho
        self._ts_callback_ratio = 0.0  # Callback ratio do trailing stop ativo
        self._daily_pnl   = 0.0
        self._wins        = 0
        self._losses      = 0
        self._halted      = False
        self._hold_reason     : str = ""
        self._last_indicators : dict = {}
        self._last_order_criteria: dict = self._empty_order_criteria()
        self._last_order_error: Optional[dict] = None
        self._entry_inflight  = False
        self._maintenance     : Optional[str] = None
        self._started_at      : Optional[str] = None   # ISO timestamp de início
        self._last_signal_log_id : Optional[int] = None
        self._task        : Optional[asyncio.Task] = None
        self.status       = "stopped"   # stopped | running | maintenance | error
        self._notifier    = TelegramNotifier()
        self._restarted   = False   # marcado pelo BotManager quando reinicia
        self._extra_candles: dict[str, list] = {}  # TF extras para estratégias multi-TF
        self._onchain_events: list[dict] = []       # eventos on-chain para pesquisa/sinais
        self._dex_prices: dict | None = None        # preços DEX para estratégias de arbitragem
        self._dex_feed = DexPriceFeed() if getattr(self.strategy, 'needs_dex_context', False) else None
        self._gex_snapshot = None                   # último GexSnapshot (Deribit options)
        self._gex_feed = GexFeed() if getattr(self.strategy, 'needs_gex_context', False) else None
        self._market_players: dict | None = None    # snapshot OKX Rubik long/short
        self._market_players_feed = OkxMarketPlayersFeed() if getattr(self.strategy, 'needs_market_players_context', False) else None
        self._last_persisted_sl: float = 0.0        # último sl_price salvo no banco (trailing)

    @staticmethod
    def _is_spot_symbol(symbol: str) -> bool:
        s = (symbol or "").upper()
        return "-" in s and not (s.endswith("-SWAP") or s.endswith("-FUTURES"))

    async def _close_size_from_exchange(self, exchange: BaseExchange) -> float:
        local_size = abs(float(self._sz or 0.0))
        if self._is_spot_symbol(self.config.symbol) and self._direction > 0:
            try:
                pos = await exchange.get_position(self.config.symbol)
                if pos and pos.side == "long" and pos.size > 0:
                    real_size = abs(float(pos.size))
                    if local_size > 0 and abs(real_size - local_size) > max(1e-8, local_size * 0.001):
                        log.warning(
                            "[Bot %d] SPOT size desync em %s: app=%.8f OKX=%.8f. Fechando saldo real.",
                            self.config.id, self.config.symbol, local_size, real_size,
                        )
                    return real_size
            except Exception as exc:
                log.warning("[Bot %d] Falha ao obter saldo spot real para fechamento: %s", self.config.id, exc)
        return local_size

    async def _entry_size_from_exchange(self, exchange: BaseExchange, fallback_size: float) -> float:
        fallback_size = abs(float(fallback_size or 0.0))
        if self._is_spot_symbol(self.config.symbol) and self._direction >= 0:
            try:
                pos = await exchange.get_position(self.config.symbol)
                if pos and pos.side == "long" and pos.size > 0:
                    real_size = abs(float(pos.size))
                    if fallback_size > 0 and abs(real_size - fallback_size) > max(1e-8, fallback_size * 0.001):
                        log.warning(
                            "[Bot %d] Entrada SPOT ajustada pelo saldo líquido OKX: fill=%.8f saldo=%.8f.",
                            self.config.id, fallback_size, real_size,
                        )
                    return real_size
            except Exception as exc:
                log.warning("[Bot %d] Falha ao obter saldo spot real da entrada: %s", self.config.id, exc)
        return fallback_size

    async def _sync_entry_fill_from_exchange(
        self,
        exchange: BaseExchange,
        ord_id: str,
        fallback_price: float,
        fallback_size: float,
    ) -> tuple[float, float]:
        fill_price = fallback_price
        fill_size = fallback_size
        for _ in range(12):
            await asyncio.sleep(0.5)
            try:
                order = await exchange.get_order(ord_id)
                if order and order.get("status") == "filled":
                    fill_price = float(order.get("filled_avg_price") or fill_price)
                    fill_size = float(order.get("filled_size") or fill_size)
                    break
            except Exception:
                pass

        for _ in range(12):
            try:
                pos = await exchange.get_position(self.config.symbol)
                if pos and pos.size > 0:
                    real_size = abs(float(pos.size))
                    real_price = float(pos.avg_price or fill_price)
                    if abs(real_size - fill_size) > max(1e-8, fill_size * 0.001):
                        log.warning(
                            "[Bot %d] Entrada SPOT ajustada pelo saldo OKX: fill=%.8f saldo=%.8f.",
                            self.config.id, fill_size, real_size,
                        )
                    return real_price, real_size
            except Exception as exc:
                log.warning("[Bot %d] Falha ao validar posição pós-entrada na OKX: %s", self.config.id, exc)
            await asyncio.sleep(0.5)

        return fill_price, fill_size

    async def _confirm_flat_then_close(self, price: float, size: float, exchange: BaseExchange) -> bool:
        for _ in range(12):
            try:
                pos = await exchange.get_position(self.config.symbol)
                if pos is None or abs(float(pos.size or 0.0)) <= 1e-9:
                    await self._on_position_closed(price, size, exchange)
                    return True
                self._sz = abs(float(pos.size))
                self._sz_remaining = self._sz
            except Exception:
                pass
            await asyncio.sleep(0.5)
        log.warning(
            "[Bot %d] Fechamento não confirmado: OKX ainda mostra posição em %s. App não será marcado como flat.",
            self.config.id, self.config.symbol,
        )
        return False

    @staticmethod
    def _criterion(
        *,
        id: str,
        label: str,
        status: str,
        detail: str,
        value: str = "",
        blocking: bool = True,
    ) -> dict:
        return {
            "id": id,
            "label": label,
            "sub": detail,
            "value": value,
            "status": status,
            "detail": detail,
            "blocking": blocking,
        }

    @classmethod
    def _empty_order_criteria(cls) -> dict:
        items = [
            cls._criterion(
                id="signal",
                label="O1 Sinal",
                value="HOLD",
                status="yellow",
                detail="Aguardando BUY/SELL da estratégia.",
            )
        ]
        return cls._pack_order_criteria(items)

    @staticmethod
    def _pack_order_criteria(items: list[dict]) -> dict:
        active = [c for c in items if c.get("status") != "none"]
        blocking = [c for c in active if c.get("blocking", True)]
        ok = [c for c in blocking if c.get("status") == "green"]
        blocked = [c for c in blocking if c.get("status") == "red"]
        waiting = [c for c in blocking if c.get("status") == "yellow"]
        ready = bool(blocking) and len(ok) == len(blocking)
        first_blocker = blocked[0] if blocked else waiting[0] if waiting else None
        return {
            "ready": ready,
            "ok": len(ok),
            "total": len(blocking),
            "items": items,
            "blocker": first_blocker,
            "reason": "" if ready else (first_blocker or {}).get("detail", ""),
        }

    def _runtime_indicators(self, result) -> dict:
        indicators = dict(result.indicators or {})
        signal = getattr(getattr(result, "signal", Signal.HOLD), "value", Signal.HOLD.value)
        strategy_id = (self.config.strategy_id or "").strip().upper()
        indicators["_signal"] = signal
        indicators["_hold_reason"] = result.hold_reason or ""
        indicators["_criteria_met"] = int(getattr(result, "criteria_met", 0) or 0)
        indicators["_criteria_total"] = int(getattr(result, "criteria_total", 0) or 0)
        indicators["_signal_executable"] = signal in (Signal.BUY.value, Signal.SELL.value)

        canonical_names = CANONICAL_ENTRY_CRITERIA.get(strategy_id)
        if canonical_names:
            canonical_total = len(canonical_names)
            reported_total = indicators["_criteria_total"]
            reported_met = indicators["_criteria_met"]
            if signal in (Signal.BUY.value, Signal.SELL.value) and reported_total <= 1 and canonical_total > 1:
                indicators["_criteria_met"] = canonical_total
            else:
                indicators["_criteria_met"] = min(max(reported_met, 0), canonical_total)
            indicators["_criteria_total"] = canonical_total
            indicators["_criteria_names"] = canonical_names

        # Inclui critérios da estratégia para o frontend quando não há mapa canônico.
        try:
            info = self.strategy.info()
            if not canonical_names and getattr(info, 'criteria', None):
                indicators["_criteria_names"] = [c.get('label', c.get('id', f'C{i+1}')) for i, c in enumerate(info.criteria)]
                indicators["_criteria_total"] = len(info.criteria)
                indicators["_criteria_met"] = min(indicators["_criteria_met"], indicators["_criteria_total"])
        except Exception:
            pass
        return indicators

    # ── Controle ─────────────────────────────────────────────────────────────

    def start(self):
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._task       = asyncio.create_task(self._run())
        self.status      = "running"

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
        self._started_at = None
        self.status      = "stopped"

    # ── Loop principal ────────────────────────────────────────────────────────

    async def _run(self):
        provider = get_exchange_provider()
        ws_channel = map_timeframe_for_ws_channel(self.config.timeframe)
        bar = map_timeframe_for_history(self.config.timeframe)
        public_stream_cls = get_public_stream_class()

        try:
            async with aiohttp.ClientSession() as session:
                self.exchange = build_exchange(session, demo=self.config.demo)
                exchange = self.exchange

                # Recupera estado anterior do banco de dados (se houver trade aberto)
                self._recover_state()

                # Sincroniza posição real da exchange para evitar reentradas
                try:
                    pos = await exchange.get_position(self.config.symbol)
                    if pos is not None:
                        if self._direction != 0:
                            # Sincroniza posição se o banco de dados já possui um trade ativo
                            actual_pos = pos.size if pos.side == "long" else -pos.size
                            old_entry  = self._entry_price  # guarda para comparação
                            self._direction   = 1 if actual_pos > 0 else -1
                            self._sz          = abs(actual_pos)
                            self._sz_remaining = self._sz
                            self._entry_price = pos.avg_price
                            self._save_trade(
                                {"size": self._sz, "entry_price": self._entry_price},
                                update_id=self._current_trade_id,
                            )
                            log.info(
                                "[Bot %d] Sincronizado com a exchange: %.4f @ %f detectada.",
                                self.config.id,
                                actual_pos,
                                self._entry_price,
                            )

                            # Se a entrada diverge significativamente (>0.5%), o _sl_price
                            # do banco pertence a outro contexto e precisa ser recalculado.
                            # Sem isso, _sl_price pode ficar acima do preço atual para um LONG
                            # (ou abaixo para um SHORT), distorcendo guaranteed_pnl e
                            # eventualmente disparando o SW-TS de forma prematura.
                            if (old_entry > 0
                                    and abs(pos.avg_price - old_entry) / old_entry > 0.005
                                    and self._sl_price > 0):
                                old_dist_pct = abs(self._sl_price - old_entry) / old_entry
                                # Preserva a distância percentual do SL, com teto de 5 %
                                safe_pct = min(max(old_dist_pct, 0.005), 0.05)
                                new_sl = round(
                                    pos.avg_price * (1 - safe_pct) if self._direction == 1
                                    else pos.avg_price * (1 + safe_pct),
                                    4,
                                )
                                log.warning(
                                    "[Bot %d] Entrada diverge DB %.4f vs OKX %.4f — "
                                    "SL recalculado de %.4f para %.4f.",
                                    self.config.id, old_entry, pos.avg_price,
                                    self._sl_price, new_sl,
                                )
                                self._sl_price = new_sl

                            # Sincronização de Emergência: reativa SW-TS se lucro >= 1% já existe
                            if self._entry_price > 0:
                                ticker = await exchange.get_ticker(self.config.symbol)
                                last_price = float(ticker.get("last") or ticker.get("ask") or self._entry_price)
                                pnl_pct = (last_price - self._entry_price) / self._entry_price * self._direction

                                if pnl_pct >= 0.01:
                                    cb = max(0.005, (self._current_atr * 1.5) / last_price if self._current_atr else 0.005)
                                    self._tp1_done = True
                                    self._peak_price = last_price
                                    self._ts_algo_id = "sw"
                                    self._ts_callback_ratio = cb
                                    self._sl_price = (last_price * (1 - cb) if self._direction == 1
                                                      else last_price * (1 + cb))
                                    log.info(
                                        "[Bot %d] Start Sync: PnL %.2f%% — SW-TS reativado. peak=%.4f stop=%.4f",
                                        self.config.id, pnl_pct * 100, self._peak_price, self._sl_price,
                                    )
                        else:
                            # Banco FLAT mas OKX tem posição → adotar automaticamente.
                            # Ignorar deixaria o bot em HOLD permanente (O11 bloqueia nova entrada
                            # enquanto a posição órfã existir na exchange sem gestão local).
                            adopted_dir = 1 if pos.side == "long" else -1
                            adopted_sz  = abs(pos.size)
                            adopted_px  = pos.avg_price or 0.0
                            # SL conservador de 5 % — substituído pelo ATR real no primeiro candle
                            fallback_sl = round(
                                adopted_px * (1 - 0.05) if adopted_dir == 1
                                else adopted_px * (1 + 0.05), 6
                            )
                            self._direction    = adopted_dir
                            self._sz           = adopted_sz
                            self._sz_remaining = adopted_sz
                            self._entry_price  = adopted_px
                            self._sl_price     = fallback_sl
                            # Calcula TP1 para posição órfã (2% de lucro como padrão conservador)
                            orphan_tp1 = adopted_px * (1.02 if adopted_dir == 1 else 0.98)
                            self._tp1_price    = orphan_tp1
                            log.warning(
                                "[Bot %d] Posição órfã adotada: %.4f %s @ %.4f "
                                "(banco estava FLAT). SL conservador em %.4f. TP1 em %.4f. "
                                "Posição será gerenciada normalmente.",
                                self.config.id, adopted_sz, pos.side, adopted_px, fallback_sl, orphan_tp1,
                            )
                            trade_id = self._save_trade({
                                "type":        "entry",
                                "event":       "ORPHAN_ADOPTED",
                                "direction":   "LONG" if adopted_dir == 1 else "SHORT",
                                "size":        adopted_sz,
                                "entry_price": adopted_px,
                                "sl_price":    fallback_sl,
                                "tp1_price":   orphan_tp1,
                                "source":      "orphan_recovery",
                            })
                            self._current_trade_id = trade_id
                    else:
                        if self._direction != 0:
                            log.warning(
                                "[Bot %d] Desajuste detectado: banco de dados contem trade aberto (%d) "
                                "mas a exchange está FLAT. Resetando estado para FLAT.",
                                self.config.id, self._direction
                            )
                            self._save_trade({
                                "type": "exit",
                                "event": "DESYNC_RESET",
                                "exit_price": self._entry_price or 0.0,
                                "pnl": 0.0,
                                "closed_at": datetime.now(timezone.utc).replace(tzinfo=None)
                            }, update_id=self._current_trade_id)
                            self._reset()
                except Exception as e:
                    log.warning("[Bot %d] Falha na sincronização inicial: %s", self.config.id, e)

                # Sincroniza stop order existente na OKX para recuperar _sl_algo_id real
                if self._direction != 0:
                    try:
                        open_orders = await exchange.get_open_orders(self.config.symbol)
                        stop_orders = [o for o in open_orders if o.get("type") == "stop"]
                        if stop_orders:
                            stop = stop_orders[0]
                            self._sl_algo_id = stop["id"]
                            recovered_stop_px = float(stop.get("stop_price") or self._sl_price)
                            if recovered_stop_px > 0:
                                self._sl_price = recovered_stop_px
                            log.info("[Bot %d] Stop order recuperado da OKX: %s @ %.4f",
                                     self.config.id, self._sl_algo_id, self._sl_price)
                    except Exception as e:
                        log.warning("[Bot %d] Falha ao recuperar stop orders: %s", self.config.id, e)

                # Inicia monitoramento de ordens privadas (SL/TP/Execuções)
                asyncio.create_task(self._monitor_orders(exchange))

                # Retry na inicialização — garante que o bot sobe mesmo se a rede
                # ainda não estiver pronta (ex: reinício do computador)
                # Pré-aquece o cache de instrumento (ctVal, lotSz) para que
                # num_contracts() use o tamanho correto desde o primeiro sinal.
                if hasattr(exchange, "warmup_instrument"):
                    try:
                        await exchange.warmup_instrument(self.config.symbol)
                        log.info("[Bot %d] Instrumento %s aquecido (ctVal=%.6g).",
                                 self.config.id, self.config.symbol,
                                 exchange.get_contract_size(self.config.symbol))
                    except Exception as _wi_exc:
                        log.warning("[Bot %d] warmup_instrument falhou: %s",
                                    self.config.id, _wi_exc)

                self.config.leverage = 1
                _INIT_MAX = 10
                _INIT_DELAY = 30  # segundos entre tentativas

                # Pré-aquecer candles — busca 300 para garantir que estratégias
                # com lookback longo (ex: RSI+MA com MA200 → min_len=205)
                # já tenham dados suficientes desde o primeiro tick
                for _attempt in range(1, _INIT_MAX + 1):
                    try:
                        candles = await exchange.fetch_candles(
                            self.config.symbol, bar, limit=300)
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        if _attempt < _INIT_MAX:
                            log.warning(
                                "[Bot %d] fetch_candles falhou (tentativa %d/%d): %s. "
                                "Aguardando %ds...",
                                self.config.id, _attempt, _INIT_MAX, exc, _INIT_DELAY,
                            )
                            await asyncio.sleep(_INIT_DELAY)
                        else:
                            raise

                if candles:
                    self._candles = candles

                    # Para estratégias de grafo: calcular grafo ANTES do compute()
                    # Sem isso, o bot ficaria esperando o próximo candle fechar
                    if getattr(self.strategy, 'needs_graph_context', False):
                        log.info("[Bot %d] Inicializando grafo de correlação...",
                                 self.config.id)
                        await self._update_graph_context(session)
                        # ts=0 sinaliza warmup — botão de interpretação habilitado desde o início
                        if self._graph_state:
                            self._graph_state['last_candle_ts'] = 0

                    # Para estratégias multi-TF: buscar candles extras no warmup
                    if self.strategy.__class__.extra_timeframes():
                        log.info("[Bot %d] Buscando timeframes extras: %s",
                                 self.config.id,
                                 self.strategy.__class__.extra_timeframes())
                        await self._update_extra_candles(exchange)

                    if getattr(self.strategy, 'needs_onchain_context', False):
                        await self._update_onchain_context(session)

                    if getattr(self.strategy, 'needs_dex_context', False) and session:
                        await self._update_dex_context(session, exchange)

                    if getattr(self.strategy, 'needs_gex_context', False) and session:
                        await self._update_gex_context(session)

                    if getattr(self.strategy, 'needs_market_players_context', False) and session:
                        await self._update_market_players_context(session)

                    ctx = self._strategy_context()
                    result = self.strategy.compute_with_context(candles, ctx)
                    if result:
                        self._current_atr     = result.indicators.get("atr", 0)
                        self._last_indicators = self._runtime_indicators(result)
                        log.info("[Bot %d] Pronto | %s",
                                 self.config.id, result.indicators)
                        self._last_signal_log_id = self._save_signal_log(
                            result, candles[-1])
                        if result.signal == Signal.BUY and self._direction == 0:
                            log.info("[Bot %d] SINAL LONG NO WARMUP | %s",
                                     self.config.id, result.indicators)
                            await self._enter(
                                "long", candles[-1].close, result, exchange,
                                self._last_signal_log_id)
                        elif result.signal == Signal.SELL and self._direction == 0:
                            log.info("[Bot %d] SINAL SHORT NO WARMUP | %s",
                                     self.config.id, result.indicators)
                            await self._enter(
                                "short", candles[-1].close, result, exchange,
                                self._last_signal_log_id)

                # Notifica início/reinício após warmup concluído
                asyncio.create_task(self._notifier.send(build_start_msg(
                    bot_name        = self.config.name,
                    strategy_id     = self.config.strategy_id,
                    symbol          = self.config.symbol,
                    timeframe       = self.config.timeframe,
                    leverage        = self.config.leverage,
                    stake_usd       = FIXED_STAKE_USD,
                    demo            = self.config.demo,
                    stop_loss_usd   = self.config.stop_loss_usd,
                    strategy_params = self.config.strategy_params or {},
                    indicators      = self._last_indicators,
                    restarted       = self._restarted,
                )))

                # ── Loop de reconexão WS com detecção de manutenção ──────
                ws_failures = 0
                while True:
                    try:
                        stream = public_stream_cls(channel=ws_channel,
                                                   symbol=self.config.symbol,
                                                   demo=self.config.demo)
                        async for bar_data, closed in stream.iter():
                            ws_failures = 0
                            # Limpa estado de manutenção ao retornar
                            if self._maintenance:
                                log.info("[Bot %d] %s voltou ao ar.", self.config.id, provider.upper())
                                self._maintenance = None
                                self.status = "running"
                                await self._broadcast({
                                    "type":   "maintenance_end",
                                    "bot_id": self.config.id,
                                })
                            await self._on_candle(bar_data, closed,
                                                  exchange, session)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        ws_failures += 1
                        log.warning("[Bot %d] WS desconectado (#%d): %s",
                                    self.config.id, ws_failures, e)

                        # Após 3 falhas consecutivas → verificar manutenção do provider
                        if ws_failures >= 3:
                            maint = await get_exchange_maintenance(session)
                            if maint:
                                msg = maint.get("title", f"Manutenção {provider.upper()}")
                                end_ts = maint.get("end", "")
                                self._maintenance = msg
                                self.status = "maintenance"
                                log.warning(
                                    "[Bot %d] %s em manutenção: %s (fim: %s). "
                                    "Verificando novamente em 60 s.",
                                    self.config.id, provider.upper(), msg, end_ts,
                                )
                                await self._broadcast({
                                    "type":   "maintenance",
                                    "bot_id": self.config.id,
                                    "msg":    msg,
                                    "end_ts": end_ts,
                                })
                                await asyncio.sleep(60)
                                ws_failures = 0
                                continue
                            else:
                                ws_failures = 0  # não é manutenção — reset contador

                        await asyncio.sleep(5)

        except asyncio.CancelledError:
            log.info("[Bot %d] Parado.", self.config.id)
            self.status = "stopped"
        except Exception as e:
            log.error("[Bot %d] Erro: %s", self.config.id, e, exc_info=True)
            self.status = "error"

    # ── Processamento de candle ───────────────────────────────────────────────

    # ── Contexto multi-asset para estratégias de grafo ────────────────────────

    _GRAPH_ASSETS = [
        'BTC-USDT', 'ETH-USDT',
        'SOL-USDT', 'XRP-USDT', 'BNB-USDT',
    ]

    async def _update_extra_candles(self, exchange: BaseExchange):
        """Busca candles dos timeframes extras declarados pela estratégia."""
        extra_tfs = self.strategy.__class__.extra_timeframes()
        if not extra_tfs:
            return
        for tf_bar in extra_tfs:
            try:
                candles = await exchange.fetch_candles(
                    self.config.symbol, tf_bar, limit=200)
                if candles:
                    self._extra_candles[tf_bar] = candles
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning('[Bot %d] Multi-TF: falha ao buscar %s: %s',
                            self.config.id, tf_bar, exc)

    async def _update_graph_context(self, session: "aiohttp.ClientSession"):
        """Busca closes de múltiplos ativos e atualiza o grafo da estratégia."""
        bar_map = {'1m':'1m','5m':'5m','15m':'15m','1h':'1H','4h':'4H','1D':'1D'}
        bar     = bar_map.get(self.config.timeframe, '15m')

        price_matrix: dict[str, list[float]] = {}
        for symbol in self._GRAPH_ASSETS:
            try:
                async with session.get(
                    'https://www.okx.com/api/v5/market/candles',
                    params={'instId': symbol, 'bar': bar, 'limit': '60'},
                ) as r:
                    data = await r.json()
                rows   = data.get('data', [])
                closes = [float(c[4]) for c in reversed(rows)]  # ASC
                if closes:
                    price_matrix[symbol] = closes
            except Exception as exc:
                log.warning('[Bot %d] Graph: falha ao buscar %s: %s',
                            self.config.id, symbol, exc)

        if price_matrix:
            self._graph_state = self.strategy.update_graph(price_matrix)
            log.debug('[Bot %d] Grafo atualizado — regime: %s',
                      self.config.id,
                      self._graph_state['regime']['name'])

    async def _update_onchain_context(self, session: "aiohttp.ClientSession"):
        """Atualiza eventos on-chain gratuitos para estratégias signal-only."""
        try:
            cfg = self.strategy.onchain_config() if hasattr(self.strategy, 'onchain_config') else {}
            self._onchain_events = await onchain_monitor.update(
                session,
                symbol=self.config.symbol,
                **cfg,
            )
            if self._onchain_events:
                largest = max(self._onchain_events, key=lambda e: e.get("amount_usd", 0))
                log.info("[Bot %d] On-chain: %d evento(s), maior %.2f USD",
                         self.config.id, len(self._onchain_events),
                         largest.get("amount_usd", 0))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("[Bot %d] On-chain context falhou: %s",
                        self.config.id, exc)

    async def _update_gex_context(self, session: "aiohttp.ClientSession"):
        """Busca snapshot GEX do Deribit para estratégias de Gamma Exposure."""
        if not self._gex_feed:
            return
        try:
            snap = await self._gex_feed.fetch(session)
            if snap:
                self._gex_snapshot = snap
                log.info(
                    "[Bot %d] GEX atualizado — regime: %s, GEX: %.0f, PCR: %.3f",
                    self.config.id, snap.regime, snap.gex_value, snap.pcr,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("[Bot %d] GEX context falhou: %s", self.config.id, exc)

    async def _update_dex_context(self, session: "aiohttp.ClientSession", exchange: BaseExchange):
        """Busca preços DEX e injeta o preço OKX para comparação."""
        if not self._dex_feed:
            return
        try:
            prices = await self._dex_feed.fetch_prices(session, self.config.symbol)
            # Injeta preço OKX atual
            try:
                ticker = await exchange.get_ticker(self.config.symbol)
                self._dex_feed.set_okx_price(
                    self.config.symbol,
                    bid=ticker.get("bid") or ticker.get("last") or 0.0,
                    ask=ticker.get("ask") or ticker.get("last") or 0.0,
                    last=ticker.get("last") or 0.0,
                )
                prices["okx"] = {
                    "bid": ticker.get("bid") or ticker.get("last") or 0.0,
                    "ask": ticker.get("ask") or ticker.get("last") or 0.0,
                    "last": ticker.get("last") or 0.0,
                }
            except Exception as exc:
                log.debug("[Bot %d] Falha ao buscar ticker OKX para DEX context: %s", self.config.id, exc)
            self._dex_prices = prices
            log.info("[Bot %d] DEX context atualizado — %d fontes, idade %ds",
                     self.config.id,
                     len(prices.get("sources", [])),
                     int(time.time()) - prices.get("timestamp", 0))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("[Bot %d] DEX context falhou: %s", self.config.id, exc)

    async def _update_market_players_context(self, session: "aiohttp.ClientSession"):
        """Busca estatisticas publicas OKX Rubik de varejo vs top traders."""
        if not self._market_players_feed:
            return
        try:
            snapshot = await self._market_players_feed.fetch(session, self.config.symbol)
            if snapshot:
                self._market_players = snapshot
                log.info(
                    "[Bot %d] Players OKX atualizados: %s | retail %.2f | top L %.1f%% / S %.1f%%",
                    self.config.id,
                    snapshot.get("scenario"),
                    snapshot.get("retail_long_short_ratio", 0.0),
                    snapshot.get("top_long_ratio", 0.0) * 100,
                    snapshot.get("top_short_ratio", 0.0) * 100,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("[Bot %d] Players OKX context falhou: %s", self.config.id, exc)

    def _strategy_context(self) -> dict | None:
        ctx = {"symbol": self.config.symbol}
        if self._extra_candles:
            ctx["extra_candles"] = self._extra_candles
        if self._onchain_events:
            ctx["onchain_events"] = self._onchain_events
        if self._dex_prices:
            ctx["dex_prices"] = self._dex_prices
        if self._gex_snapshot:
            ctx["gex_data"] = self._gex_snapshot
        if self._market_players:
            ctx["market_players"] = self._market_players
        return ctx

    async def _evaluate_order_criteria(
        self,
        *,
        result=None,
        direction: str | None = None,
        price: float = 0.0,
        exchange: BaseExchange | None = None,
        check_exchange_position: bool = False,
    ) -> dict:
        signal_value = getattr(getattr(result, "signal", Signal.HOLD), "value", Signal.HOLD.value)
        direction = direction or ("long" if signal_value == Signal.BUY.value else "short" if signal_value == Signal.SELL.value else None)
        symbol_upper = (self.config.symbol or "").upper()
        is_deriv = "-SWAP" in symbol_upper or "-FUTURES" in symbol_upper
        items: list[dict] = []

        items.append(self._criterion(
            id="signal",
            label="O1 Sinal executável",
            value=signal_value.upper(),
            status="green" if direction in ("long", "short") else "yellow",
            detail="Estratégia emitiu BUY/SELL." if direction else "Aguardando sinal BUY/SELL da estratégia.",
        ))

        if direction not in ("long", "short"):
            dormant = [
                ("runtime", "O2 Runtime", self.status.upper(), "Aguardando sinal para validar runtime."),
                ("circuit_breaker", "O3 Circuit Breaker", "—", "Aguardando sinal para validar circuit breaker."),
                ("local_flat", "O4 Posição local", "—", "Aguardando sinal para validar posição local."),
                ("inflight", "O5 Entrada pendente", "—", "Aguardando sinal para validar ordens pendentes."),
                ("market_direction", "O6 Direção", "—", "Aguardando sinal para validar direção permitida."),
                ("calendar", "O7 Calendário", "—", "Aguardando sinal para validar janela macro."),
                ("exchange", "O8 Conexão OKX", "—", "Aguardando sinal para validar conexão OKX."),
                ("sizing", "O9 Tamanho", "—", "Aguardando sinal para calcular tamanho."),
                ("risk", "O10 SL/TP", "—", "Aguardando sinal para validar SL/TP."),
                ("exchange_flat", "O11 Posição OKX", "—", "Será validado imediatamente antes do envio da ordem."),
            ]
            for id_, label, value, detail in dormant:
                items.append(self._criterion(
                    id=id_,
                    label=label,
                    value=value,
                    status="none",
                    detail=detail,
                    blocking=False,
                ))
            packed = self._pack_order_criteria(items)
            self._last_order_criteria = packed
            if not packed["ready"] and packed.get("reason"):
                self._hold_reason = packed["reason"]
            return packed

        items.append(self._criterion(
            id="runtime",
            label="O2 Runtime",
            value=self.status.upper(),
            status="green" if self.status == "running" and not self._maintenance else "red",
            detail="Bot em execução e exchange fora de manutenção." if self.status == "running" and not self._maintenance else (self._maintenance or "Bot não está em execução."),
        ))
        items.append(self._criterion(
            id="circuit_breaker",
            label="O3 Circuit breaker",
            value="OK" if not self._halted else "PAUSADO",
            status="green" if not self._halted else "red",
            detail="Stop diário não foi atingido." if not self._halted else "Circuit breaker ativo por stop diário.",
        ))
        items.append(self._criterion(
            id="local_flat",
            label="O4 Posição local",
            value="FLAT" if self._direction == 0 else ("LONG" if self._direction > 0 else "SHORT"),
            status="green" if self._direction == 0 else "red",
            detail="Bot está sem posição local." if self._direction == 0 else "Bot já está em operação.",
        ))
        items.append(self._criterion(
            id="pending_order",
            label="O5 Ordem pendente",
            value="LIVRE" if not self._entry_inflight else "ENVIANDO",
            status="green" if not self._entry_inflight else "yellow",
            detail="Nenhuma entrada está em envio." if not self._entry_inflight else "Uma entrada já está sendo enviada.",
        ))
        spot_short_blocked = direction == "short" and not is_deriv
        items.append(self._criterion(
            id="market_direction",
            label="O6 Mercado/direção",
            value="DERIV" if is_deriv else "SPOT",
            status="red" if spot_short_blocked else "green",
            detail="Direção permitida para este mercado." if not spot_short_blocked else "Mercado SPOT permite somente entradas compradas.",
        ))

        in_window, event_name = calendar_feed.is_high_impact_window(self.config.strategy_id)
        items.append(self._criterion(
            id="calendar",
            label="O7 Calendário",
            value="LIVRE" if not in_window else "BLOQUEADO",
            status="green" if not in_window else "red",
            detail="Fora de janela macro de alto impacto." if not in_window else f"Calendário: {event_name}",
        ))

        exchange_ok = exchange is not None
        items.append(self._criterion(
            id="exchange",
            label="O8 Conexão OKX",
            value="OK" if exchange_ok else "SEM EXCHANGE",
            status="green" if exchange_ok else "red",
            detail="Adaptador OKX disponível para envio." if exchange_ok else "Exchange não inicializada.",
        ))

        sz = 0.0
        if exchange and direction and price > 0:
            try:
                sz = float(exchange.num_contracts(
                    self.config.symbol,
                    price,
                    FIXED_STAKE_USD,
                    self.config.leverage,
                ) or 0.0)
            except Exception as exc:
                items.append(self._criterion(
                    id="sizing",
                    label="O9 Tamanho",
                    value="ERRO",
                    status="red",
                    detail=f"Falha ao calcular tamanho da ordem: {exc}",
                ))
            else:
                items.append(self._criterion(
                    id="sizing",
                    label="O9 Tamanho",
                    value=f"{sz:.6g}",
                    status="green" if sz > 0 else "red",
                    detail="Tamanho da ordem calculado." if sz > 0 else "Tamanho calculado é zero.",
                ))
        else:
            items.append(self._criterion(
                id="sizing",
                label="O9 Tamanho",
                value="—",
                status="yellow",
                detail="Aguardando preço, direção e exchange para calcular tamanho.",
            ))

        risk_ok = False
        if result and direction and price > 0:
            atr = result.indicators.get("atr", price * 0.005)
            sl_px = result.metadata.get("sl_price", price - atr * 1.5 if direction == "long" else price + atr * 1.5)
            tp1_px = result.metadata.get("tp1_price", price * 1.02 if direction == "long" else price * 0.98)
            risk_ok = (
                sl_px > 0 and tp1_px > 0 and
                ((direction == "long" and sl_px < price and tp1_px > price) or
                 (direction == "short" and sl_px > price and tp1_px < price))
            )
            items.append(self._criterion(
                id="risk",
                label="O10 SL/TP",
                value="OK" if risk_ok else "INVÁLIDO",
                status="green" if risk_ok else "red",
                detail="Stop Loss e TP1 coerentes com a direção." if risk_ok else "SL/TP inválidos para a direção do sinal.",
            ))
        else:
            items.append(self._criterion(
                id="risk",
                label="O10 SL/TP",
                value="—",
                status="yellow",
                detail="Aguardando sinal para validar SL/TP.",
            ))

        if check_exchange_position and exchange and direction:
            try:
                pos = await exchange.get_position(self.config.symbol)
                qty = abs(float(getattr(pos, "size", 0.0) or 0.0)) if pos else 0.0
                items.append(self._criterion(
                    id="exchange_flat",
                    label="O11 Posição OKX",
                    value="FLAT" if qty <= 0 else "ABERTA",
                    status="green" if qty <= 0 else "red",
                    detail="OKX sem posição aberta neste ativo." if qty <= 0 else "OKX já possui posição aberta neste ativo.",
                ))
            except Exception as exc:
                items.append(self._criterion(
                    id="exchange_flat",
                    label="O11 Posição OKX",
                    value="ERRO",
                    status="red",
                    detail=f"Falha ao validar posição na OKX: {exc}",
                ))
        else:
            items.append(self._criterion(
                id="exchange_flat",
                label="O11 Posição OKX",
                value="—",
                status="yellow" if direction else "none",
                detail="Será validado imediatamente antes do envio da ordem.",
            ))

        packed = self._pack_order_criteria(items)
        self._last_order_criteria = packed
        if not packed["ready"] and packed.get("reason"):
            self._hold_reason = packed["reason"]
        return packed

    async def _on_candle(self, bar, closed: bool, exchange: BaseExchange,
                         session=None):
        # Sincronização Dinâmica Automática: Apenas no fechamento do candle para economizar API
        if closed and self._ts_algo_id:
            await self.force_sync_trailing(exchange)

        # Verifica TP1 em tempo real (mesmo em candle aberto)
        if self._direction != 0 and not self._tp1_done:
            await self._check_tp1(bar.close, exchange)

        if not closed:
            # Atualiza peak intracandle para guaranteed_pnl ser preciso em tempo real
            if self._ts_algo_id and self._direction != 0:
                if self._direction == 1:
                    if self._peak_price == 0 or bar.close > self._peak_price:
                        self._peak_price = bar.close
                else:
                    if self._peak_price == 0 or bar.close < self._peak_price:
                        self._peak_price = bar.close

                # Software TS: keep _sl_price current and fire exit when stop is crossed
                if self._ts_algo_id == "sw" and self._ts_callback_ratio > 0 and self._peak_price > 0:
                    sw_stop = (
                        self._peak_price * (1 - self._ts_callback_ratio)
                        if self._direction == 1
                        else self._peak_price * (1 + self._ts_callback_ratio)
                    )
                    self._sl_price = sw_stop

                    stop_hit = (
                        (self._direction == 1  and bar.close <= sw_stop) or
                        (self._direction == -1 and bar.close >= sw_stop)
                    )
                    if stop_hit and not self._exit_ord_id:
                        self._exit_ord_id = "sw_pending"
                        log.info("[Bot %d] SW-TS TRIGGERED: price=%.4f stop=%.4f",
                                 self.config.id, bar.close, sw_stop)
                        asyncio.create_task(self._sw_exit(bar.close, exchange))

            await self._broadcast({
                "type":   "price",
                "bot_id": self.config.id,
                "price":  bar.close,
                "ts":     bar.epoch,
            })
            return

        # Atualiza histórico
        if not self._candles or self._candles[-1].epoch != bar.epoch:
            self._candles.append(bar)
            if len(self._candles) > 300:
                self._candles.pop(0)
            await self._process_strategy(bar, exchange, session)

    async def force_sync_trailing(self, exchange: BaseExchange):
        """Força a busca do ID e do preço de stop real na exchange imediatamente."""
        # Se não há posição, não há o que sincronizar
        if self._direction == 0:
            return

        # Software trailing stop: advance peak, recompute stop, trigger exit if crossed
        if self._ts_algo_id == "sw":
            if self._candles and self._ts_callback_ratio > 0:
                last_close = self._candles[-1].close
                if self._direction == 1:
                    if self._peak_price == 0 or last_close > self._peak_price:
                        self._peak_price = last_close
                else:
                    if self._peak_price == 0 or last_close < self._peak_price:
                        self._peak_price = last_close
                if self._peak_price > 0:
                    new_stop = (
                        self._peak_price * (1 - self._ts_callback_ratio)
                        if self._direction == 1
                        else self._peak_price * (1 + self._ts_callback_ratio)
                    )
                    self._sl_price = new_stop
                    log.info("[AUDITORIA Bot %d] SW-TS: peak=%.4f stop=%.4f",
                             self.config.id, self._peak_price, self._sl_price)
                    # Persiste o nível avançado do trailing stop para sobreviver a restarts
                    if new_stop > self._last_persisted_sl:
                        self._persist_trailing_stop_level()
                    # Streams de fallback podem renderizar somente candles fechados.
                    # never runs — check the trigger here on every poll cycle instead.
                    stop_hit = (
                        (self._direction == 1  and last_close <= new_stop) or
                        (self._direction == -1 and last_close >= new_stop)
                    )
                    if stop_hit and not self._exit_ord_id:
                        self._exit_ord_id = "sw_pending"
                        log.info("[Bot %d] SW-TS TRIGGERED via sync: price=%.4f stop=%.4f",
                                 self.config.id, last_close, new_stop)
                        asyncio.create_task(self._sw_exit(last_close, exchange))
            return

        try:
            # 1. Recupera o ID se não tivermos um, ou se for o marcador de restart
            if not self._ts_algo_id or self._ts_algo_id == "recovered_after_restart":
                params = {"ordType": "move_order_stop", "instId": self.config.symbol}
                data = await exchange._get("/api/v5/trade/orders-algo-pending", params)
                orders = data.get("data", [])
                if orders:
                    self._ts_algo_id = orders[0]["algoId"]
                    log.info("[AUDITORIA Bot %d] ID recuperado: %s", self.config.id, self._ts_algo_id)
                else:
                    log.warning("[AUDITORIA Bot %d] Nenhuma ordem encontrada na exchange. Verificando necessidade de criar nova...", self.config.id)
                    # Não damos return aqui. Seguimos para a lógica de reset/criação abaixo.

            # 2. Sincronização: busca o stop real na exchange e rastreia se a ordem existe
            pnl_pct = (self._candles[-1].close - self._entry_price) / self._entry_price * self._direction if self._candles else 0
            order_confirmed_on_exchange = False
            is_unprotected = True

            if self._ts_algo_id and self._ts_algo_id != "recovered_after_restart":
                algo = await exchange.get_algo_order(self.config.symbol, self._ts_algo_id)
                if algo:
                    order_confirmed_on_exchange = True
                    # move_order_stop usa moveTriggerPx (preço corrente do trailing)
                    # conditional usa slTriggerPx (SL fixo) — fallback para compatibilidade
                    move_px = float(algo.get("moveTriggerPx") or 0)
                    sl_px   = float(algo.get("slTriggerPx") or 0)
                    real_sl = move_px if move_px > 0 else sl_px
                    if real_sl > 0:
                        self._sl_price = real_sl
                        log.info("[AUDITORIA Bot %d] Stop oficial sincronizado: %.4f", self.config.id, real_sl)
                    cb_ratio = float(algo.get("callbackRatio") or 0)
                    if cb_ratio > 0:
                        self._ts_callback_ratio = cb_ratio
                        log.info("[AUDITORIA Bot %d] Campos do stop: moveTriggerPx=%s slTriggerPx=%s callbackRatio=%s activePx=%s state=%s",
                             self.config.id,
                             algo.get("moveTriggerPx"), algo.get("slTriggerPx"),
                             algo.get("callbackRatio"), algo.get("activePx"), algo.get("state"))

                    # CONDIÇÃO DE PROTEÇÃO: só recria se a exchange confirma que não há ordem ativa.
            # NÃO cancela com base em _sl_price local — evita loop de cancel/recriação.
                    is_unprotected = not order_confirmed_on_exchange
            
            if pnl_pct >= 0.01 and is_unprotected:
                log.critical("[AUDITORIA Bot %d] PROTEÇÃO EXIGIDA! Lucro %.2f%% sem ordem ativa ou com stop travado.", 
                             self.config.id, pnl_pct*100)
                
                # Cancela qualquer lixo que tenha sobrado
                if self._ts_algo_id and self._ts_algo_id != "recovered_after_restart":
                    try: await exchange.cancel_algo(self.config.symbol, self._ts_algo_id)
                    except: pass
                
                # Cria a nova ordem de proteção
                current_price = self._candles[-1].close if self._candles else self._entry_price
                cb = 0.01 # 1% de callback para segurança manual
                
                new_id = await exchange.place_trailing_stop(
                    self.config.symbol, "sell" if self._direction == 1 else "buy", 
                    self._sz, cb, current_price)
                
                if new_id:
                    self._ts_algo_id = new_id
                    self._ts_callback_ratio = cb
                    log.critical("[AUDITORIA Bot %d] PROTEÇÃO CRIADA! Nova ordem: %s", self.config.id, new_id)
                else:
                    err = getattr(exchange, "last_order_error", None) or {}
                    self._record_order_rejection(
                        side="sell" if self._direction == 1 else "buy",
                        order_type="trailing_stop_resync",
                        reason=err.get("message", "Exchange recusou recriação do trailing stop."),
                        status="fallback_attempted",
                        raw_payload=err,
                    )
                    fallback_stop = (
                        max(self._entry_price, current_price * (1 - cb))
                        if self._direction == 1
                        else min(self._entry_price, current_price * (1 + cb))
                    )
                    fallback_id = await exchange.place_stop_loss(
                        self.config.symbol,
                        "sell" if self._direction == 1 else "buy",
                        self._sz,
                        fallback_stop,
                    )
                    if fallback_id:
                        self._sl_algo_id = fallback_id
                        self._sl_price = fallback_stop
                        self._persist_trailing_stop_level()
                        log.critical(
                            "[AUDITORIA Bot %d] FALLBACK SL FIXO CRIADO: %s @ %.4f",
                            self.config.id,
                            fallback_id,
                            fallback_stop,
                        )
                    else:
                        fallback_err = getattr(exchange, "last_order_error", None) or {}
                        self._record_order_rejection(
                            side="sell" if self._direction == 1 else "buy",
                            order_type="stop_loss_fallback_resync",
                            reason=fallback_err.get("message", "Fallback de Stop Loss também recusado."),
                            status="critical",
                            raw_payload=fallback_err,
                        )
                        log.error("[AUDITORIA Bot %d] ERRO FATAL: trailing e fallback SL foram recusados.", self.config.id)
        except Exception as e:
            log.warning("[Bot %d] Erro no Sync Manual: %s", self.config.id, e)

    async def _process_strategy(self, bar, exchange: BaseExchange, session=None):
        # Atualiza grafo se a estratégia precisar
        if getattr(self.strategy, 'needs_graph_context', False) and session:
            await self._update_graph_context(session)
            # Carimba o epoch da vela fechada — frontend usa para habilitar o botão
            if self._graph_state:
                self._graph_state['last_candle_ts'] = bar.epoch

            # Alimenta o slot de sentiment com a surpresa do calendário económico.
            if hasattr(self.strategy, 'update_sentiment'):
                surprise = calendar_feed.get_surprise_score()
                if surprise is not None:
                    self.strategy.update_sentiment(surprise)
                    log.info("[Bot %d] Calendar sentiment aplicado ao grafo: %+.3f",
                             self.config.id, surprise)

        # Atualiza candles extras para estratégias multi-TF
        if self.strategy.__class__.extra_timeframes():
            await self._update_extra_candles(exchange)

        if getattr(self.strategy, 'needs_onchain_context', False) and session:
            await self._update_onchain_context(session)

        if getattr(self.strategy, 'needs_dex_context', False) and session:
            await self._update_dex_context(session, exchange)

        if getattr(self.strategy, 'needs_gex_context', False) and session:
            await self._update_gex_context(session)

        if getattr(self.strategy, 'needs_market_players_context', False) and session:
            await self._update_market_players_context(session)

        ctx    = self._strategy_context()
        result = self.strategy.compute_with_context(self._candles, ctx)
        if result is None:
            return

        self._current_atr     = result.indicators.get("atr", self._current_atr)
        self._hold_reason     = result.hold_reason
        self._last_indicators = self._runtime_indicators(result)

        # Persiste snapshot completo do sinal para análise posterior
        self._last_signal_log_id = self._save_signal_log(result, bar)

        signal_direction = (
            "long" if result.signal == Signal.BUY
            else "short" if result.signal == Signal.SELL
            else None
        )
        order_criteria = await self._evaluate_order_criteria(
            result=result,
            direction=signal_direction,
            price=bar.close,
            exchange=exchange,
            check_exchange_position=signal_direction is not None,
        )

        # Cálculo de Lucro Garantido (Sombra)
        guaranteed_pnl = 0.0
        if self._direction != 0 and self._ts_algo_id:
            # Atualiza o pico
            if self._direction == 1:
                if self._peak_price == 0 or bar.close > self._peak_price:
                    self._peak_price = bar.close
            else:
                if self._peak_price == 0 or bar.close < self._peak_price:
                    self._peak_price = bar.close
            
            # Cálculo do lucro garantido baseado no stop real confirmado pela exchange
            if self._sl_price > 0:
                diff = (self._sl_price - self._entry_price) / self._entry_price * self._direction
                guaranteed_pnl = max(0, diff) * 100

        # Broadcast de indicadores
        await self._broadcast({
            "type":        "indicators",
            "bot_id":      self.config.id,
            "indicators":  self._last_indicators,
            "signal":      result.signal,
            "hold_reason": result.hold_reason,
            "criteria_met": getattr(result, "criteria_met", 0),
            "criteria_total": getattr(result, "criteria_total", 0),
            "order_criteria": order_criteria,
            "guaranteed_pnl": round(guaranteed_pnl, 2),
            "ts":          bar.epoch,
        })

        if self._halted:
            self._hold_reason = "Circuit Breaker: Bot pausado (Stop Loss atingido)"
            return
        if self._direction != 0:
            self._hold_reason = f"Em operação: { 'LONG' if self._direction == 1 else 'SHORT' }"
            return

        if signal_direction and not order_criteria.get("ready"):
            log.info(
                "[Bot %d] SINAL %s BLOQUEADO POR CRITÉRIO DE ORDEM: %s",
                self.config.id,
                signal_direction.upper(),
                order_criteria.get("reason", "bloqueio operacional"),
            )
            return

        if result.signal == Signal.BUY:
            log.info("[Bot %d] SINAL LONG | %s", self.config.id, result.indicators)
            await self._enter("long", bar.close, result, exchange,
                              self._last_signal_log_id)
        elif result.signal == Signal.SELL:
            log.info("[Bot %d] SINAL SHORT | %s", self.config.id, result.indicators)
            await self._enter("short", bar.close, result, exchange,
                              self._last_signal_log_id)

    # ── Entrada ───────────────────────────────────────────────────────────────

    async def _enter(self, direction: str, price: float, result, exchange: BaseExchange,
                     signal_log_id: Optional[int] = None):
        order_gate = await self._evaluate_order_criteria(
            result=result,
            direction=direction,
            price=price,
            exchange=exchange,
            check_exchange_position=True,
        )
        if not order_gate.get("ready"):
            log.info(
                "[Bot %d] Entrada %s não enviada — critério de ordem bloqueou: %s",
                self.config.id,
                direction.upper(),
                order_gate.get("reason", "bloqueio operacional"),
            )
            await self._broadcast({
                "type":        "indicators",
                "bot_id":      self.config.id,
                "indicators":  self._runtime_indicators(result),
                "signal":      "hold",
                "hold_reason": order_gate.get("reason", self._hold_reason),
                "criteria_met": getattr(result, "criteria_met", 0),
                "criteria_total": getattr(result, "criteria_total", 0),
                "order_criteria": order_gate,
                "ts":          int(datetime.now(timezone.utc).timestamp() * 1000),
            })
            return

        self._entry_inflight = True
        # Bloqueia Short em SPOT (não há margem/borrow no modo cash)
        is_deriv = ("-SWAP" in self.config.symbol.upper() or "-FUTURES" in self.config.symbol.upper())
        if direction == "short" and not is_deriv:
            self._hold_reason = "Mercado SPOT: Apenas sinais de Compra permitidos."
            self._entry_inflight = False
            log.warning("[Bot %d] Bloqueio SPOT: Tentativa de Short em mercado à vista.", self.config.id)
            await self._broadcast({
                "type":        "indicators",
                "bot_id":      self.config.id,
                "indicators":  self._runtime_indicators(result),
                "signal":      "hold",
                "hold_reason": self._hold_reason,
                "criteria_met": 2, 
                "criteria_total": 3,
                "order_criteria": self._last_order_criteria,
                "ts":          int(datetime.now(timezone.utc).timestamp() * 1000),
            })
            return
        # ── Gate do calendário económico ──────────────────────────────────────
        # Suspende entradas nas estratégias de prioridade crítica durante janelas
        # de eventos macro High-impact (Bollinger Breakout e Graph Regime são as
        # mais expostas a fake-outs gerados por anúncios do Fed, CPI, Payroll, etc.)
        in_window, event_name = calendar_feed.is_high_impact_window(self.config.strategy_id)
        if in_window:
            log.info("[Bot %d] ENTRADA BLOQUEADA — calendário: '%s'",
                     self.config.id, event_name)
            self._hold_reason = f"Calendário: {event_name}"
            self._entry_inflight = False
            await self._broadcast({
                "type":        "indicators",
                "bot_id":      self.config.id,
                "indicators":  self._last_indicators,
                "signal":      Signal.HOLD,
                "hold_reason": self._hold_reason,
                "order_criteria": self._last_order_criteria,
                "ts":          int(datetime.now(timezone.utc).timestamp() * 1000),
            })
            return
        # ─────────────────────────────────────────────────────────────────────

        atr = result.indicators.get("atr", price * 0.005)
        sz  = exchange.num_contracts(
            self.config.symbol, price, FIXED_STAKE_USD, self.config.leverage)

        sl_px  = result.metadata.get("sl_price",
            price - atr * 1.5 if direction == "long" else price + atr * 1.5)
        tp1_px = result.metadata.get("tp1_price",
            price * 1.02 if direction == "long" else price * 0.98)

        # Proteção Estatística: Hard Floor de 0.8% para evitar ruído (whipsaw)
        min_sl_dist_abs = price * 0.008
        current_sl_dist = abs(price - sl_px)
        
        if current_sl_dist < min_sl_dist_abs:
            log.warning(f"[Bot {self.config.id}] SL Original apertado ({current_sl_dist/price*100:.2f}%). Ajustando para o mínimo (0.8%).")
            if direction == "long":
                sl_px = price - min_sl_dist_abs
            else:
                sl_px = price + min_sl_dist_abs
                
            original_tp_dist = abs(tp1_px - price)
            original_rr = original_tp_dist / current_sl_dist if current_sl_dist > 0 else 2.0
            new_tp_dist = min_sl_dist_abs * original_rr
            
            if direction == "long":
                tp1_px = price + new_tp_dist
            else:
                tp1_px = price - new_tp_dist
        
        sl_px = round(sl_px, 4)
        tp1_px = round(tp1_px, 4)

        # Proteção Extra: Verifica se já existe posição aberta na exchange antes de entrar
        # Isso evita entradas múltiplas se o estado local estiver dessincronizado
        try:
            pos = await exchange.get_position(self.config.symbol)
            if pos and pos.size != 0:
                if self._direction != 0:
                    self._hold_reason = "Bloqueio: Posição já aberta na exchange."
                    log.warning("[Bot %d] Bloqueio de reentrada: Posição detectada na exchange (%.4f %s @ %f).", 
                                self.config.id, pos.size, pos.side, pos.avg_price)
                    self._direction = 1 if pos.side == "long" else -1
                    self._sz = abs(pos.size)
                    self._sz_remaining = self._sz
                    self._entry_price = pos.avg_price
                    self._save_trade(
                        {"size": self._sz, "entry_price": self._entry_price},
                        update_id=self._current_trade_id,
                    )
                else:
                    # Banco FLAT mas OKX tem posição — adotar agora para não ficar bloqueado.
                    adopted_dir = 1 if pos.side == "long" else -1
                    adopted_sz  = abs(pos.size)
                    adopted_px  = pos.avg_price or 0.0
                    fallback_sl = round(
                        adopted_px * (1 - 0.05) if adopted_dir == 1
                        else adopted_px * (1 + 0.05), 6
                    )
                    self._direction    = adopted_dir
                    self._sz           = adopted_sz
                    self._sz_remaining = adopted_sz
                    self._entry_price  = adopted_px
                    self._sl_price     = fallback_sl
                    # Calcula TP1 para posição órfã (2% de lucro como padrão conservador)
                    orphan_tp1 = adopted_px * (1.02 if adopted_dir == 1 else 0.98)
                    self._tp1_price    = orphan_tp1
                    self._hold_reason  = "Posição órfã adotada — gerenciando."
                    log.warning(
                        "[Bot %d] Posição órfã adotada em _enter: %.4f %s @ %.4f. SL conservador %.4f. TP1 em %.4f.",
                        self.config.id, adopted_sz, pos.side, adopted_px, fallback_sl, orphan_tp1,
                    )
                    trade_id = self._save_trade({
                        "type":        "entry",
                        "event":       "ORPHAN_ADOPTED",
                        "direction":   "LONG" if adopted_dir == 1 else "SHORT",
                        "size":        adopted_sz,
                        "entry_price": adopted_px,
                        "sl_price":    fallback_sl,
                        "tp1_price":   orphan_tp1,
                        "source":      "orphan_recovery",
                    })
                    self._current_trade_id = trade_id
                self._entry_inflight = False
                return
        except Exception as e:
            log.error("[Bot %d] Erro ao validar posição na exchange: %s", self.config.id, e)

        # Verifica horário de mercado para stocks (crypto é 24/7)
        _sym_upper = self.config.symbol.upper()
        _is_crypto_sym = "/" in _sym_upper or "-" in _sym_upper
        if not _is_crypto_sym:
            try:
                clock = await exchange.get_clock()
                if not clock.get("is_open", True):
                    self._hold_reason = "Mercado fechado (fora do horário de operação)."
                    log.info("[Bot %d] Entrada bloqueada — mercado fechado. Próxima abertura: %s",
                             self.config.id, clock.get("next_open", "?"))
                    self._entry_inflight = False
                    return
            except Exception as _ce:
                log.warning("[Bot %d] Falha ao verificar clock de mercado: %s", self.config.id, _ce)

        entry_side = "buy" if direction == "long" else "sell"
        close_side = "sell" if direction == "long" else "buy"
        if self._is_spot_symbol(self.config.symbol) and direction != "long":
            self._hold_reason = "Mercado spot sem alavancagem permite apenas compra/fechamento de saldo."
            self._entry_inflight = False
            return

        try:
            ord_id = await exchange.market_order(self.config.symbol, entry_side, sz)
        except Exception as exc:
            ord_id = None
            self._entry_inflight = False
            exchange.last_order_error = {
                "kind": "order_exception",
                "message": str(exc),
            }
        if not ord_id:
            err = getattr(exchange, "last_order_error", None) or {
                "kind": "order_rejected",
                "message": "Ordem recusada pela exchange.",
            }
            suggested = self.config.symbol.replace("-SWAP", "").replace("-FUTURES", "")
            self._last_order_error = {
                **err,
                "symbol": self.config.symbol,
                "suggested_symbol": suggested,
            }
            self._record_order_rejection(
                side=entry_side,
                order_type="market",
                reason=err.get("message", "Ordem recusada pela exchange."),
                status="rejected",
                raw_payload=err,
            )
            # Notifica via WebSocket (UI) com nome do bot para o toast
            await self._broadcast({
                "type":     "order_error",
                "bot_id":   self.config.id,
                "bot_name": self.config.name,
                "symbol":   self.config.symbol,
                "direction": direction,
                "error":    self._last_order_error,
            })
            # Notifica via Telegram com detalhes do erro
            asyncio.create_task(self._notifier.send(build_order_failed_msg(
                bot_name  = self.config.name,
                demo      = self.config.demo,
                symbol    = self.config.symbol,
                direction = direction,
                error_msg = err.get("message", "Erro desconhecido"),
                sz        = sz,
                price     = price,
            )))
            self._entry_inflight = False
            return
        self._last_order_error = None

        price, sz = await self._sync_entry_fill_from_exchange(exchange, ord_id, price, sz)
        if sz <= 0:
            self._record_order_rejection(
                side=entry_side,
                order_type="market",
                reason="OKX não confirmou saldo da posição após ordem de entrada.",
                status="critical",
                raw_payload={"order_id": ord_id},
            )
            self._entry_inflight = False
            return

        sl_id = await exchange.place_stop_loss(self.config.symbol, close_side, sz, sl_px)
        if not sl_id:
            err = getattr(exchange, "last_order_error", None) or {}
            self._record_order_rejection(
                side=close_side,
                order_type="stop_loss",
                reason=err.get("message", "Stop Loss fixo recusado pela exchange."),
                status="critical",
                raw_payload=err,
            )

        self._direction    = 1 if direction == "long" else -1
        self._entry_price  = price
        self._sz           = sz
        self._sz_remaining = sz
        self._tp1_price    = tp1_px
        self._sl_price     = sl_px
        self._tp1_done     = False
        self._sl_algo_id   = sl_id
        self._entry_ord_id = ord_id # Armazenamos o ID de entrada
        self._entry_inflight = False

        # Persiste no banco e linka o signal_log que originou esta entrada
        trade_id = self._save_trade({
            "type": "entry", "direction": direction.upper(),
            "size": sz, "entry_price": price,
            "sl_price": sl_px, "tp1_price": tp1_px, "atr": atr,
        })
        self._current_trade_id = trade_id
        if signal_log_id and trade_id:
            self._link_signal_to_trade(signal_log_id, trade_id)

        # Confirma o preço real de fill em background (market orders podem demorar para executar)
        asyncio.create_task(self._confirm_fill_price(ord_id, exchange))

        await self._broadcast({
            "type":      "trade",
            "bot_id":    self.config.id,
            "event":     "entry",
            "direction": direction,
            "price":     price,
            "sz":        sz,
            "sl":        sl_px,
            "tp1":       tp1_px,
        })

        asyncio.create_task(self._notifier.send(build_entry_msg(
            bot_name      = self.config.name,
            strategy_id   = self.config.strategy_id,
            symbol        = self.config.symbol,
            timeframe     = self.config.timeframe,
            leverage      = self.config.leverage,
            stake_usd     = FIXED_STAKE_USD,
            demo          = self.config.demo,
            stop_loss_usd = self.config.stop_loss_usd,
            direction     = direction,
            entry_price   = price,
            sl_price      = sl_px,
            tp1_price     = tp1_px,
            sz            = sz,
            indicators    = result.indicators,
            daily_pnl     = self._daily_pnl,
            wins          = self._wins,
            losses        = self._losses,
        )))

    # ── TP1 + Trailing ────────────────────────────────────────────────────────

    async def _check_tp1(self, price: float, exchange: BaseExchange):
        if self._tp1_done: return
        
        pnl_pct = (price - self._entry_price) / self._entry_price
        if self._direction == -1: pnl_pct = -pnl_pct
        
        # Gatilho de Ativação: +1.0% de PnL líquido
        if pnl_pct >= 0.01:
            self._tp1_done = True
            close_side = "sell" if self._direction == 1 else "buy"
            
            # Cancela a proteção de Stop Loss "burra" (estática)
            if self._sl_algo_id:
                await exchange.cancel_algo(self.config.symbol, self._sl_algo_id)
                self._sl_algo_id = None

            # Ativa a Sombra Dinâmica (Trailing Stop 100% dinâmico) para toda a posição!
            # A sombra manterá uma distância baseada no ATR (volatilidade real) do momento
            min_cb = 0.005 # Nunca menor que 0.5%
            atr_cb = (self._current_atr * 1.5) / price
            cb = max(min_cb, atr_cb)
            
            log.info("[Bot %d] DYNAMIC SHADOW TRIGGER ativado! Lucro: %.2f%%. Callback dinâmico: %.2f%%", 
                     self.config.id, pnl_pct*100, cb*100)
            
            ts_id = await exchange.place_trailing_stop(
                self.config.symbol, close_side, self._sz, cb, price)
            self._ts_algo_id = ts_id
            self._ts_callback_ratio = cb
            if not ts_id:
                err = getattr(exchange, "last_order_error", None) or {}
                self._record_order_rejection(
                    side=close_side,
                    order_type="trailing_stop",
                    reason=err.get("message", "Trailing stop nativo recusado pela exchange."),
                    status="fallback_attempted",
                    raw_payload=err,
                )
                fallback_stop = (
                    max(self._entry_price, price * (1 - cb))
                    if self._direction == 1
                    else min(self._entry_price, price * (1 + cb))
                )
                fallback_id = await exchange.place_stop_loss(
                    self.config.symbol,
                    close_side,
                    self._sz,
                    fallback_stop,
                )
                if fallback_id:
                    self._sl_algo_id = fallback_id
                    self._sl_price = fallback_stop
                    log.warning(
                        "[Bot %d] Trailing recusado; Stop Loss fixo fallback criado: %s @ %.4f",
                        self.config.id,
                        fallback_id,
                        fallback_stop,
                    )
                else:
                    fallback_err = getattr(exchange, "last_order_error", None) or {}
                    self._record_order_rejection(
                        side=close_side,
                        order_type="stop_loss_fallback",
                        reason=fallback_err.get("message", "Fallback de Stop Loss recusado pela exchange."),
                        status="critical",
                        raw_payload=fallback_err,
                    )
            # For software TS: set peak and initial stop immediately so get_status()
            # reflects the correct values on the same poll cycle.
            # yields closed=True, so the intracandle block never runs).
            if ts_id == "sw":
                self._peak_price = price
                self._sl_price = (price * (1 - cb) if self._direction == 1
                                  else price * (1 + cb))
            
            self._save_trade({
                "type": "tp1", "event": "DYNAMIC_SHADOW_TRIGGER",
                "pnl": 0.0,   # Lucro não é realizado agora, a sombra garantiu o breakeven
                "sl_price": self._sl_price,   # Nível inicial da sombra, não o SL estático da entrada
            }, update_id=self._current_trade_id)
            # Persiste imediatamente o nível do trailing stop no entry record
            self._persist_trailing_stop_level()

            await self._broadcast({"type": "trade", "bot_id": self.config.id,
                                   "event": "shadow_trigger", "price": price, "pnl": 0.0})

            # Notifica o usuário no Telegram
            direction_str = "long" if self._direction == 1 else "short"
            try:
                msg = (
                    f"🛡️ <b>DYNAMIC SHADOW TRIGGER ATIVADO</b> 🛡️\n\n"
                    f"🤖 Bot: {self.config.name} ({self.config.symbol})\n"
                    f"📈 Lucro Atual: +{pnl_pct*100:.2f}%\n"
                    f"🎯 Sombra (Trailing): {cb*100:.2f}% de distância da máxima\n\n"
                    f"O Stop Loss inicial foi cancelado. Uma sombra dinâmica foi acoplada ao preço. "
                    f"A partir de agora, é matematicamente impossível perder dinheiro nesta operação."
                )
                asyncio.create_task(self._notifier.send(msg))
            except:
                pass

    # ── P&L ──────────────────────────────────────────────────────────────────

    def _record_pnl(self, pnl: float):
        self._daily_pnl += pnl
        if pnl > 0:
            self._wins += 1
        else:
            self._losses += 1
        if self._daily_pnl <= self.config.stop_loss_usd:
            self._halted = True
            log.warning("[Bot %d] CIRCUIT BREAKER: P&L diário %.2f",
                        self.config.id, self._daily_pnl)
            asyncio.create_task(self._notifier.send(build_circuit_breaker_msg(
                bot_name      = self.config.name,
                demo          = self.config.demo,
                symbol        = self.config.symbol,
                daily_pnl     = self._daily_pnl,
                stop_loss_usd = self.config.stop_loss_usd,
                wins          = self._wins,
                losses        = self._losses,
            )))

    def _reset(self):
        self._direction = self._sz = self._sz_remaining = 0
        self._entry_price = self._tp1_price = self._sl_price = 0.0
        self._tp1_done = False
        self._sl_algo_id = self._ts_algo_id = None
        self._ts_callback_ratio = 0.0
        self._peak_price = 0.0
        self._last_persisted_sl = 0.0

    # ── Persistência ──────────────────────────────────────────────────────────

    def _persist_trailing_stop_level(self):
        """
        Salva o nível atual do trailing stop (SW) no registro de entrada do banco.
        Chamado sempre que _sl_price avança para garantir que restarts recuperem
        o nível correto — e não apenas o SL original da entrada.
        """
        if not self._current_trade_id or self._sl_price <= 0:
            return
        db = SessionLocal()
        try:
            trade = db.query(TradeModel).filter(
                TradeModel.id == self._current_trade_id
            ).first()
            if trade:
                trade.sl_price = round(self._sl_price, 6)
                db.commit()
                self._last_persisted_sl = self._sl_price
        except Exception as exc:
            log.debug("[Bot %d] Falha ao persistir trailing stop: %s", self.config.id, exc)
        finally:
            db.close()

    def _record_order_rejection(
        self,
        *,
        side: str | None = None,
        order_type: str | None = None,
        reason: str | None = None,
        ord_id: str | None = None,
        algo_id: str | None = None,
        status: str = "open",
        raw_payload: dict | None = None,
    ) -> None:
        db = SessionLocal()
        try:
            db.add(OrderRejectionModel(
                bot_id=self.config.id,
                bot_name=self.config.name,
                symbol=self.config.symbol,
                side=side,
                order_type=order_type,
                ord_id=ord_id,
                algo_id=algo_id,
                status=status,
                reason=reason,
                raw_payload=raw_payload or {},
            ))
            db.commit()
        except Exception as exc:
            log.warning("[Bot %d] Falha ao persistir rejeição de ordem: %s", self.config.id, exc)
        finally:
            db.close()

    def _save_trade(self, data: dict, update_id: Optional[int] = None) -> Optional[int]:
        db = SessionLocal()
        try:
            # Para trades de saída (exit), criamos um NOVO registro vinculado ao trade de entrada
            # para que o histórico completo (entry → tp1 → exit) apareça no gráfico
            if data.get("type") == "exit":
                # Busca o trade de entrada aberto para copiar dados base
                entry_trade = None
                if update_id:
                    entry_trade = db.query(TradeModel).filter(TradeModel.id == update_id).first()
                if not entry_trade:
                    entry_trade = db.query(TradeModel).filter(
                        TradeModel.bot_id == self.config.id,
                        TradeModel.exit_price.is_(None)
                    ).order_by(TradeModel.id.desc()).first()
                
                if entry_trade:
                    # Atualiza o trade de entrada com o preço de saída e PnL
                    # para que o summary possa calcular corretamente (filtra entry + exit_price not null)
                    entry_trade.exit_price = data.get("exit_price")
                    entry_trade.pnl = data.get("pnl")
                    entry_trade.closed_at = data.get("closed_at")
                    entry_trade.event = data.get("event", entry_trade.event)
                    db.commit()
                    
                    # Cria um novo registro de saída para aparecer no gráfico/histórico
                    exit_trade = TradeModel(
                        bot_id=self.config.id,
                        symbol=self.config.symbol,
                        type="exit",
                        event=data.get("event", "EXIT"),
                        direction=entry_trade.direction,
                        size=entry_trade.size,
                        entry_price=entry_trade.entry_price,
                        exit_price=data.get("exit_price"),
                        sl_price=entry_trade.sl_price,
                        tp1_price=entry_trade.tp1_price,
                        pnl=data.get("pnl"),
                        closed_at=data.get("closed_at"),
                        source=data.get("source", "bot"),
                    )
                    db.add(exit_trade)
                    db.commit()
                    db.refresh(exit_trade)
                    return exit_trade.id
            
            # Para TP1 (Dynamic Shadow Trigger), criamos um registro separado
            if data.get("type") == "tp1":
                entry_trade = None
                if update_id:
                    entry_trade = db.query(TradeModel).filter(TradeModel.id == update_id).first()
                if not entry_trade:
                    entry_trade = db.query(TradeModel).filter(
                        TradeModel.bot_id == self.config.id,
                        TradeModel.exit_price.is_(None)
                    ).order_by(TradeModel.id.desc()).first()
                
                if entry_trade:
                    # Marca a sombra como ativa no registro de entrada para sobreviver a restarts
                    entry_trade.tp1_done = True
                    # Cria registro separado de TP1 para o gráfico
                    tp1_trade = TradeModel(
                        bot_id=self.config.id,
                        symbol=self.config.symbol,
                        type="tp1",
                        event=data.get("event", "TP1"),
                        direction=entry_trade.direction,
                        size=entry_trade.size,
                        entry_price=entry_trade.entry_price,
                        # Usa o nível inicial do trailing stop (passado via data["sl_price"])
                        # em vez do SL estático da entrada — mais preciso para análise histórica.
                        sl_price=data.get("sl_price", entry_trade.sl_price),
                        tp1_price=entry_trade.tp1_price,
                        pnl=data.get("pnl", 0.0),
                        source=data.get("source", "bot"),
                    )
                    db.add(tp1_trade)
                    db.commit()
                    db.refresh(tp1_trade)
                    return tp1_trade.id

            if update_id:
                # Tenta atualizar um trade existente
                trade = db.query(TradeModel).filter(TradeModel.id == update_id).first()
                if trade:
                    for k, v in data.items():
                        setattr(trade, k, v)
                    db.commit()
                    return trade.id

            # Criação de um novo registro (normalmente para "entry")
            trade = TradeModel(bot_id=self.config.id,
                               symbol=self.config.symbol, **data)
            db.add(trade)
            db.commit()
            db.refresh(trade)
            return trade.id
        except Exception as e:
            log.error("Erro ao salvar trade: %s", e)
            db.rollback()
            return None
        finally:
            db.close()

    def _recover_state(self):
        """Tenta recuperar o estado de uma posição aberta do banco de dados."""
        db = SessionLocal()
        try:
            # Usa o registro de ENTRADA (type="entry") como fonte da verdade para recuperação.
            # Antes usávamos order_by(id.desc()) que retornava o registro TP1 — cujo sl_price
            # era copiado do entry no momento do trigger e nunca atualizado posteriormente.
            # O entry record tem sl_price atualizado pelo _persist_trailing_stop_level.
            last_trade = db.query(TradeModel).filter(
                TradeModel.bot_id == self.config.id,
                TradeModel.exit_price.is_(None),
                TradeModel.type == "entry",
            ).order_by(TradeModel.id.desc()).first()
            
            if last_trade:
                self._direction    = 1 if last_trade.direction == "LONG" else -1
                self._entry_price  = last_trade.entry_price or 0.0
                self._sz           = last_trade.size or 0.0
                self._sz_remaining = self._sz
                self._tp1_price    = last_trade.tp1_price or 0.0
                self._sl_price     = last_trade.sl_price or 0.0
                self._current_trade_id = last_trade.id
                # Restaura estado da Sombra Dinâmica — sempre que houver posição aberta,
                # independente de TP1 ter sido atingido ou não. O trailing stop é proteção
                # primária, não secundária ao TP1.
                self._tp1_done          = bool(last_trade.tp1_done)
                self._ts_algo_id        = "sw"
                self._ts_callback_ratio = 0.005  # mínimo seguro; force_sync_trailing recalcula
                if self._sl_price > 0 and self._ts_callback_ratio > 0:
                    if self._direction == 1:
                        self._peak_price = self._sl_price / (1 - self._ts_callback_ratio)
                    else:
                        self._peak_price = self._sl_price / (1 + self._ts_callback_ratio)
                # Marca o nível já persistido para evitar re-escrita desnecessária
                self._last_persisted_sl = self._sl_price
                log.info("[Bot %d] Estado recuperado do banco: %s @ %.2f (Trade ID: %d) — Sombra reativada (sl=%.4f peak=%.4f tp1_done=%s)",
                         self.config.id, last_trade.direction, self._entry_price,
                         self._current_trade_id, self._sl_price, self._peak_price,
                         self._tp1_done)
        except Exception as e:
            log.error("[Bot %d] Falha ao recuperar estado: %s", self.config.id, e)
        finally:
            db.close()

    def _save_signal_log(self, result, bar) -> Optional[int]:
        db = SessionLocal()
        try:
            from datetime import timezone as _tz
            ts = datetime.fromtimestamp(bar.epoch / 1000, tz=_tz.utc).replace(tzinfo=None)
            entry = SignalLogModel(
                bot_id=self.config.id,
                timestamp=ts,
                signal=result.signal.value,
                hold_reason=result.hold_reason or None,
                indicators=dict(result.indicators) if result.indicators else {},
                meta=dict(result.metadata) if result.metadata else {},
                candle_open=bar.open,
                candle_high=bar.high,
                candle_low=bar.low,
                candle_close=bar.close,
                candle_volume=bar.volume,
            )
            db.add(entry)
            db.commit()
            db.refresh(entry)
            return entry.id
        except Exception as e:
            log.error("Erro ao salvar signal_log: %s", e)
            db.rollback()
            return None
        finally:
            db.close()

    def _link_signal_to_trade(self, signal_log_id: int, trade_id: int):
        db = SessionLocal()
        try:
            entry = db.get(SignalLogModel, signal_log_id)
            if entry:
                entry.resulted_in_trade_id = trade_id
                db.commit()
        except Exception as e:
            log.error("Erro ao linkar signal_log→trade: %s", e)
            db.rollback()
        finally:
            db.close()

    # ── Status público ────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        # Preço atual vindo do último candle
        last_price = self._candles[-1].close if self._candles else 0.0
        
        # Se não tem candles ainda, retorna status básico
        if not self._candles:
            return {
                "bot_id":      self.config.id,
                "status":      self.status,
                "direction":   0,
                "size":        0.0,
                "entry_price": 0.0,
                "last_price":  0.0,
                "peak_price":  0.0,
                "sl_price":    0.0,
                "tp1_price":   0.0,
                "tp1_done":    False,
                "guaranteed_pnl": 0.0,
                "daily_pnl":   round(self._daily_pnl, 2),
                "wins":        self._wins,
                "losses":      self._losses,
                "halted":      self._halted,
                "hold_reason": self._hold_reason,
                "last_indicators": self._last_indicators,
                "order_criteria":  self._last_order_criteria,
                "last_order_error": self._last_order_error,
                "maintenance":     self._maintenance,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }
        
        # PnL Garantido — só válido quando o SW-TS está ativo (tp1_done=True).
        # Antes do TP1, _sl_price é o SL inicial (abaixo da entrada para LONG),
        # então diff seria negativo e garantido seria 0. Mas se o sync sobrescreveu
        # _entry_price com um contexto diferente, diff pode ser positivo erroneamente.
        # Garantir que somente o trailing stop ativo gere guaranteed_pnl > 0.
        guaranteed_pnl = 0.0
        if self._direction != 0 and self._sl_price > 0 and self._tp1_done:
            diff = (self._sl_price - self._entry_price) / self._entry_price * self._direction
            guaranteed_pnl = max(0, diff) * 100

        return {
            "bot_id":      self.config.id,
            "status":      self.status,
            "direction":   self._direction,
            "size":        self._sz,
            "entry_price": self._entry_price,
            "last_price":  last_price,
            "peak_price":  self._peak_price,
            "sl_price":    self._sl_price,
            "tp1_price":   self._tp1_price,
            "tp1_done":    self._tp1_done,
            "guaranteed_pnl": round(guaranteed_pnl, 2),
            "daily_pnl":   round(self._daily_pnl, 2),
            "wins":        self._wins,
            "losses":      self._losses,
            "halted":      self._halted,
            "hold_reason":     self._hold_reason,
            "last_indicators": self._last_indicators,
            "order_criteria":  self._last_order_criteria,
            "last_order_error": self._last_order_error,
            "maintenance":     self._maintenance,
            "started_at":      self._started_at,
            "last_price":      self._candles[-1].close if self._candles else 0.0,
            "graph_state":     self._graph_state,
            "onchain_events":  self._onchain_events,
        }

    # ── Monitoramento de Ordens em Tempo Real ─────────────────────────────────

    async def _monitor_orders(self, exchange: BaseExchange):
        private_stream_cls = get_private_stream_class()
        failures = 0
        while True:
            try:
                stream = private_stream_cls(exchange)
                async for event in stream.iter():
                    failures = 0
                    await self._handle_order_event(event, exchange)
            except asyncio.CancelledError:
                break
            except Exception as e:
                failures += 1
                err = str(e)
                delay = 15
                if "403" in err or "server rejected WebSocket connection" in err:
                    delay = min(300, 30 * failures)
                    log.warning(
                        "[Bot %d] WS Privado OKX recusado (%s). "
                        "Bot continua com confirmação REST; nova tentativa em %ds.",
                        self.config.id,
                        err,
                        delay,
                    )
                else:
                    log.warning("[Bot %d] WS Privado falhou: %s. Reconectando em %ds...",
                                self.config.id, e, delay)
                await asyncio.sleep(delay)

    async def _handle_order_event(self, event: dict, exchange: BaseExchange):
        """Processa avisos de execução da exchange."""
        state = event.get("state")
        side  = event.get("side")
        ord_id  = event.get("ordId")
        algo_id = event.get("algoId")
        inst_id = event.get("instId")
        source = str(event.get("source") or "")

        # Só nos interessamos por execuções
        if state not in ("filled", "effective"):
            return

        if inst_id and inst_id != self.config.symbol:
            return

        # Verifica se esta ordem/algo pertence a este bot. Na OKX, uma ordem normal
        # pode ser gerada pelo algo de SL/trailing e chegar só com ordId/source.
        is_ours = (ord_id and ord_id in (self._entry_ord_id, self._tp1_ord_id, self._exit_ord_id)) or \
                  (algo_id and algo_id in (self._sl_algo_id, self._ts_algo_id)) or \
                  (source in ("7", "13", "25") and self._direction != 0)

        if not is_ours:
            return

        px = float(event.get("fillPx") or event.get("avgPx") or 0)
        sz = float(event.get("fillSz") or event.get("sz") or 0)

        if not px or not sz: return

        log.info("[Bot %d] EVENTO: Ordem %s executada a %.2f (ID: %s)",
                 self.config.id, side, px, ord_id or algo_id)

        # Entrada: fill na mesma direção que a posição atual
        if ord_id and ord_id == self._entry_ord_id:
            entry_side_matches = (
                (self._direction == 1  and side == "buy") or
                (self._direction == -1 and side == "sell")
            )
            if entry_side_matches:
                actual_size = await self._entry_size_from_exchange(exchange, sz)
                if actual_size > 0 and abs(actual_size - abs(self._sz)) > max(1e-8, abs(self._sz) * 0.001):
                    log.info(
                        "[Bot %d] WS: tamanho real da entrada %.8f (estimado %.8f) — corrigindo.",
                        self.config.id, actual_size, self._sz,
                    )
                    self._sz = actual_size
                    self._sz_remaining = actual_size
                    self._save_trade({"size": actual_size}, update_id=self._current_trade_id)
                if abs(px - self._entry_price) > 0.001:
                    log.info("[Bot %d] WS: entry fill real $%.4f (estimado $%.4f) — corrigindo.",
                             self.config.id, px, self._entry_price)
                    self._entry_price = px
                    self._save_trade({"entry_price": px}, update_id=self._current_trade_id)
                direction_str = "long" if self._direction == 1 else "short"
                await self._broadcast({
                    "type":         "order_confirmed",
                    "bot_id":       self.config.id,
                    "bot_name":     self.config.name,
                    "symbol":       self.config.symbol,
                    "direction":    direction_str,
                    "order_id":     ord_id,
                    "filled_price": px,
                    "qty":          self._sz,
                    "sl_price":     self._sl_price,
                    "tp1_price":    self._tp1_price,
                })
                asyncio.create_task(self._notifier.send(build_order_confirmed_msg(
                    bot_name     = self.config.name,
                    demo         = self.config.demo,
                    symbol       = self.config.symbol,
                    direction    = direction_str,
                    order_id     = ord_id,
                    filled_price = px,
                    sz           = self._sz,
                    sl_price     = self._sl_price,
                    tp1_price    = self._tp1_price,
                )))
                return

        # Saída: fill na direção oposta à posição atual
        if (self._direction == 1 and side == "sell") or \
           (self._direction == -1 and side == "buy"):

            # Se for SL/TP final ou Trailing (não o TP1 parcial)
            if ord_id != self._tp1_ord_id:
                await self._confirm_flat_then_close(px, abs(self._sz), exchange)
            else:
                log.info("[Bot %d] TP1 parcial confirmado via WS.", self.config.id)

    async def _deferred_fee_sync(self, trade_id: int, symbol: str):
        """
        OKX registra taxas diretamente nos fills. Mantido como no-op para
        compatibilidade com chamadas antigas após fechamento.
        """
        return

    async def _on_position_closed(self, price: float, size: float, exchange: BaseExchange):
        """Finaliza o trade e limpa o estado do bot."""
        if self._direction == 0: return

        ct_size = exchange.get_contract_size(self.config.symbol)
        pnl = size * ct_size * (price - self._entry_price) * self._direction
        entry_price_snap = self._entry_price
        direction_snap   = "long" if self._direction == 1 else "short"

        self._record_pnl(pnl)
        self._save_trade({
            "type": "exit", "event": "WS_EXECUTION",
            "exit_price": price, "pnl": pnl,
            "closed_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }, update_id=self._current_trade_id)

        # Captura trade_id e símbolo antes do _reset()
        _trade_id_snap = self._current_trade_id
        _symbol_snap   = self.config.symbol

        log.info("[Bot %d] POSIÇÃO ENCERRADA via exchange | P&L: %.2f", self.config.id, pnl)

        await self._broadcast({
            "type":      "trade",
            "bot_id":    self.config.id,
            "event":     "exit",
            "price":     price,
            "pnl":       pnl,
            "direction": 0
        })

        asyncio.create_task(self._notifier.send(build_exit_msg(
            bot_name      = self.config.name,
            demo          = self.config.demo,
            symbol        = self.config.symbol,
            direction     = direction_snap,
            exit_reason   = "WS_EXECUTION",
            entry_price   = entry_price_snap,
            exit_price    = price,
            pnl           = pnl,
            daily_pnl     = self._daily_pnl,
            stop_loss_usd = self.config.stop_loss_usd,
            wins          = self._wins,
            losses        = self._losses,
        )))

        # Registra corretagem em background (CFEE aparece ~10-60s após o fill)
        if _trade_id_snap:
            asyncio.create_task(self._deferred_fee_sync(_trade_id_snap, _symbol_snap))

        self._reset()

    async def _confirm_fill_price(self, ord_id: str, exchange: BaseExchange):
        """Fallback REST polling para entry fill price caso o WebSocket não chegue a tempo.

        _handle_order_event já captura fills via trade_updates WebSocket e atualiza
        _entry_price + envia broadcast/Telegram em tempo real. Este método serve como
        segurança: tenta por ~5 minutos e desiste (o WebSocket deve ter tratado antes).
        """
        delays = [1, 3, 5, 10, 15, 30, 60]  # ~2 min total; WS deve chegar antes
        for delay in delays:
            await asyncio.sleep(delay)
            if self._direction == 0:
                return  # posição fechada
            if ord_id != self._entry_ord_id:
                return  # nova entrada sobrescreveu — não atualizar com ordem antiga
            try:
                order = await exchange.get_order(ord_id)
                if not order:
                    continue
                status = order.get("status", "")
                if status == "filled":
                    filled_avg = order.get("filled_avg_price")
                    filled_size = order.get("filled_size")
                    if filled_size:
                        actual_size = await self._entry_size_from_exchange(exchange, float(filled_size))
                        if actual_size > 0 and abs(actual_size - abs(self._sz)) > max(1e-8, abs(self._sz) * 0.001):
                            log.info(
                                "[Bot %d] Fill REST: tamanho real %.8f (estimado %.8f) — corrigindo.",
                                self.config.id, actual_size, self._sz,
                            )
                            self._sz = actual_size
                            self._sz_remaining = actual_size
                            self._save_trade({"size": actual_size}, update_id=self._current_trade_id)
                    if filled_avg:
                        actual = float(filled_avg)
                        if abs(actual - self._entry_price) > 0.001:
                            log.info(
                                "[Bot %d] Fill confirmado via REST fallback: $%.4f (estimado: $%.4f)",
                                self.config.id, actual, self._entry_price,
                            )
                            self._entry_price = actual
                            self._save_trade({"entry_price": actual}, update_id=self._current_trade_id)
                            # Broadcast apenas se o WS ainda não enviou (entry_price diferiu)
                            direction_str = "long" if self._direction == 1 else "short"
                            await self._broadcast({
                                "type":         "order_confirmed",
                                "bot_id":       self.config.id,
                                "bot_name":     self.config.name,
                                "symbol":       self.config.symbol,
                                "direction":    direction_str,
                                "order_id":     ord_id,
                                "filled_price": actual,
                                "qty":          self._sz,
                                "sl_price":     self._sl_price,
                                "tp1_price":    self._tp1_price,
                            })
                            asyncio.create_task(self._notifier.send(build_order_confirmed_msg(
                                bot_name     = self.config.name,
                                demo         = self.config.demo,
                                symbol       = self.config.symbol,
                                direction    = direction_str,
                                order_id     = ord_id,
                                filled_price = actual,
                                sz           = self._sz,
                                sl_price     = self._sl_price,
                                tp1_price    = self._tp1_price,
                            )))
                    return
                if status in ("canceled", "expired", "rejected"):
                    log.warning(
                        "[Bot %d] Ordem %s não executada (status: %s) — "
                        "removendo trade não confirmado do banco.",
                        self.config.id, ord_id, status,
                    )
                    # Remove o trade registrado antes da confirmação
                    if self._current_trade_id:
                        _db = SessionLocal()
                        try:
                            _db.query(TradeModel).filter(
                                TradeModel.id == self._current_trade_id
                            ).delete()
                            _db.commit()
                        except Exception as _de:
                            log.error("[Bot %d] Falha ao remover trade não confirmado: %s",
                                      self.config.id, _de)
                        finally:
                            _db.close()
                    await self._broadcast({
                        "type":     "order_error",
                        "bot_id":   self.config.id,
                        "bot_name": self.config.name,
                        "symbol":   self.config.symbol,
                        "error": {
                            "kind":    "order_not_filled",
                            "message": f"Ordem {ord_id} não executada pela OKX: {status}",
                        },
                    })
                    self._reset()
                    self._current_trade_id = None
                    return
            except Exception as exc:
                log.warning("[Bot %d] Erro ao confirmar fill (fallback): %s", self.config.id, exc)

    async def _sw_exit(self, price: float, exchange: BaseExchange):
        """Execute software trailing stop: place market exit, then poll REST to confirm."""
        if self._direction == 0:
            return
        close_side = "sell" if self._direction == 1 else "buy"
        close_size = await self._close_size_from_exchange(exchange)
        if close_size <= 0:
            log.warning("[Bot %d] SW-TS: tamanho de saída zero para %s.", self.config.id, self.config.symbol)
            return
        try:
            ord_id = await exchange.market_order(
                self.config.symbol, close_side, close_size, reduce_only=True)
            self._exit_ord_id = ord_id or "sw_exit"
            log.info("[Bot %d] SW-TS: ordem de saída enviada (ID: %s)", self.config.id, self._exit_ord_id)
        except Exception as e:
            log.error("[Bot %d] SW-TS: falha ao enviar ordem de saída: %s", self.config.id, e)
            self._exit_ord_id = None
            return
        # Poll REST until OKX confirms position closed; use real fill price for PnL.
        # WebSocket (trade_updates) may already call _on_position_closed first — that's fine,
        # _on_position_closed guards against double-execution via self._direction == 0.
        for attempt in range(12):
            await asyncio.sleep(2)
            if self._direction == 0:
                return  # already handled by WebSocket trade_updates
            try:
                # From attempt 2 onwards, query order directly for fill price
                if ord_id and attempt >= 1:
                    order = await exchange.get_order(ord_id)
                    if order and order.get("status") == "filled":
                        exit_price = float(order.get("filled_avg_price") or price)
                        filled_size = float(order.get("filled_size") or close_size)
                        log.info("[Bot %d] SW-TS fill real de saída: $%.4f (estimado: $%.4f)",
                                 self.config.id, exit_price, price)
                        if await self._confirm_flat_then_close(exit_price, filled_size, exchange):
                            return
                        continue
                pos = await exchange.get_position(self.config.symbol)
                if pos is None:
                    exit_price = price
                    if ord_id:
                        try:
                            order = await exchange.get_order(ord_id)
                            if order and order.get("filled_avg_price"):
                                exit_price = float(order["filled_avg_price"])
                        except Exception:
                            pass
                    await self._on_position_closed(exit_price, close_size, exchange)
                    return
            except Exception:
                pass
        log.warning("[Bot %d] SW-TS: posição pode ainda estar aberta após saída", self.config.id)

    async def manual_liquidate(self, exchange: BaseExchange):
        """Fecha a posição a mercado e confirma via REST polling OKX."""
        if self._direction == 0: return

        last_price = self._candles[-1].close if self._candles else self._entry_price
        close_size = await self._close_size_from_exchange(exchange)

        try:
            # 1. Tenta liquidação nativa na exchange
            ord_id = await exchange.liquidate_position(self.config.symbol)
            if not ord_id:
                # 2. Fallback para ordem contrária manual se a liquidação nativa falhar ou não for suportada
                close_side = "sell" if self._direction > 0 else "buy"
                ord_id = await exchange.market_order(
                    symbol=self.config.symbol,
                    side=close_side,
                    size=close_size,
                    reduce_only=True
                )
                if not ord_id:
                    err = getattr(exchange, "last_order_error", None) or {"message": "Erro desconhecido ao liquidar."}
                    raise RuntimeError(f"Ordem de mercado falhou: {err.get('message')}")
            
            self._exit_ord_id = ord_id
            log.info("[Bot %d] Liquidação manual enviada (ID: %s). Polling REST...", self.config.id, ord_id)
        except Exception as e:
            log.warning("[Bot %d] Falha ao liquidar na exchange: %s", self.config.id, e)
            self._save_trade({
                "type": "exit", "event": "MANUAL_FAILED",
                "exit_price": self._entry_price,
                "pnl": 0.0
            }, update_id=self._current_trade_id)
            self._reset()
            return

        # Polling REST até confirmar fechamento (mesmo padrão do _sw_exit).
        # WebSocket pode chegar primeiro — _on_position_closed guarda contra duplicação.
        for attempt in range(12):
            await asyncio.sleep(2)
            if self._direction == 0:
                return  # already handled by WebSocket trade_updates
            try:
                if ord_id and attempt >= 1:
                    order = await exchange.get_order(ord_id)
                    if order and order.get("status") == "filled":
                        exit_price = float(order.get("filled_avg_price") or last_price)
                        filled_size = float(order.get("filled_size") or close_size)
                        if await self._confirm_flat_then_close(exit_price, filled_size, exchange):
                            return
                        continue
                pos = await exchange.get_position(self.config.symbol)
                if pos is None:
                    exit_price = last_price
                    if ord_id:
                        try:
                            order = await exchange.get_order(ord_id)
                            if order and order.get("filled_avg_price"):
                                exit_price = float(order["filled_avg_price"])
                        except Exception:
                            pass
                    await self._on_position_closed(exit_price, close_size, exchange)
                    return
            except Exception:
                pass
        log.warning("[Bot %d] Liquidação manual: posição pode ainda estar aberta", self.config.id)

# ── BotManager ────────────────────────────────────────────────────────────────

class BotManager:
    """Singleton que gerencia todas as instâncias de bots."""

    def __init__(self):
        self._instances: dict[int, BotInstance] = {}
        self._ws_clients: set = set()

    # ── WebSocket broadcast ───────────────────────────────────────────────────

    def register_ws(self, queue: asyncio.Queue):
        self._ws_clients.add(queue)

    def unregister_ws(self, queue: asyncio.Queue):
        self._ws_clients.discard(queue)

    async def _broadcast(self, msg: dict):
        import json
        data = json.dumps(msg)
        dead = set()
        for q in self._ws_clients:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dead.add(q)
        self._ws_clients -= dead

    # ── Gestão de bots ────────────────────────────────────────────────────────

    def start_bot(self, config: BotModel) -> BotInstance:
        restarted = False
        target_symbol = (config.symbol or "").upper()
        for other_id, other in self._instances.items():
            if other_id == config.id or other.status != "running":
                continue
            if (other.config.symbol or "").upper() == target_symbol:
                raise RuntimeError(
                    f"Ativo {config.symbol} já está em execução no bot {other.config.id} ({other.config.name})."
                )
        if config.id in self._instances:
            prev = self._instances[config.id]
            if prev.status == "running":
                return prev
            prev.stop()
            restarted = True

        inst = BotInstance(config, ws_broadcast=self._broadcast)
        inst._restarted = restarted
        inst.start()
        self._instances[config.id] = inst
        log.info("Bot %d iniciado (%s / %s)", config.id, config.strategy_id, config.symbol)
        return inst

    def stop_bot(self, bot_id: int):
        inst = self._instances.get(bot_id)
        if inst:
            inst.stop()
            log.info("Bot %d parado.", bot_id)

    async def liquidate_bot(self, bot_id: int):
        """Fecha a posição aberta de um bot específico."""
        inst = self._instances.get(bot_id)
        if inst and inst._direction != 0 and inst.exchange:
            log.info("Reset: Liquidando manualmente bot %d (%s)", bot_id, inst.config.symbol)
            await inst.manual_liquidate(inst.exchange)

    async def liquidate_all(self, db: Session):
        """Para todos os bots, cancela ordens algo pendentes e fecha posições abertas."""
        running_ids = list(self._instances.keys())
        
        # 1. Cancela TODAS as ordens e fecha TODAS as posições na exchange
        # Usa o exchange do primeiro bot ativo como referência
        cancelled_exchange = False
        for bid in running_ids:
            inst = self._instances.get(bid)
            if inst and inst.exchange:
                try:
                    cancelled = await inst.exchange.close_all_positions()
                    log.info("Reset Global: %d posições e ordens limpas na exchange.", cancelled)
                    cancelled_exchange = True
                except Exception as e:
                    log.warning("Reset Global: falha ao limpar exchange: %s", e)
                break  # Só precisa fazer uma vez (a API cancela tudo)

        # Fallback: Se não havia bots rodando em memória, mas queremos limpar a exchange
        if not cancelled_exchange:
            try:
                import aiohttp
                from .exchanges.factory import build_exchange
                async with aiohttp.ClientSession() as session:
                    ex = build_exchange(session)
                    cancelled = await ex.close_all_positions()
                    log.info("Reset Global (Fallback): %d posições e ordens limpas na exchange.", cancelled)
            except Exception as e:
                log.warning("Reset Global (Fallback): falha ao limpar exchange: %s", e)

        # 2. Desliga e para todos os robôs em execução na memória
        for bid in running_ids:
            inst = self._instances.get(bid)
            if inst:
                if inst._direction != 0 and inst.exchange:
                    log.info("Reset: Liquidando %s em memória", inst.config.symbol)
                    try:
                        await inst.manual_liquidate(inst.exchange)
                    except Exception:
                        pass
                self.stop_bot(bid)
            self._instances.pop(bid, None)
        
        # 3. Força o fechamento de todos os trades em aberto no banco de dados para evitar ressurgimento
        try:
            open_trades = db.query(TradeModel).filter(TradeModel.exit_price.is_(None)).all()
            for t in open_trades:
                t.exit_price = t.entry_price or 0.0
                t.pnl = 0.0
                t.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                t.event = "RESET_FORCE_CLOSE"
                
                # Também cria um registro de saída correspondente no histórico para manter consistência
                exit_trade = TradeModel(
                    bot_id=t.bot_id,
                    symbol=t.symbol,
                    type="exit",
                    event="RESET_FORCE_CLOSE",
                    direction=t.direction,
                    size=t.size,
                    entry_price=t.entry_price,
                    exit_price=t.entry_price,
                    sl_price=t.sl_price,
                    tp1_price=t.tp1_price,
                    pnl=0.0,
                    closed_at=t.closed_at,
                    source="bot"
                )
                db.add(exit_trade)
            db.commit()
            log.info("Reset Global: %d trades abertos no banco de dados foram fechados forçadamente.", len(open_trades))
        except Exception as e:
            log.warning("Reset Global: falha ao fechar trades em aberto no banco: %s", e)

        from .database import BotModel
        db.query(BotModel).update({BotModel.active: False})
        db.commit()

    def get_status(self, bot_id: int) -> Optional[dict]:
        inst = self._instances.get(bot_id)
        return inst.get_status() if inst else None

    def all_statuses(self) -> list[dict]:
        return [inst.get_status() for inst in self._instances.values()]

    def get_open_positions_summary(self) -> list[dict]:
        """Retorna resumo das posições abertas para uso em deploy/alertas."""
        result = []
        for bot_id, inst in self._instances.items():
            if inst._direction != 0:
                result.append({
                    "bot_id": bot_id,
                    "name": inst.config.name,
                    "symbol": inst.config.symbol,
                    "direction": "LONG" if inst._direction == 1 else "SHORT",
                    "entry_price": inst._entry_price,
                    "size": inst._sz,
                })
        return result

    async def _reconcile_loop(self):
        """Verifica periodicamente se algum bot ficou divergente na OKX."""
        while True:
            await asyncio.sleep(15)
            try:
                for bot in list(self._instances.values()):
                    exchange = bot.exchange
                    if exchange is None:
                        continue
                    pos = await exchange.get_position(bot.config.symbol)
                    qty = abs(float(pos.size or 0.0)) if pos else 0.0
                    if bot._direction == 0:
                        if qty > 0 and bot._is_spot_symbol(bot.config.symbol):
                            adopted_dir = 1 if pos.side == "long" else -1
                            adopted_px = float(pos.avg_price or 0.0)
                            fallback_sl = round(
                                adopted_px * (1 - 0.05) if adopted_dir == 1
                                else adopted_px * (1 + 0.05),
                                6,
                            )
                            orphan_tp1 = adopted_px * (1.02 if adopted_dir == 1 else 0.98)
                            bot._direction = adopted_dir
                            bot._sz = qty
                            bot._sz_remaining = qty
                            bot._entry_price = adopted_px
                            bot._sl_price = fallback_sl
                            bot._tp1_price = orphan_tp1
                            trade_id = bot._save_trade({
                                "type": "entry",
                                "event": "ORPHAN_ADOPTED_RECONCILE",
                                "direction": "LONG" if adopted_dir == 1 else "SHORT",
                                "size": qty,
                                "entry_price": adopted_px,
                                "sl_price": fallback_sl,
                                "tp1_price": orphan_tp1,
                                "source": "orphan_recovery",
                            })
                            bot._current_trade_id = trade_id
                            log.warning(
                                "[Bot %d] Reconciliação adotou saldo spot órfão: %.8f %s @ %.6f.",
                                bot.config.id, qty, bot.config.symbol, adopted_px,
                            )
                        continue
                    if qty <= 0:
                        log.warning(
                            "[Bot %d] DESYNC: banco tem posição %s aberta mas OKX está FLAT. Resetando.",
                            bot.config.id, bot.config.symbol,
                        )
                        bot._save_trade({
                            "type": "exit", "event": "DESYNC_RECONCILE",
                            "exit_price": bot._entry_price or 0.0,
                            "pnl": 0.0,
                            "closed_at": datetime.now(timezone.utc).replace(tzinfo=None),
                        }, update_id=bot._current_trade_id)
                        bot._reset()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("Reconciliação de posições falhou: %s", exc)


# Instância global
manager = BotManager()
