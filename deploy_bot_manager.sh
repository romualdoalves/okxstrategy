#!/bin/bash
# Script para atualizar bot_manager.py no servidor via docker cp
# Execute na sua máquina local (Windows com Git Bash ou WSL)

set -e

SERVER="root@srv1616170"
REMOTE_DIR="/opt/okx-strategy"
CONTAINER="okx_strategy"

echo "========================================"
echo "  Deploy bot_manager.py"
echo "  Target: ${SERVER}"
echo "========================================"

# Verifica se o arquivo local existe
if [ ! -f "backend/bot_manager.py" ]; then
    echo "[ERRO] backend/bot_manager.py não encontrado"
    echo "Execute este script do diretório OKXStrategy/"
    exit 1
fi

echo "[1/4] Copiando arquivo para o servidor..."
scp backend/bot_manager.py ${SERVER}:${REMOTE_DIR}/backend/bot_manager.py

echo "[2/4] Copiando arquivo para dentro do container..."
ssh ${SERVER} "docker cp ${REMOTE_DIR}/backend/bot_manager.py ${CONTAINER}:/app/backend/bot_manager.py"

echo "[3/4] Verificando sintaxe..."
ssh ${SERVER} "docker exec ${CONTAINER} python -m py_compile /app/backend/bot_manager.py"

echo "[4/4] Restartando container..."
ssh ${SERVER} "docker restart ${CONTAINER}"

echo ""
echo "========================================"
echo "  Deploy concluído!"
echo "  Aguarde 10 segundos e teste:"
echo "  curl https://okx.tradixio.com/api/monitor"
echo "========================================"
