import sys
import os
sys.path.append(os.getcwd())
from backend.database import SessionLocal, BotModel

def list_all_bots():
    db = SessionLocal()
    try:
        bots = db.query(BotModel).all()
        print(f"{'ID':<5} | {'Nome':<25} | {'Símbolo':<12} | {'Ativo':<6}")
        print("-" * 60)
        for b in bots:
            print(f"{b.id:<5} | {b.name:<25} | {b.symbol:<12} | {b.active:<6}")
    finally:
        db.close()

if __name__ == "__main__":
    list_all_bots()
