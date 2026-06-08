import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from backend.database import SessionLocal, BotModel

def list_params():
    db = SessionLocal()
    try:
        bots = db.query(BotModel).filter(BotModel.active == True).all()
        print(f"{'NOME DO BOT':<25} | {'GATILHO STS':<12} | {'PASSO STS':<10}")
        print("-" * 55)
        for b in bots:
            p = b.strategy_params or {}
            # Busca os nomes comuns de parâmetros de Trailing Stop
            trigger = p.get("sts_activation_pct") or p.get("sts_trigger_pct") or "---"
            step = p.get("sts_step_pct") or "---"
            
            print(f"{b.name:<25} | {str(trigger):<12}% | {str(step):<10}%")
    finally:
        db.close()

if __name__ == "__main__":
    list_params()
