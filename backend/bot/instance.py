"""
BotInstance: uma instância de bot em execução (compõe os mixins de criteria/entry/position/accounting/streams).
"""

from __future__ import annotations

import aiohttp
import asyncio
from typing import Optional
from datetime import datetime, timezone

from ..exchanges.base import BaseExchange
from ..candle_store import drop_open_candle
from ..database import BotModel, SessionLocal, StrategyModel
from ..feeds.dex_price_feed import DexPriceFeed
from ..feeds.gex_feed import GexFeed
from ..feeds.okx_market_players import OkxMarketPlayersFeed
from ..strategies.base import STRATEGY_WINDOW, Signal
from ..notifications import TelegramNotifier, build_start_msg
from ..profit_lock import fee_rate as _fee_rate
from ..risk import current_day_start_utc as _current_day_start_utc
from ..exchanges.factory import build_exchange, get_exchange_maintenance, get_exchange_provider, get_public_stream_class, map_timeframe_for_history, map_timeframe_for_ws_channel
from ..strategies.registry import get_strategy
from .criteria import OrderGateMixin
from .entry import EntryMixin
from .position import PositionMixin
from .accounting import AccountingMixin
from .goal import GoalMixin
from .streams import StreamsMixin
from .common import CANONICAL_ENTRY_CRITERIA, log


class BotInstance(OrderGateMixin, EntryMixin, PositionMixin, AccountingMixin, GoalMixin, StreamsMixin):
    """
    Uma instância de bot em execução.
    Conecta ao WebSocket da exchange, processa candles e executa ordens.
    """

    def __init__(self, config: BotModel, ws_broadcast):
        self.config       = config
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
        self._last_meta: dict = {}    # metadata do último sinal da estratégia (SL/TP1/ts_pct de referência)
        self._last_price  = 0.0       # último preço AO VIVO (qualquer tick); _candles só guarda candles FECHADOS
        self._lock_retry_at = 0.0     # monotonic: não repetir a trava de lucro a cada tick se a OKX recusar
        # Modo meta (bot/goal.py)
        self._goal_loaded = False
        self._goal_done = False
        self._goal_closing = False
        self._goal_realized_pct = 0.0
        self._goal_had_order = False
        self._goal_outcome = None
        self._goal_retry_at = 0.0
        self._lock_events: dict = {}  # degrau -> horário ISO em que foi alcançado (só desta sessão do processo)
        self._ts_pct      = 0.0       # distância do trailing definida pela estratégia (0 = ATR)
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
        # Por que _halted está ativo: "daily_loss" (some na virada do dia) ou "critical"
        # (posição sem SL etc. — só sai com intervenção manual).
        self._halt_kind   : Optional[str] = None
        self._pnl_day     : datetime = _current_day_start_utc()
        self._hold_reason     : str = ""
        self._last_indicators : dict = self._seed_initial_indicators()
        self._last_order_criteria: dict = self._empty_order_criteria()
        self._last_order_error: Optional[dict] = None
        self._entry_inflight  = False
        self._planned_stake_usd: Optional[float] = None   # stake planejado da entrada em envio (exposição global)
        self._maintenance     : Optional[str] = None
        self._started_at      : Optional[str] = None   # ISO timestamp de início
        self._started_epoch_ms: int = 0
        self._last_closed_candle_at: Optional[str] = None
        self._closed_candle_count: int = 0
        self._last_counted_closed_epoch: int = 0
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

    def _seed_initial_indicators(self) -> dict:
        """Inicializa critérios oficiais para o checklist antes da primeira vela."""
        strategy_id = (self.config.strategy_id or "").strip().upper()
        indicators = {
            "_signal": Signal.HOLD.value,
            "_hold_reason": "",
            "_signal_executable": False,
            "_criteria_met": 0,
            "_criteria_total": 0,
        }

        canonical_names = CANONICAL_ENTRY_CRITERIA.get(strategy_id)
        if canonical_names:
            indicators["_criteria_names"] = list(canonical_names)
            indicators["_criteria_total"] = len(canonical_names)
            return indicators

        try:
            info = self.strategy.info()
            criteria = getattr(info, "criteria", None) or []
            if criteria:
                names = [c.get("label", c.get("id", f"C{i+1}")) for i, c in enumerate(criteria)]
                indicators["_criteria_names"] = names
                indicators["_criteria_total"] = len(names)
                return indicators
        except Exception:
            pass

        # Fallback: usa critérios persistidos no plano da estratégia no banco.
        db_names = self._load_plan_criteria_names(strategy_id)
        if db_names:
            indicators["_criteria_names"] = db_names
            indicators["_criteria_total"] = len(db_names)

        return indicators

    def _load_plan_criteria_names(self, strategy_id: str) -> list[str]:
        """Lê nomes de critérios do plan_json salvo em strategies para fallback de checklist."""
        if not strategy_id:
            return []
        db = SessionLocal()
        try:
            row = db.query(StrategyModel).filter_by(strategy_id=strategy_id).first()
            if not row:
                return []
            plan = row.plan_json or {}
            criteria = plan.get("criteria", []) if isinstance(plan, dict) else []
            if not criteria:
                return []
            return [c.get("label", c.get("id", f"C{i+1}")) for i, c in enumerate(criteria)]
        except Exception:
            return []
        finally:
            db.close()

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
            elif not canonical_names:
                # Se o código da estratégia não expõe criteria, usa fallback já semeado/DB.
                seeded_names = self._last_indicators.get("_criteria_names", []) if isinstance(self._last_indicators, dict) else []
                if not seeded_names:
                    seeded_names = self._load_plan_criteria_names(strategy_id)
                if seeded_names:
                    indicators["_criteria_names"] = seeded_names
                    indicators["_criteria_total"] = len(seeded_names)
                    indicators["_criteria_met"] = min(indicators["_criteria_met"], indicators["_criteria_total"])
        except Exception:
            pass
        return indicators

    # ── Controle ─────────────────────────────────────────────────────────────
    def start(self):
        started = datetime.now(timezone.utc)
        self._started_at = started.isoformat()
        self._started_epoch_ms = int(started.timestamp() * 1000)
        self._closed_candle_count = 0
        self._last_counted_closed_epoch = 0
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
                    # Com trade aberto no banco, o que importa é a posição do BOT (saldo menos baseline em spot).
                    if pos is not None and self._direction != 0 and self._net_size(pos) <= 1e-9:
                        pos = None
                    if pos is not None:
                        if self._direction != 0:
                            # Sincroniza posição se o banco de dados já possui um trade ativo
                            net_size   = self._net_size(pos)
                            actual_pos = net_size if pos.side == "long" else -net_size
                            old_entry  = self._entry_price  # guarda para comparação
                            # Em spot o avg_price da OKX é o ÚLTIMO preço, não o custo: mantém o do banco.
                            exch_entry = old_entry if (self._is_spot_symbol(self.config.symbol) and old_entry > 0) else pos.avg_price
                            self._direction   = 1 if actual_pos > 0 else -1
                            self._sz          = abs(actual_pos)
                            self._sz_remaining = self._sz
                            self._entry_price = exch_entry
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
                                    and abs(exch_entry - old_entry) / old_entry > 0.005
                                    and self._sl_price > 0):
                                old_dist_pct = abs(self._sl_price - old_entry) / old_entry
                                # Preserva a distância percentual do SL, com teto de 5 %
                                safe_pct = min(max(old_dist_pct, 0.005), 0.05)
                                new_sl = round(
                                    exch_entry * (1 - safe_pct) if self._direction == 1
                                    else exch_entry * (1 + safe_pct),
                                    4,
                                )
                                log.warning(
                                    "[Bot %d] Entrada diverge DB %.4f vs OKX %.4f — "
                                    "SL recalculado de %.4f para %.4f.",
                                    self.config.id, old_entry, exch_entry,
                                    self._sl_price, new_sl,
                                )
                                self._sl_price = new_sl

                            # Sincronização de Emergência baseada na OKX apenas (removido SW-TS local)
                            pass
                        else:
                            # Banco FLAT mas OKX tem posição.
                            # A adoção automática foi desativada.
                            self._can_adopt_spot_position(pos, context="startup_recovery")
                    else:
                        if self._direction != 0:
                            log.warning(
                                "[Bot %d] Desajuste detectado: banco de dados contem trade aberto (%d) "
                                "mas a exchange está FLAT. Resetando estado para FLAT.",
                                self.config.id, self._direction
                            )
                            # Mesmo tratamento do _reconcile_loop: busca o fill real de saída
                            # antes de fabricar pnl=0 — o WS pode só ter perdido o evento
                            # enquanto o bot estava reiniciando (deploy, crash, etc.).
                            real_exit = await self._resolve_desync_exit_fill(exchange)
                            if real_exit:
                                exit_price, fill_qty = real_exit
                                ct_size = exchange.get_contract_size(self.config.symbol)
                                pnl = fill_qty * ct_size * (exit_price - self._entry_price) * self._direction
                                log.warning(
                                    "[Bot %d] Fill real recuperado @ %.6f (pnl=%.4f) — fechando com dados reais.",
                                    self.config.id, exit_price, pnl,
                                )
                            else:
                                exit_price, pnl = self._entry_price or 0.0, 0.0
                                log.error(
                                    "[Bot %d] Nenhum fill correspondente encontrado na OKX — "
                                    "fechando com pnl=0 (dado pode estar incorreto).",
                                    self.config.id,
                                )
                            self._record_pnl(pnl)
                            self._save_trade({
                                "type": "exit",
                                "event": "DESYNC_RESET",
                                "exit_price": exit_price,
                                "pnl": pnl, "peak_price": self._peak_price or None,
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
                            self.config.symbol, bar, limit=STRATEGY_WINDOW + 1)
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

                # A OKX devolve o candle em formação junto com os fechados. Mantê-lo faria o warmup
                # sinalizar (e até entrar) em cima de um candle parcial e, no fechamento dele, o
                # `_on_candle` descartaria o candle por já existir esse epoch (ciclo perdido).
                candles = drop_open_candle(candles, bar)
                if candles:
                    self._candles = candles
                    self._last_closed_candle_at = datetime.fromtimestamp(
                        float(candles[-1].epoch) / 1000,
                        tz=timezone.utc,
                    ).isoformat()

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
                        self._last_meta       = dict(result.metadata or {})
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
                    stake_usd       = self.config.stake_usd,
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

    def _get_dynamic_atr(self, result, default_atr: float) -> float:
        """Calcula ATR(14) dinâmico caso a estratégia não tenha fornecido."""
        if result and result.indicators and result.indicators.get("atr"):
            return float(result.indicators["atr"])
        if not self._candles:
            return default_atr
            
        try:
            import pandas as pd
            import pandas_ta as ta
            df = pd.DataFrame([c.__dict__ for c in self._candles])
            if len(df) > 14:
                df.ta.atr(length=14, append=True)
                atr_cols = [c for c in df.columns if c.startswith('ATRr_')]
                if atr_cols:
                    current_atr = float(df[atr_cols[0]].iloc[-1])
                    if not pd.isna(current_atr) and current_atr > 0:
                        if result and result.indicators is not None:
                            result.indicators["atr"] = current_atr
                        return current_atr
        except Exception as e:
            log.warning(f"[Bot {self.config.id}] Falha ao calcular ATR dinâmico: {e}")
            
        return default_atr

    async def _on_candle(self, bar, closed: bool, exchange: BaseExchange,
                         session=None):
        # Candle fechado já conhecido (o stream reentrega os últimos candles no início): ignora, sem
        # reprocessar nem duplicar o histórico fora de ordem.
        if closed and self._candles and int(bar.epoch) <= int(self._candles[-1].epoch):
            return
        # Preço ao vivo para P&L, trailing e status: num bot 1D o último candle fechado tem até 24h de atraso.
        if float(getattr(bar, "close", 0.0) or 0.0) > 0:
            self._last_price = float(bar.close)
        await self._goal_tick(float(bar.close), exchange)      # modo meta: vender ao cobrir / parar
        # Sincronização Dinâmica Automática: Apenas no fechamento do candle para economizar API
        if closed and self._direction != 0:
            await self.force_sync_trailing(exchange)

        # ⚠️ CRÍTICO: Atualiza ATR IMEDIATAMENTE antes de verificar TP1, caso candle tenha fechado
        # Assim _check_tp1() usa ATR fresco, não stale do candle anterior
        if closed:
            # Computa contexto e estratégia para ter ATR atualizado
            await self._update_extra_candles(exchange) if self.strategy.__class__.extra_timeframes() else None
            ctx = self._strategy_context()
            result = self.strategy.compute_with_context(self._candles, ctx)
            if result:
                fresh_atr = result.indicators.get("atr", self._current_atr)
                self._current_atr = fresh_atr
                log.debug("[Bot %d] ATR atualizado antes de TP1: %.6f", self.config.id, fresh_atr)

        # Verifica TP1 em tempo real (mesmo em candle aberto), agora com ATR fresco
        if self._direction != 0 and not self._tp1_done:
            await self._check_profit_lock(bar.close, exchange)
            await self._check_tp1(bar.close, exchange)

        if not closed:
            # Atualiza peak intracandle desde a entrada (não só pós-TP1) — é a base da
            # métrica de efetividade do trailing: quanto do movimento favorável máximo
            # a operação realmente capturou na saída.
            if self._direction != 0:
                if self._direction == 1:
                    if self._peak_price == 0 or bar.close > self._peak_price:
                        self._peak_price = bar.close
                else:
                    if self._peak_price == 0 or bar.close < self._peak_price:
                        self._peak_price = bar.close

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
            bar_epoch = int(bar.epoch)
            self._last_closed_candle_at = datetime.fromtimestamp(
                float(bar_epoch) / 1000 if float(bar_epoch) > 10_000_000_000 else float(bar_epoch),
                tz=timezone.utc,
            ).isoformat()
            bar_epoch_ms = bar_epoch if bar_epoch > 10_000_000_000 else bar_epoch * 1000
            if (
                self._started_epoch_ms > 0
                and bar_epoch_ms > self._started_epoch_ms
                and bar_epoch != self._last_counted_closed_epoch
            ):
                self._closed_candle_count += 1
                self._last_counted_closed_epoch = bar_epoch
            if len(self._candles) > STRATEGY_WINDOW:
                self._candles.pop(0)
            self._roll_day()
            await self._process_strategy(bar, exchange, session)

    # ── Status público ────────────────────────────────────────────────────────
    @staticmethod
    def _to_json(v):
        """Converte tipos numpy/pandas para primitivos Python serializáveis pelo FastAPI."""
        t = type(v).__module__
        if t == 'numpy' or (t and t.startswith('numpy')):
            import numpy as _np
            if isinstance(v, _np.bool_):   return bool(v)
            if isinstance(v, _np.integer): return int(v)
            if isinstance(v, _np.floating):return float(v)
            if isinstance(v, _np.ndarray): return v.tolist()
        if isinstance(v, dict):  return {k: BotInstance._to_json(val) for k, val in v.items()}
        if isinstance(v, list):  return [BotInstance._to_json(i) for i in v]
        return v

    def get_status(self) -> dict:
        # Preço atual vindo do último candle
        last_price = self._last_price or (self._candles[-1].close if self._candles else 0.0)
        execution_cycle = {
            "timeframe": self.config.timeframe,
            "closed_candles": self._closed_candle_count,
            "last_closed_at": self._last_closed_candle_at,
        }
        
        # Se não tem candles ainda, retorna status básico
        if not self._candles:
            return {
                "bot_id":      self.config.id,
                "status":      self.status,
                "started_at":  self._started_at,
                "execution_cycle": execution_cycle,
                "direction":   0,
                "size":        0.0,
                "entry_price": 0.0,
                "last_price":  0.0,
                "peak_price":  0.0,
                "sl_price":    0.0,
                "tp1_price":   0.0,
                "tp1_done":    False,
                "guaranteed_pnl": 0.0,
                "profit_lock": {"enabled": False, "steps": []},
                "trailing": None,
                "goal": self._goal_view(0.0),
                "unrealized_net_usd": 0.0,
                "unrealized_net_pct": 0.0,
                "daily_pnl":   round(self._daily_pnl, 2),
                "wins":        self._wins,
                "losses":      self._losses,
                "halted":      self._halted,
                "hold_reason": self._hold_reason,
                "last_indicators": self._to_json(self._last_indicators),
                "order_criteria":  self._to_json(self._last_order_criteria),
                "last_order_error": self._last_order_error,
                "maintenance":     self._maintenance,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }
        
        # PnL Garantido — só válido quando o SW-TS está ativo (tp1_done=True).
        # Antes do TP1, _sl_price é o SL inicial (abaixo da entrada para LONG),
        # então diff seria negativo e garantido seria 0. Mas se o sync sobrescreveu
        # _entry_price com um contexto diferente, diff pode ser positivo erroneamente.
        # Garantir que somente o trailing stop ativo gere guaranteed_pnl > 0.
        # Agora LÍQUIDO de taxas e também válido nos degraus da trava de lucro (antes do TP1): ver _profit_view.
        pv = self._profit_view(last_price)

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
            "guaranteed_pnl": pv["guaranteed_pnl"],
            "profit_lock": pv["profit_lock"],
            "trailing": self._trailing_view(last_price),
            "goal": self._goal_view(last_price),
            "fee_rate": _fee_rate(),          # o card calcula o líquido sobre o preço ao vivo com esta taxa
            "unrealized_net_usd": pv["unrealized_net_usd"],
            "unrealized_net_pct": pv["unrealized_net_pct"],
            "daily_pnl":   round(self._daily_pnl, 2),
            "wins":        self._wins,
            "losses":      self._losses,
            "halted":      self._halted,
            "hold_reason":     self._hold_reason,
            "last_indicators": self._to_json(self._last_indicators),
            "order_criteria":  self._to_json(self._last_order_criteria),
            "last_order_error": self._last_order_error,
            "maintenance":     self._maintenance,
            "started_at":      self._started_at,
            "execution_cycle":  execution_cycle,
            "graph_state":     self._graph_state,
            "onchain_events":  self._onchain_events,
        }
