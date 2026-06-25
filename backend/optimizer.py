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
            "TF001": {
                "ema_fast": [13, 21, 34],
                "ema_slow": [55, 89],
                "atr_mult_sl": [1.2, 1.5, 2.0],
                "tp1_pct": [1.5, 2.0, 3.0],
            },
            "TF002": {
                "rsi_oversold": [25, 30, 35],
                "rsi_overbought": [65, 70, 75],
                "ma_period": [100, 200],
                "atr_mult_sl": [1.2, 1.5, 2.0],
            },
            "TF003": {
                "macd_fast": [8, 12],
                "macd_slow": [21, 26, 34],
                "macd_signal": [7, 9],
                "trend_ma": [100, 200],
            },
            "TF004": {
                "atr_period": [10, 14],
                "multiplier": [2.0, 3.0, 4.0],
                "rsi_min": [45, 50, 55],
                "sl_mult": [2.0, 2.5, 3.0],
            },
            "TF005": {
                "ema_trend_len": [34, 50, 89, 144],
            },
            "TF006": {
                "adx_threshold": [20, 25, 30],
                "ema_fast": [5, 8, 13],
                "ema_slow": [21, 34],
                "tp_multiplier": [1.5, 2.0, 3.0],
            },
            "TF007": {
                "bias_ema": [20, 30, 40],
                "rsi_threshold": [48.0, 50.0, 52.0],
                "rsi_buffer": [1.0, 2.0, 3.0],
                "allow_continuation": [0, 1],
                "sl_mult": [2.0, 2.5, 3.0],
                "tp1_rr": [1.5, 2.0, 3.0],
            },
            "TF008": {
                "ema_period": [13, 21, 34],
                "sl_mult": [2.0, 2.5, 3.0],
                "tp_rr": [3.0, 4.0, 5.0],
            },
            "TF009": {
                "fast_ma_period": [10, 20, 34],
                "slow_ma_period": [50, 89, 144],
                "ma_type": ["sma", "ema"],
                "atr_mult_sl": [1.5, 2.0, 2.5],
            },
            "TF010": {
                "ema_fast": [13, 21],
                "ema_med": [34, 55],
                "ema_slow": [144, 200],
                "tp_rr": [1.5, 2.0, 3.0],
            },
            "TF011": {
                "chop_max": [45.0, 50.0, 55.0],
                "vol_mult": [1.0, 1.2, 1.5],
                "min_gap_pct": [0.03, 0.05, 0.1],
                "sl_mult": [2.0, 2.5, 3.0],
            },
            "TF012": {
                "magnitude_length": [10, 14, 21],
                "smoothing_length": [5, 9, 13],
                "threshold_extreme": [1.2, 1.5, 2.0],
            },
            "TF013": {
                "bb_period": [20, 30, 50],
                "bb_std": [1.8, 2.0, 2.5],
                "vol_mult": [1.2, 1.5, 2.0],
                "atr_mult_sl": [1.2, 1.5, 2.0],
            },
            "TF014": {
                "ema_macro_period": [10, 20],
                "ema_micro_period": [34, 50, 89],
                "sl_mult": [1.5, 2.0, 2.5],
                "tp1_rr": [1.5, 2.0, 3.0],
            },
            "MR001": {
                "ema_period": [55, 80, 120],
                "signal_period": [3, 5, 8],
                "buy_zone_pct": [4.0, 7.0, 10.0],
                "sl_mult": [2.0, 2.5, 3.0],
            },
            "MR002": {
                "outer_length": [4, 6, 8],
                "outer_std": [3.0, 4.0, 5.0],
                "inner_length": [20, 30],
                "inner_std": [1.8, 2.0, 2.5],
                "rr_ratio": [2.0, 3.0],
            },
            "MR003": {
                "bb_length": [20, 30],
                "bb_std": [1.8, 2.0, 2.5],
                "rsi_period": [9, 14, 21],
                "ema_fast": [5, 9, 13],
            },
            "MR004": {
                "rsi_period": [7, 9, 14],
                "ema_rsi": [2, 3, 5],
                "wma_rsi": [14, 21, 30],
            },
            "MR005": {
                "rsi_length": [9, 14, 21],
                "bb_length": [20, 30, 50],
                "bb_std": [1.8, 2.0, 2.5],
                "squeeze_perc": [10, 20, 30],
                "rr_ratio": [2.0, 2.5, 3.0],
            },
            "PA001": {
                "ema_period": [21, 50, 100],
                "pivot_lookback": [3, 4, 5],
                "ema_proximity_mult": [0.8, 1.0, 1.3],
                "tp_rr": [2.0, 3.0, 4.0],
            },
            "PA002": {
                "ema_period": [13, 21, 34],
                "min_ema_slope_pct": [0.04, 0.08, 0.12],
                "abcd_tolerance": [0.15, 0.25, 0.35],
                "min_rr": [1.0, 1.2, 1.5],
            },
            "PA003": {
                "range_lookback": [50, 80, 120],
                "edge_zone_pct": [15.0, 20.0, 25.0],
                "min_rr": [1.5, 2.0, 2.5],
                "min_range_atr": [1.5, 2.0, 3.0],
            },
            "PA004": {
                "ema_trend": [100, 200],
                "rr_trend": [1.0, 1.5],
                "rr_reversal": [2.0, 3.0],
                "rsi_threshold": [25, 30, 35],
            },
            "PA005": {
                "support_window": [3, 4, 5],
                "sweep_lookback": [15, 20, 30],
                "wick_ratio": [0.4, 0.5, 0.6],
                "rr_ratio": [2.0, 3.0, 4.0],
            },
            "PA006": {
                "htf_swing_lookback": [15, 20, 30],
                "wick_ratio": [0.45, 0.55, 0.65],
                "choch_lookback": [20, 30, 40],
                "tp1_rr": [1.2, 1.5, 2.0],
            },
            "PA007": {
                "ema_fast": [13, 20, 34],
                "ema_slow": [50, 89],
                "zone_tolerance_pct": [2.0, 2.5, 3.0],
                "min_criteria_to_trade": [2, 3],
                "require_zone_or_choch": [1],
                "sl_mult": [1.5, 2.0, 2.5],
                "tp1_rr": [1.5, 2.0, 3.0],
            },
            "PA008": {
                "orb_minutes": [15, 30, 45],
                "max_volatility_range_pct": [1.0, 2.0, 3.0],
                "sl_mult": [1.5, 2.0, 2.5],
                "tp1_rr": [1.5, 2.0, 3.0],
            },
            "SC001": {
                "atr_mult_threshold": [0.15, 0.20, 0.30],
                "rejection_ratio": [1.5, 2.0, 2.5],
                "tp_rr": [1.5, 2.0, 3.0],
            },
            "SC002": {
                "min_body_break_pct": [0.01, 0.02, 0.05],
                "vwap_rejection_max_dist_pct": [0.5, 0.8, 1.2],
                "min_volume_ratio": [0.8, 1.0, 1.2],
                "rr_ratio": [1.2, 1.5, 2.0],
            },
            "SC003": {
                "window": [14, 20, 30],
                "std_dev": [1.5, 2.0, 2.5],
                "vol_threshold": [1.2, 1.5, 2.0],
            },
            "RG001": {
                "window": [14, 20, 30],
                "threshold": [0.03, 0.05, 0.08],
                "min_train": [120, 180, 252],
                "tp1_pct": [2.0, 3.0, 5.0],
            },
            "RG004": {
                "location_z": [0.25, 0.35, 0.5],
                "expansion_threshold": [1.2, 1.5, 2.0],
                "prob_threshold": [0.52, 0.55, 0.6],
                "tp1_rr": [1.5, 2.0, 3.0],
            },
            "IF003": {
                "atr_multiplier": [0.10, 0.15, 0.25],
                "vol_window": [80, 100, 150],
                "poc_tolerance": [0.006, 0.008, 0.010],
                "extended_poc_tolerance": [0.010, 0.012, 0.015],
                "min_criteria_to_trade": [2, 3],
                "rr_ratio": [1.5, 2.0, 3.0],
            },
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
        stake_usd = 100.0
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
