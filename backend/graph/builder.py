"""
graph/builder.py — Constrói grafo de correlação dinâmica entre ativos.

Lógica:
  - Nós    = ativos cripto (BTC, ETH, SOL, XRP, BNB)
  - Arestas = correlação de Pearson dos log-retornos, janela rolante
  - Filtro  = |r| >= threshold AND não-nan
  - Peso    = |r|   (força da correlação, sempre positivo na aresta)
  - Sinal   = r     (campo 'raw': positivo = mesma direção, negativo = inversa)

Por que log-retornos e não preços?
  Preços têm tendência e não-estacionariedade. Log-retornos são estacionários
  e permitem comparação justa entre ativos de preços absolutos diferentes
  (BTC ~$80k vs XRP ~$0.5).
"""

from __future__ import annotations
from itertools import combinations

import numpy as np
import networkx as nx


ASSETS = [
    'BTC-USDT',
    'ETH-USDT',
    'SOL-USDT',
    'XRP-USDT',
    'BNB-USDT',
]


class CorrelationGraphBuilder:

    def build(
        self,
        price_matrix: dict[str, list[float]],
        window: int = 48,
        threshold: float = 0.65,
    ) -> nx.Graph:
        """
        Constrói o grafo de correlação.

        Args:
            price_matrix : dict {asset_id → lista de preços de fechamento (ASC)}
            window       : quantas barras usar no cálculo da correlação
            threshold    : correlação mínima para criar aresta

        Returns:
            nx.Graph com atributos de aresta: weight (|r|), raw (r com sinal)
        """
        G = nx.Graph()
        available = [a for a in ASSETS if a in price_matrix
                     and len(price_matrix[a]) >= max(window + 1, 15)]
        G.add_nodes_from(available)

        # Log-retornos na janela mais recente
        returns: dict[str, np.ndarray] = {}
        for asset in available:
            prices = np.array(price_matrix[asset][-(window + 1):], dtype=float)
            prices = np.where(prices <= 0, np.nan, prices)
            log_ret = np.diff(np.log(prices))
            # Remove NaN
            log_ret = log_ret[~np.isnan(log_ret)]
            if len(log_ret) >= 10:
                returns[asset] = log_ret

        # Correlação entre todos os pares
        for a1, a2 in combinations(list(returns.keys()), 2):
            r1, r2 = returns[a1], returns[a2]
            n = min(len(r1), len(r2))
            r1, r2 = r1[-n:], r2[-n:]

            if n < 10:
                continue

            corr_matrix = np.corrcoef(r1, r2)
            r = float(corr_matrix[0, 1])

            if np.isnan(r) or abs(r) < threshold:
                continue

            G.add_edge(
                a1, a2,
                weight=round(abs(r), 4),   # sempre positivo (força)
                raw=round(r, 4),           # com sinal (direção)
            )

        return G

    def to_serializable(self, G: nx.Graph) -> dict:
        """Converte grafo para formato JSON-friendly."""
        return {
            'nodes': list(G.nodes()),
            'edges': [
                {'source': u, 'target': v,
                 'weight': d['weight'], 'raw': d.get('raw', d['weight'])}
                for u, v, d in G.edges(data=True)
            ],
        }
