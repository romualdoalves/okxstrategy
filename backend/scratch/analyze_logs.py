import os

def check_logs():
    # Procura pelo arquivo de log do backend (ajuste o caminho se necessário)
    log_file = "backend/logs/app.log" # Ou o caminho padrão que definimos
    if not os.path.exists(log_file):
        print(f"❌ Arquivo de log não encontrado em {log_file}")
        return

    print(f"📄 Analisando os últimos erros da OKX em {log_file}...\n")
    
    with open(log_file, "r") as f:
        lines = f.readlines()
        # Filtra por mensagens de erro da OKX ou falhas de ordem
        relevant_lines = [l for l in lines if "okx" in l.lower() and ("error" in l.lower() or "falhou" in l.lower() or "rejected" in l.lower())]
        
        for l in relevant_lines[-20:]: # Mostra os últimos 20 erros
            print(l.strip())

if __name__ == "__main__":
    check_logs()
