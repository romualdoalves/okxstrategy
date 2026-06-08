"""
graph/ — Módulo de Graph Data Science para análise de regime de mercado.

Pipeline:
  price_matrix (dict[asset, closes])
    → CorrelationGraphBuilder  →  nx.Graph
    → compute_metrics          →  dict (centrality, density, communities)
    → RegimeClassifier         →  RegimeState
    → GraphRegimeStrategy      →  Signal

Integração FinGPT (Fase 4):
  SentimentChannel (async side-channel, não bloqueia o loop)
    → CryptoPanic API → headlines
    → FinNLP (BERT financeiro) → score [-1, +1]
    → Modifica confidence do sinal final
"""
from .builder import CorrelationGraphBuilder
from .metrics import compute_metrics
from .regime  import RegimeClassifier, RegimeState

__all__ = ['CorrelationGraphBuilder', 'compute_metrics',
           'RegimeClassifier', 'RegimeState']
