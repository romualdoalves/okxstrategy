"""
graph/metrics.py — Extrai métricas de grafo relevantes para trading.

Métricas calculadas:
  centrality_btc   — Eigenvector Centrality do BTC
                     Alto → BTC está no centro da rede → lidera o mercado
                     Baixo → BTC é periférico → seguidor

  centrality_eth   — Eigenvector Centrality do ETH
                     ETH frequentemente lidera BTC em reversões de tendência.
                     Se ETH > BTC → sinal antecipado de movimento

  betweenness_btc  — Betweenness Centrality do BTC
                     BTC como ponte entre comunidades → papel de relay

  graph_density    — Densidade do grafo (0 = sem arestas, 1 = totalmente conectado)
                     Alta density = mercado em modo "risk-on/off" unificado
                     Baixa density = mercado fragmentado = chaos

  n_communities    — Número de comunidades (Louvain greedy modularity)
                     1 ou 2 = mercado unificado (regime bull/bear)
                     3+ = fragmentação = cautela

  btc_cluster      — ID da comunidade do BTC
  eth_same_cluster — ETH no mesmo cluster que BTC?

  eth_leads        — Heurística: ETH tem centrality > BTC E está no mesmo cluster
                     → sinal de liderança ETH→BTC (antecipação)
"""

from __future__ import annotations
from typing import Optional

import networkx as nx
from networkx.algorithms.community import greedy_modularity_communities

TARGET  = 'BTC-USDT'
ETH     = 'ETH-USDT'


def compute_metrics(G: nx.Graph, target: str = TARGET) -> dict:
    """
    Calcula todas as métricas do grafo para o ativo alvo.

    Returns dict com todas as métricas + 'centrality_all' (dict completo).
    Returns {'error': reason} se o grafo for inválido.
    """
    if G.number_of_nodes() == 0:
        return {'error': 'empty_graph'}

    if target not in G:
        return {'error': f'{target} not in graph'}

    n_edges = G.number_of_edges()

    # ── Centralidade ──────────────────────────────────────────────────────────
    if n_edges == 0:
        # Grafo sem arestas: todos os nós são igualmente centrais (ou irrelevantes)
        centrality_all = {n: 0.0 for n in G.nodes()}
        betweenness    = {n: 0.0 for n in G.nodes()}
    else:
        try:
            centrality_all = nx.eigenvector_centrality(
                G, weight='weight', max_iter=1000, tol=1e-6)
        except nx.PowerIterationFailedConvergence:
            # Fallback: degree centrality (sempre converge)
            centrality_all = nx.degree_centrality(G)

        betweenness = nx.betweenness_centrality(G, weight='weight', normalized=True)

    centrality_btc = centrality_all.get(target, 0.0)
    centrality_eth = centrality_all.get(ETH, 0.0)

    # ── Detecção de comunidades (Louvain greedy modularity) ───────────────────
    try:
        if n_edges > 0:
            communities = list(greedy_modularity_communities(G, weight='weight'))
        else:
            communities = [frozenset(G.nodes())]
    except Exception:
        communities = [frozenset(G.nodes())]

    partition: dict[str, int] = {}
    for idx, comm in enumerate(communities):
        for node in comm:
            partition[node] = idx

    btc_cluster      = partition.get(target, -1)
    eth_cluster      = partition.get(ETH, -2)
    eth_same_cluster = (eth_cluster == btc_cluster)
    cluster_members  = [n for n, c in partition.items() if c == btc_cluster]

    # ── Heurística de liderança ETH → BTC ────────────────────────────────────
    eth_leads = (
        centrality_eth > centrality_btc
        and eth_same_cluster
        and n_edges >= 2
    )

    # ── Densidade do grafo ────────────────────────────────────────────────────
    density    = nx.density(G)
    btc_degree = G.degree(target) if target in G else 0

    return {
        # Métricas principais
        'centrality_btc':   round(centrality_btc, 4),
        'centrality_eth':   round(centrality_eth, 4),
        'betweenness_btc':  round(betweenness.get(target, 0.0), 4),
        'graph_density':    round(density, 4),
        'n_communities':    len(communities),
        'n_edges':          n_edges,
        'btc_degree':       btc_degree,

        # Cluster info
        'btc_cluster':      btc_cluster,
        'eth_same_cluster': eth_same_cluster,
        'cluster_members':  cluster_members,
        'eth_leads':        eth_leads,

        # Para serialização de nós no frontend
        'centrality_all':  {k: round(v, 4) for k, v in centrality_all.items()},
        'partition':       partition,
    }
