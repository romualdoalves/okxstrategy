import asyncio
import os
import sys
from unittest.mock import MagicMock

# Ajusta path para importar o backend
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.backtest_engine import BacktestEngine
from backend.exchanges.base import CandleBar

async def debug():
    print("--- INICIANDO DEBUG DE BACKTEST ---")
    
    # 1. Simula candles
    print("Simulando 200 candles...")
    mock_candles = [
        CandleBar(epoch=i, open=100.0, high=105.0, low=95.0, close=102.0, volume=10.0)
        for i in range(200)
    ]
    
    # 2. Tenta inicializar engine (estratégia S006 - Double Bollinger)
    print("Inicializando Engine (S006)...")
    try:
        engine = BacktestEngine("S006", {"outer_length": 20, "outer_std": 2.0})
        print("✅ Engine inicializado!")
    except Exception as e:
        print(f"❌ FALHA na inicialização: {e}")
        return

    # 3. Tenta rodar
    print("Executando simulação...")
    try:
        results = await engine.run(mock_candles, stake_usd=100.0)
        print("✅ Simulação concluída com sucesso!")
        print(f"Resultado: {results.get('total_profit')} USD")
    except Exception as e:
        import traceback
        print("❌ FALHA na execução:")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(debug())
