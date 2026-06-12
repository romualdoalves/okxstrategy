import os
import sys

# Garante que as importações funcionem se executado da raiz do projeto (/app)
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from backend.database import SessionLocal, StrategyModel

def recover():
    db = SessionLocal()
    factory_dir = os.path.join("backend", "strategies", "factory")
    os.makedirs(factory_dir, exist_ok=True)
    
    # Adicionando __init__.py vazio para garantir que o python trate como pacote
    init_file = os.path.join(factory_dir, "__init__.py")
    if not os.path.exists(init_file):
        with open(init_file, "w") as f:
            f.write("")
            
    strategies = db.query(StrategyModel).filter(StrategyModel.code_py.isnot(None)).all()
    
    count = 0
    for strat in strategies:
        # Apenas reescreve se for um ID típico de fábrica
        if len(strat.strategy_id) >= 5 and strat.strategy_id[:2].upper() in ("TF", "MR", "PA", "SC", "RG", "IF", "NW", "T"):
            filename = f"{strat.strategy_id.lower()}.py"
            filepath = os.path.join(factory_dir, filename)
            
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(strat.code_py)
            print(f"[OK] Recuperado: {filename}")
            count += 1
            
    print(f"\nRecuperação concluída: {count} arquivo(s) recriado(s) fisicamente no disco.")
    db.close()

if __name__ == "__main__":
    recover()
