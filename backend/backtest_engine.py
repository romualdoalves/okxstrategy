import pandas as pd
import logging
from typing import List, Dict, Any, Optional
from .strategies.registry import get_strategy
from .strategies.base import Signal

log = logging.getLogger("backtest_engine")

class BacktestEngine:
    def __init__(self, strategy_id: str, params: dict = None):
        try:
            self.strategy = get_strategy(strategy_id)
            if params:
                self.strategy.set_params(params)
            log.info(f"Engine inicializado para estratégia: {strategy_id}")
        except Exception as e:
            log.error(f"Erro ao inicializar estratégia no engine: {e}")
            raise e
        
        self.balance = 1000.0
        self.trades = []

    async def run(self, candles: list, stake_usd: float = 100.0):
        """
        Executa a simulação. 
        'candles' deve ser uma lista de objetos CandleBar (o mesmo que a estratégia usa).
        """
        stake_usd = 100.0
        self.balance = 1000.0
        self.trades = []
        position = None
        entry_price = 0.0
        
        if not candles or len(candles) < 150:
            return {
                "total_profit": 0, "trades_count": 0, 
                "final_balance": self.balance, "trades": [],
                "error": "Dados insuficientes para backtest (mínimo 150 candles)"
            }

        # Loop de simulação
        warmup = 100
        for i in range(warmup, len(candles)):
            current_slice = candles[:i+1]
            current_price = candles[i].close
            
            try:
                result = self.strategy.compute(current_slice)
                if not result:
                    continue

                # Lógica de Execução Simplificada
                if position is None:
                    if result.signal == Signal.BUY:
                        position = 'long'
                        entry_price = current_price
                        self.trades.append({
                            "type": "ENTRY LONG",
                            "price": entry_price,
                            "time": i,
                            "reason": result.hold_reason
                        })
                    elif result.signal == Signal.SELL:
                        position = 'short'
                        entry_price = current_price
                        self.trades.append({
                            "type": "ENTRY SHORT",
                            "price": entry_price,
                            "time": i,
                            "reason": result.hold_reason
                        })
                
                elif position == 'long' and (result.signal == Signal.SELL or (result.hold_reason and "Saída" in result.hold_reason)):
                    profit = (current_price - entry_price) / entry_price * stake_usd
                    self.balance += profit
                    self.trades.append({
                        "type": "EXIT LONG",
                        "price": current_price,
                        "profit": profit,
                        "balance": self.balance,
                        "reason": result.hold_reason
                    })
                    position = None
                    
                elif position == 'short' and (result.signal == Signal.BUY or (result.hold_reason and "Saída" in result.hold_reason)):
                    profit = (entry_price - current_price) / entry_price * stake_usd
                    self.balance += profit
                    self.trades.append({
                        "type": "EXIT SHORT",
                        "price": current_price,
                        "profit": profit,
                        "balance": self.balance,
                        "reason": result.hold_reason
                    })
                    position = None
            except Exception as e:
                log.error(f"Erro no passo {i} do backtest: {e}")
                continue

        return {
            "total_profit": self.balance - 1000.0,
            "trades_count": len(self.trades),
            "final_balance": self.balance,
            "trades": self.trades
        }
