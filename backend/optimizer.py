import asyncio
import logging
import itertools
from typing import Dict, Any, List

log = logging.getLogger("optimizer")

class StrategyOptimizer:
    """
    Espaços de busca de parâmetros por estratégia (apenas dados).

    A OTIMIZAÇÃO ESTÁ DESATIVADA. O método antigo escolhia o maior lucro em 500 candles com o motor "clássico" (sem taxas,
    slippage nem stops), exigindo só 3 operações, e a tela aplicava o resultado nos parâmetros do bot. Reativar exige o
    motor estrito (backtest_service), custos e validação fora da amostra.
    """

    def __init__(self, strategy_id: str):
        self.strategy_id = strategy_id
        
        # Define o espaço de busca para cada estratégia
        self.search_space = {
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
            "TF005": {
                "ema_trend_len": [34, 50, 89, 144],
            },
            "TF006": {
                "adx_threshold": [20, 25, 30],
                "breakout_weeks": [1, 4, 8, 13],
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
                "max_dist_pct": [0.0, 10.0, 20.0],
                "exit_buffer_pct": [0.0, 2.0],
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
            "TF017": {
                "supertrend_factor": [2.0, 3.0, 4.0],
                "commit_buffer": [0.25, 0.5, 0.75],
                "sl_mult": [1.5, 2.0, 3.0],
                "tp1_rr": [1.5, 2.0, 3.0],
            },
            "TF018": {
                "rsi_oversold": [5, 10, 20],
                "keltner_mult": [2.0, 2.5, 3.0],
                "regime_threshold": [20.0, 25.0, 30.0],
                "tp1_rr": [1.5, 2.0, 3.0],
            },
            "TF015": {
                "ema_period": [5, 9, 13],
                "confirm_candles": [1, 2, 3],
                "sl_mult": [1.5, 2.0, 3.0],
                "tp1_rr": [1.0, 2.0, 3.0],
            },
            "MR001": {
                "ema_period": [55, 80, 120],
                "signal_period": [3, 5, 8],
                "buy_zone_pct": [4.0, 7.0, 10.0],
                "sl_mult": [2.0, 2.5, 3.0],
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
            "RG005": {
                "lookback": [14, 20, 30],
                "bull_threshold": [3.0, 5.0, 8.0],
                "bear_threshold": [-3.0, -5.0, -8.0],
                "sl_mult": [1.5, 2.0, 3.0],
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
