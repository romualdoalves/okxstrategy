"""
strategies/influencers_followers.py — I005 Influencers and Followers (Lead-Lag Effect)

Implementação do algoritmo de Graph Data Science para identificar transmissão de 
liquidez entre ativos, definindo o Influenciador (PageRank) e o Seguidor.
"""

from __future__ import annotations
from typing import Optional, Any
import logging
import numpy as np
import pandas as pd
import networkx as nx

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult

log = logging.getLogger(__name__)


class InfluencersFollowersEngine:
    def __init__(self, tickers):
        self.tickers = tickers
        self.graph = nx.DiGraph()
        self.influencer = None
        self.follower = None
        
    def build_market_graph(self, historical_data: pd.DataFrame):
        """
        historical_data: DataFrame com colunas multi-index ou index de data 
        e colunas para o fechamento M15 de cada ticker nos últimos 30 dias.
        """
        self.graph.clear()
        returns = historical_data.pct_change().dropna()
        
        # Adiciona os nós
        for ticker in self.tickers:
            self.graph.add_node(ticker)
            
        # Calcula correlação de Lead-Lag (Deslocamento de 1 vela / 15 min)
        for i in self.tickers:
            for j in self.tickers:
                if i == j:
                    continue
                
                # Correlação de i influenciando j no futuro (t+1)
                correlation = returns[i].shift(1).corr(returns[j])
                
                # Se a influência for estatisticamente relevante
                if correlation > 0.65:
                    # O peso armazena a força da transmissão do preço
                    self.graph.add_edge(i, j, weight=correlation)
                    
    def select_strategy_targets(self):
        """
        Aplica PageRank para descobrir o ativo mais influente e 
        seleciona o seguidor com maior dependência direta dele.
        """
        # 1. Identifica o Ativo Influenciador mais forte via PageRank
        if not self.graph.nodes:
            self.influencer = None
            self.follower = None
            return None, None
            
        pagerank = nx.pagerank(self.graph, weight='weight')
        if not pagerank:
            self.influencer = None
            self.follower = None
            return None, None
            
        self.influencer = max(pagerank, key=pagerank.get)
        
        # 2. Identifica o Seguidor mais dependente (maior peso de aresta saindo do Influenciador)
        successors = list(self.graph.successors(self.influencer))
        if not successors:
            # Fallback caso não haja sucessores diretos na nota de corte
            self.follower = None
            return self.influencer, self.follower
            
        edge_weights = {sec: self.graph[self.influencer][sec]['weight'] for sec in successors}
        self.follower = max(edge_weights, key=edge_weights.get)
        
        return self.influencer, self.follower

    def check_execution_signals(
        self,
        current_m15_candles: dict,
        *,
        atr_multiplier: float = 2.0,
        rsi_threshold: float = 62.0,
        tp_multiplier: float = 2.5,
    ):
        """
        current_m15_candles: dicionário contendo DataFrames das últimas velas dos ativos.
        """
        criteria_total = 4
        criteria_met = 0
        diagnostics = {
            "influencer": self.influencer,
            "follower": self.follower,
            "leader_body": 0.0,
            "leader_atr": 0.0,
            "leader_impulse_ratio": 0.0,
            "follower_rsi": 50.0,
        }

        if not self.influencer or not self.follower:
            return {
                "action": "HOLD",
                "reason": "Aguardando definição de líder e seguidor.",
                "criteria_met": criteria_met,
                "criteria_total": criteria_total,
                "diagnostics": diagnostics,
            }
        criteria_met += 1
            
        if self.influencer not in current_m15_candles or self.follower not in current_m15_candles:
            return {
                "action": "HOLD",
                "reason": "Faltam candles para líder ou seguidor.",
                "criteria_met": criteria_met,
                "criteria_total": criteria_total,
                "diagnostics": diagnostics,
            }
            
        inf_df = current_m15_candles[self.influencer]
        fol_df = current_m15_candles[self.follower]
        
        if inf_df.empty or fol_df.empty:
            return {
                "action": "HOLD",
                "reason": "Candles vazios para líder ou seguidor.",
                "criteria_met": criteria_met,
                "criteria_total": criteria_total,
                "diagnostics": diagnostics,
            }
        criteria_met += 1
        
        # Cálculo do ATR do Influenciador para validar tamanho da vela
        # Para evitar warning de view, faremos cópia implícita ao atribuir
        inf_df = inf_df.copy()
        
        inf_df['TR'] = np.maximum(inf_df['high'] - inf_df['low'], 
                                  np.maximum(abs(inf_df['high'] - inf_df['close'].shift(1)), 
                                             abs(inf_df['low'] - inf_df['close'].shift(1))))
        inf_atr = float(inf_df['TR'].rolling(14).mean().iloc[-1] or 0.0)
        if not np.isfinite(inf_atr):
            inf_atr = 0.0
        
        last_inf_candle = inf_df.iloc[-1]
        last_fol_candle = fol_df.iloc[-1]
        
        # 3. Critério da Vela Impulsiva do Influenciador
        inf_body = float(last_inf_candle['close'] - last_inf_candle['open'])
        impulse_threshold = float(atr_multiplier) * inf_atr
        impulse_ratio = abs(inf_body) / inf_atr if inf_atr > 0 else 0.0
        is_inf_impulsive = inf_atr > 0 and abs(inf_body) > impulse_threshold

        fol_rsi = float(self._calculate_rsi(fol_df['close'], 14))
        if not np.isfinite(fol_rsi):
            fol_rsi = 50.0
        direction = "BUY" if inf_body > 0 else "SELL" if inf_body < 0 else "HOLD"
        follower_ready = (
            (direction == "BUY" and fol_rsi < float(rsi_threshold)) or
            (direction == "SELL" and fol_rsi > (100.0 - float(rsi_threshold)))
        )
        diagnostics.update({
            "leader_body": inf_body,
            "leader_atr": inf_atr,
            "leader_impulse_ratio": impulse_ratio,
            "follower_rsi": fol_rsi,
        })

        if is_inf_impulsive:
            criteria_met += 1
        if follower_ready:
            criteria_met += 1

        if is_inf_impulsive and follower_ready and direction in ("BUY", "SELL"):
            body_abs = abs(inf_body)
            if direction == "BUY":
                sl_price = float(last_fol_candle['low'])
                tp1_price = float(last_fol_candle['close'] + (float(tp_multiplier) * body_abs))
            else:
                sl_price = float(last_fol_candle['high'])
                tp1_price = float(last_fol_candle['close'] - (float(tp_multiplier) * body_abs))
            return {
                "action": direction,
                "asset": self.follower,
                "reason": (
                    f"Líder {self.influencer} disparou {direction}. "
                    f"Seguidor {self.follower} em atraso estrutural "
                    f"(RSI: {fol_rsi:.2f}, impulso: {impulse_ratio:.2f} ATR)."
                ),
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "criteria_met": criteria_met,
                "criteria_total": criteria_total,
                "diagnostics": diagnostics,
            }

        if not is_inf_impulsive:
            reason = (
                f"Líder sem vela impulsiva: {impulse_ratio:.2f} ATR "
                f"(mín {float(atr_multiplier):.2f} ATR)."
            )
        elif not follower_ready:
            reason = (
                f"Seguidor não está em atraso para {direction}: RSI {fol_rsi:.2f} "
                f"(limite long < {float(rsi_threshold):.0f}, short > {100.0 - float(rsi_threshold):.0f})."
            )
        else:
            reason = "Aguardando gatilho direcional do líder."

        return {
            "action": "HOLD",
            "reason": reason,
            "criteria_met": criteria_met,
            "criteria_total": criteria_total,
            "diagnostics": diagnostics,
        }

    def _calculate_rsi(self, series, period=14):
        if len(series) < period + 1:
            return 50.0
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs)).iloc[-1]


class InfluencersFollowersStrategy(BaseStrategy):
    needs_graph_context = True

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="NW001",
            name="NW001 - Influencers and Followers (Lead-Lag Effect)",
            description=(
                "Implementa a estratégia de Influencers e Followers usando Graph Data Science. "
                "O mercado não é uma tabela de preços, mas uma Rede de Transmissão de Liquidez. "
                "Calcula a causalidade temporal entre ativos (Lead-Lag) e usa PageRank "
                "para encontrar o Líder. Compra o Seguidor mais dependente quando o Líder dispara."
            ),
            tags=["graph", "network", "lead-lag", "pagerank", "correlation", "advanced"],
            recommended_timeframe="15m",
            criteria=[
                {"id": "c1_leader", "label": "C1 Líder", "description": "Ativo líder identificado por PageRank/influência."},
                {"id": "c2_follower", "label": "C2 Seguidor", "description": "Símbolo do bot corresponde ao seguidor correto."},
                {"id": "c3_impulse", "label": "C3 Impulso", "description": "Impulso do líder em múltiplos de ATR."},
                {"id": "c4_follower_rsi", "label": "C4 RSI Seguidor", "description": "RSI do seguidor dentro da zona operacional."},
            ],
            params={
                "rsi_threshold": ParamDef(
                    type="int", default=62, min=40, max=80, step=1,
                    description="RSI máximo do Seguidor (para não comprar sobrecomprado)."
                ),
                "atr_multiplier": ParamDef(
                    type="float", default=2.0, min=1.0, max=5.0, step=0.1,
                    description="Multiplicador de ATR para confirmar vela impulsiva do Líder."
                ),
                "tp_multiplier": ParamDef(
                    type="float", default=2.5, min=1.0, max=5.0, step=0.1,
                    description="Multiplicador do corpo da vela do Líder para alvo de Take Profit."
                ),
            },
        )

    def __init__(self):
        p = self.info().params
        self.rsi_threshold = p["rsi_threshold"].default
        self.atr_multiplier = p["atr_multiplier"].default
        self.tp_multiplier = p["tp_multiplier"].default

        # Engine encapsulada
        self.engine = InfluencersFollowersEngine(tickers=[])
        self.last_graph_update = None
        self._last_price_matrix: dict = {}
        
    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        # BotManager com context é mandatório para esta estratégia
        return None

    def update_graph(self, price_matrix: dict) -> dict:
        """
        Reconstrói o grafo (chamado pelo BotManager se needs_graph_context=True).
        """
        current_tickers = list(price_matrix.keys())
        if not self.engine.tickers or set(self.engine.tickers) != set(current_tickers):
            self.engine.tickers = current_tickers

        self._last_price_matrix = price_matrix

        df_hist = pd.DataFrame(price_matrix)
        if len(df_hist) >= 30:
            self.engine.build_market_graph(df_hist)
            try:
                self.engine.select_strategy_targets()
            except Exception as e:
                log.warning("I005 select_strategy_targets falhou: %s", e)

        # Calcula métricas pro UI — pagerank protegido contra falha de convergência
        has_nodes = len(self.engine.graph.nodes) > 0
        try:
            pagerank = nx.pagerank(self.engine.graph, weight='weight') if has_nodes else {}
        except Exception:
            pagerank = {}
        density  = nx.density(self.engine.graph) if has_nodes else 0
        c_btc    = pagerank.get("BTC-USDT", 0.0)
        c_eth    = pagerank.get("ETH-USDT", 0.0)
        eth_leads    = c_eth > c_btc if has_nodes else False
        regime_name  = 'trending' if self.engine.influencer else 'neutral'

        self.last_graph_update = {
            'regime': {'name': regime_name, 'confidence': 1.0, 'bars_in_regime': 0},
            'nodes': [{'id': n, 'label': n} for n in self.engine.graph.nodes],
            'edges': [{'source': u, 'target': v, 'weight': d.get('weight', 0)}
                      for u, v, d in self.engine.graph.edges(data=True)],
            'metrics': {
                'graph_density': density,
                'centrality_btc': c_btc,
                'centrality_eth': c_eth,
                'eth_leads': eth_leads,
            },
            'sentiment': None,
        }
        return self.last_graph_update

    def compute_with_context(self, candles: list, context: dict | None = None) -> Optional[StrategyResult]:
        if not candles:
            return None

        inds = self._build_indicators(candles)

        if not self.engine.influencer or not self.engine.follower:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Aguardando construção do grafo de mercado (update_graph ainda não executado).",
                indicators=inds,
                metadata={"influencer": None, "follower": None},
            )

        if not self._last_price_matrix:
            return StrategyResult(
                signal=Signal.HOLD,
                hold_reason="Grafo identificado mas price_matrix ainda não recebido do BotManager.",
                indicators=inds,
                metadata={"influencer": self.engine.influencer, "follower": self.engine.follower},
            )

        # Reconstrói pseudo-OHLC de cada ticker a partir dos preços de fechamento armazenados
        current_m15_candles = {}
        for ticker, prices in self._last_price_matrix.items():
            df = pd.DataFrame({"close": prices})
            df["open"] = df["close"].shift(1).fillna(df["close"])
            df["high"] = df[["open", "close"]].max(axis=1) * 1.001
            df["low"]  = df[["open", "close"]].min(axis=1) * 0.999
            current_m15_candles[ticker] = df

        signal_output = self.engine.check_execution_signals(
            current_m15_candles,
            atr_multiplier=float(self.atr_multiplier),
            rsi_threshold=float(self.rsi_threshold),
            tp_multiplier=float(self.tp_multiplier),
        )

        inds["influencer"] = self.engine.influencer
        inds["follower"]   = self.engine.follower
        target_symbol = str((context or {}).get("symbol") or "").upper()
        follower_symbol = str(self.engine.follower or "").upper()
        target_ok = bool(target_symbol and follower_symbol and target_symbol == follower_symbol)
        diagnostics = signal_output.get("diagnostics", {}) if isinstance(signal_output, dict) else {}
        inds.update({
            "leader_body": round(float(diagnostics.get("leader_body", 0.0)), 6),
            "leader_atr": round(float(diagnostics.get("leader_atr", 0.0)), 6),
            "leader_impulse_ratio": round(float(diagnostics.get("leader_impulse_ratio", 0.0)), 4),
            "follower_rsi": round(float(diagnostics.get("follower_rsi", 50.0)), 2),
            "target_symbol": target_symbol,
            "target_is_follower": 1 if target_ok else 0,
        })
        if isinstance(signal_output, dict):
            signal_output["criteria_total"] = int(signal_output.get("criteria_total", 0) or 0) + 1
            if target_ok:
                signal_output["criteria_met"] = int(signal_output.get("criteria_met", 0) or 0) + 1

        if isinstance(signal_output, dict) and signal_output.get("action") in ("BUY", "SELL"):
            action = signal_output.get("action")
            if not target_ok:
                return StrategyResult(
                    signal=Signal.HOLD,
                    hold_reason=(
                        f"Grafo quer operar {self.engine.follower}, mas este bot está configurado para "
                        f"{target_symbol or 'símbolo desconhecido'}."
                    ),
                    indicators=inds,
                    metadata={"influencer": self.engine.influencer, "follower": self.engine.follower},
                    criteria_met=int(signal_output.get("criteria_met", 0) or 0),
                    criteria_total=int(signal_output.get("criteria_total", 0) or 0),
                )
            return StrategyResult(
                signal=Signal.BUY if action == "BUY" else Signal.SELL,
                hold_reason=signal_output.get("reason", ""),
                indicators=inds,
                metadata=self._risk_metadata(
                    candles[-1].close,
                    signal_output.get("sl_price"),
                    signal_output.get("tp1_price"),
                    {
                    "influencer": self.engine.influencer,
                    "follower":   self.engine.follower,
                    },
                ),
                criteria_met=int(signal_output.get("criteria_met", 0) or 0),
                criteria_total=int(signal_output.get("criteria_total", 0) or 0),
            )

        return StrategyResult(
            signal=Signal.HOLD,
            hold_reason=signal_output.get("reason", str(signal_output)) if isinstance(signal_output, dict) else str(signal_output),
            indicators=inds,
            metadata={"influencer": self.engine.influencer, "follower": self.engine.follower},
            criteria_met=int(signal_output.get("criteria_met", 0) or 0) if isinstance(signal_output, dict) else 0,
            criteria_total=int(signal_output.get("criteria_total", 0) or 0) if isinstance(signal_output, dict) else 0,
        )

    def _build_indicators(self, candles: list) -> dict[str, Any]:
        last = candles[-1]
        inds: dict[str, Any] = {
            "close": round(last.close, 2),
            "high":  round(last.high, 2),
            "low":   round(last.low, 2),
        }

        # ATR (needed by BotManager trailing-stop logic)
        if len(candles) >= 15:
            closes = [c.close for c in candles]
            highs  = [c.high  for c in candles]
            lows   = [c.low   for c in candles]
            trs = [
                max(h - l, abs(h - pc), abs(l - pc))
                for h, l, pc in zip(highs[1:], lows[1:], closes[:-1])
            ]
            inds["atr"] = round(sum(trs[-14:]) / 14, 6)
        else:
            inds["atr"] = 0.0

        # Graph-level metrics for the UI
        if self.last_graph_update:
            metrics = self.last_graph_update.get("metrics", {})
            regime  = self.last_graph_update.get("regime",  {})
            inds["regime"]        = regime.get("name",          "neutral")
            inds["regime_conf"]   = regime.get("confidence",    1.0)
            inds["bars_in_regime"] = regime.get("bars_in_regime", 0)
            inds["graph_density"]  = metrics.get("graph_density",  0)
            inds["centrality_btc"] = metrics.get("centrality_btc", 0)
            inds["centrality_eth"] = metrics.get("centrality_eth", 0)
            inds["eth_leads"]      = metrics.get("eth_leads",      False)
            inds["sentiment"]      = self.last_graph_update.get("sentiment")
        else:
            inds.update({
                "regime": "neutral", "regime_conf": 1.0, "bars_in_regime": 0,
                "graph_density": 0, "centrality_btc": 0, "centrality_eth": 0,
                "eth_leads": False, "sentiment": None,
            })

        inds["ema_fast"] = 0
        inds["ema_slow"] = 0
        return inds

    @staticmethod
    def _risk_metadata(entry: float, sl_price, tp1_price, extra: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(extra)
        metadata["sl_price"] = sl_price
        metadata["tp1_price"] = tp1_price
        if entry and sl_price and tp1_price:
            metadata["sl_pct"] = round(abs(float(entry) - float(sl_price)) / float(entry), 5)
            metadata["tp1_pct"] = round(abs(float(tp1_price) - float(entry)) / float(entry), 5)
            metadata["ts_pct"] = metadata["tp1_pct"]
        return metadata

    def _base_indicators(self, candle) -> dict[str, Any]:
        return {
            "close": round(candle.close, 2),
            "high":  round(candle.high, 2),
            "low":   round(candle.low, 2),
        }
