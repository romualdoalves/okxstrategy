"""
graph/regime.py — Máquina de estados para classificação de regime de mercado.

Regimes:
  chaos      → grafo esparso (density < 0.25): ativos descorrelacionados,
               mercado sem direção clara. Não operar.

  transition → mudança de cluster do BTC detectada: regime acaba de mudar.
               Aguardar 3 barras antes de operar (evitar whipsaw).

  trending   → BTC é o nó mais central, mercado unificado (density > 0.4,
               n_communities ≤ 2). BTC está liderando. Sinal de EMA é confiável.

  lagging    → ETH tem centrality > BTC no mesmo cluster.
               ETH lidera BTC: antecipa movimento do BTC em 1–3 barras.
               Sinal mais arriscado mas de maior alpha potencial.

  neutral    → condição intermediária, sem regime claro. Operar com metade
               do tamanho normal.

Transições de estado e freeze period:
  Qualquer mudança de cluster aciona 'transition' por 3 barras.
  Isso evita entrar numa posição imediatamente após uma quebra de regime,
  quando o ruído é máximo.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RegimeState:
    name:           str    # chaos | transition | trending | lagging | neutral
    confidence:     float  # 0.0 – 1.0
    bars_in_regime: int    = 0
    details:        dict   = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'name':           self.name,
            'confidence':     self.confidence,
            'bars_in_regime': self.bars_in_regime,
            'details':        self.details,
        }


class RegimeClassifier:
    """
    Classifica o regime de mercado a partir das métricas do grafo.
    Mantém estado interno para detectar transições e aplicar freeze period.
    """

    # Thresholds de classificação
    DENSITY_CHAOS      = 0.25   # abaixo → chaos
    DENSITY_TREND_MIN  = 0.35   # mínimo para trending
    CENTRALITY_LEAD    = 0.30   # BTC deve ter centrality >= isso para trending
    CENTRALITY_LAG_MAX = 0.20   # se BTC < isso e ETH > → lagging
    TRANSITION_FREEZE  = 3      # barras de freeze após mudança de cluster

    def __init__(self):
        self._prev_cluster:     Optional[int] = None
        self._transition_bars:  int = 0
        self._current:          str = 'neutral'
        self._bars_in_regime:   int = 0

    def classify(self, metrics: dict) -> RegimeState:
        if 'error' in metrics:
            return RegimeState(
                name='chaos', confidence=1.0,
                details={'reason': metrics['error']})

        density      = metrics['graph_density']
        centrality   = metrics['centrality_btc']
        eth_cent     = metrics['centrality_eth']
        n_comm       = metrics['n_communities']
        btc_cluster  = metrics['btc_cluster']
        eth_same     = metrics['eth_same_cluster']
        eth_leads    = metrics['eth_leads']

        # ── Detectar mudança de cluster (regime shift) ─────────────────────
        cluster_changed = (
            self._prev_cluster is not None
            and btc_cluster != self._prev_cluster
            and btc_cluster >= 0
        )
        if cluster_changed:
            self._transition_bars = self.TRANSITION_FREEZE

        self._prev_cluster = btc_cluster

        # ── Aplicar freeze de transição ────────────────────────────────────
        if self._transition_bars > 0:
            self._transition_bars -= 1
            regime     = 'transition'
            confidence = 0.25
        # ── Chaos: grafo esparso, sem estrutura ────────────────────────────
        elif density < self.DENSITY_CHAOS:
            regime     = 'chaos'
            confidence = round(1.0 - density / self.DENSITY_CHAOS, 3)
        # ── Trending: BTC liderando mercado unificado ──────────────────────
        elif (centrality >= self.CENTRALITY_LEAD
              and n_comm <= 2
              and density >= self.DENSITY_TREND_MIN):
            regime     = 'trending'
            confidence = round(min(1.0, centrality * density * 2.5), 3)
        # ── Lagging: ETH lidera BTC no mesmo cluster ───────────────────────
        elif (eth_leads
              and centrality <= self.CENTRALITY_LAG_MAX
              and eth_same):
            regime     = 'lagging'
            confidence = round(min(1.0, (eth_cent - centrality) * 3), 3)
        # ── Neutral: condição intermediária ────────────────────────────────
        else:
            regime     = 'neutral'
            confidence = 0.40

        # ── Atualizar contador de barras no regime ─────────────────────────
        if regime == self._current:
            self._bars_in_regime += 1
        else:
            self._bars_in_regime = 1
            self._current = regime

        return RegimeState(
            name           = regime,
            confidence     = confidence,
            bars_in_regime = self._bars_in_regime,
            details        = {
                'density':         density,
                'centrality_btc':  centrality,
                'centrality_eth':  eth_cent,
                'n_communities':   n_comm,
                'eth_leads':       eth_leads,
                'transition_left': self._transition_bars,
            },
        )

    def reset(self):
        self._prev_cluster    = None
        self._transition_bars = 0
        self._current         = 'neutral'
        self._bars_in_regime  = 0
