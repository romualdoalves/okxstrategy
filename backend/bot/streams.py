"""
Eventos de ordens da exchange (WS privado) e contextos externos (grafo, on-chain, GEX, DEX).
"""

from __future__ import annotations

import aiohttp
import asyncio
import time

from ..exchanges.base import BaseExchange
from ..candle_store import drop_open_candle
from ..strategies.base import STRATEGY_WINDOW
from ..notifications import build_order_confirmed_msg
from ..exchanges.factory import get_private_stream_class
from ..feeds.onchain_monitor import onchain_monitor
from .common import log


class StreamsMixin:
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
                    self.config.symbol, tf_bar, limit=STRATEGY_WINDOW + 1)
                # Só candles fechados: o semanal/4H em formação mudaria a cada consulta (repaint).
                candles = drop_open_candle(candles, tf_bar)
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
