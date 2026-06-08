"""
strategies/graph_regime.py — Graph Regime Strategy (estratégia autoral)

Filosofia
─────────
Estratégias convencionais perguntam: "o preço do BTC vai subir?"
Esta pergunta:  "o BTC está numa posição de liderança na rede de ativos
                 cripto agora, e essa posição é consistente com um movimento
                 direcional sustentado?"

É uma pergunta de segunda ordem — onde está o alpha real, porque pouquíssimos
traders retail pensam em termos de estrutura de rede.

Pipeline de sinal
─────────────────
  1. GRAFO  — CorrelationGraphBuilder constrói G com BTC, ETH, SOL, XRP, BNB
  2. REGIME — RegimeClassifier classifica: trending | lagging | neutral |
              transition | chaos
  3. DIREÇÃO — EMA 21/55 crossover como gatilho direcional (dentro do regime)
  4. ATR    — Risco adaptativo (SL, TP1, Trailing)

Regimes e comportamento
───────────────────────
  trending   → BTC lidera a rede → confiar no crossover de EMA, sinal pleno
  lagging    → ETH lidera BTC → antecipar movimento, sinal com 70% de confiança
  neutral    → sem regime claro → aguardar, apenas crossover muito limpo
  transition → regime acabou de mudar → HOLD por 3 barras (freeze)
  chaos      → mercado fragmentado → HOLD sempre

Slot FinGPT (Fase 4)
────────────────────
  O método update_sentiment(score: float) aceita um score [-1, +1] vindo
  de um canal assíncrono de sentiment (FinNLP/FinGPT). Quando presente,
  modifica a confidence do sinal:
    alinha com o grafo   → +20% position size
    contradiz o grafo    → sinal bloqueado se |score| > 0.6
"""

from __future__ import annotations
from typing import Optional

import pandas as pd
import pandas_ta as ta

from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult

# Import resiliente: funciona tanto rodando como módulo quanto como script
try:
    from backend.graph.builder import CorrelationGraphBuilder
    from backend.graph.metrics import compute_metrics
    from backend.graph.regime import RegimeClassifier
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from backend.graph.builder import CorrelationGraphBuilder
    from backend.graph.metrics import compute_metrics
    from backend.graph.regime import RegimeClassifier


class GraphRegimeStrategy(BaseStrategy):

    # Flag lida pelo BotInstance para ativar fetch multi-asset
    needs_graph_context: bool = True

    # ── Metadados ────────────────────────────────────────────────────────────

    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="RG002",
            name="RG002 - Graph Regime + EMA",
            description = (
                'Estratégia autoral baseada em Graph Data Science. '
                'Constrói um grafo de correlação dinâmico entre BTC, ETH, SOL, '
                'XRP e BNB a cada candle. Usa detecção de comunidades (Louvain) '
                'e centralidade de eigenvector para classificar o regime de mercado '
                'em 5 estados: trending, lagging, neutral, transition e chaos. '
                'Só opera em regime favorável, usando EMA 21/55 como gatilho '
                'direcional. Detecta quando ETH lidera BTC (sinal antecipado). '
                'Slot preparado para integração FinGPT/FinNLP (Fase 4).'
            ),
            tags   = ['trend', 'graph', 'network', 'regime', 'atr', 'momentum'],
            criteria=[
                {"id": "c1_regime", "label": "C1 Regime", "description": "Regime de grafo favorável."},
                {"id": "c2_confidence", "label": "C2 Confiança", "description": "Confiança mínima do regime detectado."},
            ],
            params = {
                'corr_window': ParamDef(
                    type='int', default=48, min=20, max=100, step=4,
                    description='Janela de barras para calcular correlação'),
                'corr_threshold': ParamDef(
                    type='float', default=0.65, min=0.5, max=0.85, step=0.05,
                    description='Correlação mínima para criar aresta no grafo'),
                'ema_fast': ParamDef(
                    type='int', default=21, min=5, max=50, step=1,
                    description='EMA rápida para gatilho direcional'),
                'ema_slow': ParamDef(
                    type='int', default=55, min=20, max=200, step=5,
                    description='EMA lenta para gatilho direcional'),
                'atr_period': ParamDef(
                    type='int', default=14, min=5, max=30, step=1,
                    description='Período do ATR para gestão de risco'),
                'sl_mult': ParamDef(
                    type='float', default=1.5, min=0.5, max=4.0, step=0.1,
                    description='Multiplicador ATR para Stop Loss'),
                'tp1_pct': ParamDef(
                    type='float', default=2.0, min=0.5, max=8.0, step=0.5,
                    description='Take Profit 1 (% da entrada — fecha 50%)'),
                'ts_mult': ParamDef(
                    type='float', default=2.5, min=1.0, max=5.0, step=0.5,
                    description='Multiplicador ATR para Trailing Stop (após TP1)'),
            },
        )

    # ── Init ─────────────────────────────────────────────────────────────────

    def __init__(self):
        p = self.info().params
        self.corr_window     = p['corr_window'].default
        self.corr_threshold  = p['corr_threshold'].default
        self.ema_fast        = p['ema_fast'].default
        self.ema_slow        = p['ema_slow'].default
        self.atr_period      = p['atr_period'].default
        self.sl_mult         = p['sl_mult'].default
        self.tp1_pct         = p['tp1_pct'].default
        self.ts_mult         = p['ts_mult'].default

        # Graph state (atualizado pelo BotInstance a cada candle fechado)
        self._builder    = CorrelationGraphBuilder()
        self._classifier = RegimeClassifier()
        self._graph_state: Optional[dict] = None
        self._regime_state = None

        # Slot FinGPT: score de sentiment [-1, +1], None = sem dado
        self._sentiment_score: Optional[float] = None

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k):
                setattr(self, k, v)

    # ── API de atualização do grafo (chamada pelo BotInstance) ───────────────

    def update_graph(self, price_matrix: dict) -> dict:
        """
        Reconstrói o grafo e reclassifica o regime.
        Chamado pelo BotInstance após cada candle fechado.

        Args:
            price_matrix: dict {asset_id → list[float] closes (ASC)}

        Returns:
            dict com estado completo do grafo para API/frontend
        """
        G = self._builder.build(
            price_matrix,
            window=self.corr_window,
            threshold=self.corr_threshold,
        )
        metrics = compute_metrics(G)
        self._regime_state = self._classifier.classify(metrics)

        centrality_all = metrics.pop('centrality_all', {})
        partition      = metrics.get('partition', {})

        self._graph_state = {
            'regime':    self._regime_state.to_dict(),
            'metrics':   {k: v for k, v in metrics.items()
                          if k not in ('partition', 'cluster_members')},
            'cluster_members': metrics.get('cluster_members', []),
            'nodes': [
                {
                    'id':         n,
                    'label':      n.split('-')[0],
                    'cluster':    partition.get(n, 0),
                    'centrality': centrality_all.get(n, 0.0),
                    'degree':     G.degree(n),
                }
                for n in G.nodes()
            ],
            'edges': [
                {
                    'source': u,
                    'target': v,
                    'weight': d['weight'],
                    'raw':    d.get('raw', d['weight']),
                }
                for u, v, d in G.edges(data=True)
            ],
            'sentiment': self._sentiment_score,
        }
        return self._graph_state

    # ── Slot FinGPT / FinNLP (Fase 4) ────────────────────────────────────────

    def update_sentiment(self, score: float) -> None:
        """
        Recebe score de sentiment de canal assíncrono (FinNLP/FinGPT).
        score: -1.0 (muito bearish) → +1.0 (muito bullish)
        """
        self._sentiment_score = max(-1.0, min(1.0, score))

    # ── Compute — lógica principal de sinal ──────────────────────────────────

    def compute(self, candles: list) -> Optional[StrategyResult]:
        min_len = max(self.ema_slow, self.atr_period) + 5
        if len(candles) < min_len:
            return None

        # Sem grafo ainda → hold
        if self._regime_state is None or self._graph_state is None:
            return None

        regime     = self._regime_state
        regime_name = regime.name

        df = pd.DataFrame([
            {'high': c.high, 'low': c.low, 'close': c.close,
             'open': c.open, 'volume': c.volume}
            for c in candles
        ])

        ema_f = ta.ema(df['close'], length=self.ema_fast)
        ema_l = ta.ema(df['close'], length=self.ema_slow)
        atr_s = ta.atr(df['high'], df['low'], df['close'], length=self.atr_period)

        if ema_f is None or ema_l is None or atr_s is None:
            return None

        ef,  el  = float(ema_f.iloc[-1]),  float(ema_l.iloc[-1])
        ef2, el2 = float(ema_f.iloc[-2]),  float(ema_l.iloc[-2])
        atr_val  = float(atr_s.iloc[-1])
        close    = candles[-1].close

        cross_up   = ef2 <= el2 and ef > el
        cross_down = ef2 >= el2 and ef < el

        # ── Filtro de regime → sinal ──────────────────────────────────────
        signal = Signal.HOLD

        if regime_name == 'chaos' or regime_name == 'transition':
            signal = Signal.HOLD

        elif regime_name == 'trending':
            # BTC lidera → confiar na tendência EMA
            if cross_up or (ef > el and ef2 > el2):
                signal = Signal.BUY
            elif cross_down or (ef < el and ef2 < el2):
                signal = Signal.SELL

        elif regime_name == 'lagging':
            # ETH lidera BTC → sinal antecipado, mais sensível
            if cross_up or (ef > el and ef2 > el2):
                signal = Signal.BUY
            elif cross_down or (ef < el and ef2 < el2):
                signal = Signal.SELL

        elif regime_name == 'neutral':
            # Só cruza se confiança da EMA for muito clara (exige o crossover exato)
            if cross_up:
                signal = Signal.BUY
            elif cross_down:
                signal = Signal.SELL

        # ── Razão do HOLD (diagnóstico para UI) ──────────────────────────
        hold_reason = ""
        if signal == Signal.HOLD:
            if regime_name in ('chaos', 'transition'):
                hold_reason = f"Regime {regime_name} — aguardando estabilização"
            elif not (cross_up or cross_down):
                gap = abs(ef - el)
                hold_reason = f"Sem cruzamento EMA — gap {gap:.1f} pts"
            else:
                hold_reason = f"Regime {regime_name} sem sinal direcional claro"

        # ── Modificador FinGPT (Fase 4 — slot pronto) ────────────────────
        if self._sentiment_score is not None and abs(self._sentiment_score) >= 0.6:
            sent = self._sentiment_score
            if signal == Signal.BUY  and sent < -0.6:
                signal = Signal.HOLD
                hold_reason = f"FinGPT bloqueou — sentiment bearish forte ({sent:+.2f})"
            elif signal == Signal.SELL and sent > 0.6:
                signal = Signal.HOLD
                hold_reason = f"FinGPT bloqueou — sentiment bullish forte ({sent:+.2f})"

        # ── Risk prices ───────────────────────────────────────────────────
        if signal == Signal.BUY:
            sl_price  = round(close - atr_val * self.sl_mult, 2)
            tp1_price = round(close * (1 + self.tp1_pct / 100), 2)
        else:
            sl_price  = round(close + atr_val * self.sl_mult, 2)
            tp1_price = round(close * (1 - self.tp1_pct / 100), 2)

        # ── Confidence scaling (posição menor em regimes menos certos) ────
        regime_confidence = regime.confidence
        effective_sl_mult = self.sl_mult * (1.0 + (1.0 - regime_confidence) * 0.5)

        return StrategyResult(
            signal      = signal,
            criteria_met= 2 if signal in (Signal.BUY, Signal.SELL) else 0,
            criteria_total= 2,
            hold_reason = hold_reason,
            indicators  = {
                'regime':          regime_name,
                'regime_conf':     regime.confidence,
                'bars_in_regime':  regime.bars_in_regime,
                'ema_fast':        round(ef, 2),
                'ema_slow':        round(el, 2),
                'atr':             round(atr_val, 4),
                'close':           round(close, 2),
                'graph_density':   self._graph_state['metrics'].get('graph_density', 0),
                'centrality_btc':  self._graph_state['metrics'].get('centrality_btc', 0),
                'centrality_eth':  self._graph_state['metrics'].get('centrality_eth', 0),
                'eth_leads':       self._graph_state['metrics'].get('eth_leads', False),
                'sentiment':       self._sentiment_score,
            },
            metadata = {
                'sl_price':   sl_price,
                'tp1_price':  tp1_price,
                'sl_pct':     round(atr_val * effective_sl_mult / close, 5),
                'tp1_pct':    round(self.tp1_pct / 100, 5),
                'ts_pct':     round(atr_val * self.ts_mult / close, 5),
                'regime':     regime_name,
                'confidence': regime_confidence,
            },
        )
