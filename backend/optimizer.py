import asyncio
import logging
import itertools
from typing import Dict, Any, List
from .backtest_engine import BacktestEngine

log = logging.getLogger("optimizer")

class StrategyOptimizer:
    """
    Motor de otimização de parâmetros.
    Testa múltiplas combinações e retorna a melhor.
    """

    def __init__(self, strategy_id: str):
        self.strategy_id = strategy_id
        
        # Define o espaço de busca para cada estratégia
        self.search_space = {
            "S006": {
                "outer_length": [14, 20, 26],
                "outer_std": [2.5, 3.0, 4.0],
                "inner_length": [14, 20],
                "inner_std": [1.5, 2.0],
                "rr_ratio": [1.5, 2.0, 3.0]
            },
            "S007": {
                "bb_length": [20, 30, 50],
                "bb_std": [2.0, 2.5],
                "rsi_length": [7, 14],
                "rsi_overbought": [70, 80],
                "rsi_oversold": [20, 30]
            },
            "S008": {
                "support_window": [3, 4, 5],
                "sweep_lookback": [15, 20, 30],
                "wick_ratio": [0.4, 0.5, 0.6],
                "rr_ratio": [2.0, 3.0, 4.0]
            }
        }.get(strategy_id, {})

    async def optimize(self, candles: list, stake_usd: float = 100.0) -> Dict[str, Any]:
        if not self.search_space:
            return {"error": "Espaço de busca não definido para esta estratégia"}

        # Gera todas as combinações possíveis
        keys = self.search_space.keys()
        values = self.search_space.values()
        combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
        
        log.info(f"Otimizando {self.strategy_id}: testando {len(combinations)} combinações...")
        
        best_profit = -999999.0
        best_params = {}
        best_trades = 0

        # Para cada combinação, roda um backtest rápido
        # Usamos uma versão simplificada ou reduzimos os candles para performance
        for params in combinations:
            engine = BacktestEngine(self.strategy_id, params)
            result = await engine.run(candles, stake_usd=stake_usd)
            
            profit = result.get("total_profit", 0)
            
            # Critério: Maior lucro, mas deve ter feito pelo menos 3 trades
            if profit > best_profit and result.get("trades_count", 0) >= 3:
                best_profit = profit
                best_params = params
                best_trades = result.get("trades_count")

        return {
            "best_params": best_params,
            "estimated_profit": best_profit,
            "trades_count": best_trades,
            "combinations_tested": len(combinations)
        }
