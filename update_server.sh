#!/bin/bash
# Execute este script DIRETAMENTE NO SERVIDOR Hostinger
# cd /opt/okx-strategy && bash update_server.sh

set -e

CONTAINER="okx_strategy"

echo "========================================"
echo "  Atualizando bot_manager.py"
echo "========================================"

# Faz backup do arquivo atual
echo "[1/3] Criando backup..."
docker exec ${CONTAINER} cp /app/backend/bot_manager.py /app/backend/bot_manager.py.bak.$(date +%s)

# Aplica as correções diretamente no container usando sed
echo "[2/3] Aplicando correções..."

# Correção 1: _recover_state() - sempre recria trailing stop
docker exec ${CONTAINER} sed -i \
    -e 's/if last_trade.tp1_done:/# Trailing stop sempre ativo para posições abertas/' \
    -e 's/self._tp1_done          = True/self._tp1_done          = bool(last_trade.tp1_done)/' \
    -e 's/self._ts_algo_id        = "sw"/self._ts_algo_id        = "sw"/' \
    -e 's/log.info("\[Bot %d\] Estado recuperado do banco: %s @ %.2f (Trade ID: %d) — Sombra reativada (sl=%.4f peak=%.4f)",/log.info("[Bot %d] Estado recuperado do banco: %s @ %.2f (Trade ID: %d) — Sombra reativada (sl=%.4f peak=%.4f tp1_done=%s)",/' \
    -e 's/self._current_trade_id, self._sl_price, self._peak_price)/self._current_trade_id, self._sl_price, self._peak_price, self._tp1_done)/' \
    /app/backend/bot_manager.py

# Correção 2: Orphan recovery - calcula TP1
docker exec ${CONTAINER} sed -i \
    -e 's/"tp1_price":   0.0,/"tp1_price":   adopted_px * (1.02 if adopted_dir == 1 else 0.98),/' \
    /app/backend/bot_manager.py

# Verifica sintaxe
echo "[3/3] Verificando sintaxe..."
docker exec ${CONTAINER} python -m py_compile /app/backend/bot_manager.py

echo "========================================"
echo "  Correções aplicadas!"
echo "  Restartando container..."
echo "========================================"

docker restart ${CONTAINER}

echo ""
echo "Aguarde 10 segundos e teste:"
echo "  curl https://okx.tradixio.com/api/monitor"
