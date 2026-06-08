#!/usr/bin/env bash
# =============================================================================
# pre-deploy-check.sh — Verifica posições abertas antes de deployar
#
# USO:
#   bash pre-deploy-check.sh          # interativo (pede confirmação)
#   FORCE=true bash pre-deploy-check.sh   # ignora posições abertas (CI/CD)
#
# RETORNO:
#   0 — seguro para deployar
#   1 — deploy cancelado (posições abertas e usuário negou)
#
# O deploy.sh chama este script automaticamente antes de fazer o build.
# =============================================================================

set -euo pipefail

DOMAIN="${DOMAIN:-okx.tradixio.com}"
APP_URL="https://${DOMAIN}"
FORCE="${FORCE:-false}"
COMPOSE_FILE="/opt/okx-strategy/docker-compose.prod.yml"

# ── Cores ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log_ok()   { echo -e "${GREEN}[OK]${RESET}   $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${RESET} $*"; }
log_err()  { echo -e "${RED}[ERRO]${RESET} $*"; }
log_info() { echo -e "${CYAN}[INFO]${RESET} $*"; }

echo ""
echo -e "${BOLD}════════════════════════════════════════${RESET}"
echo -e "${BOLD}  PRÉ-DEPLOY: Verificação de Posições${RESET}"
echo -e "${BOLD}════════════════════════════════════════${RESET}"
echo ""

# ── Verifica se o container está rodando ────────────────────────────────────
if ! docker ps --format "{{.Names}}" 2>/dev/null | grep -q "^okx_strategy$"; then
    log_info "Container okx_strategy não está rodando — primeiro deploy ou container parado."
    log_ok "Nenhuma verificação necessária. Prosseguindo com o deploy."
    echo ""
    exit 0
fi

log_info "Container okx_strategy ativo. Verificando posições abertas via API..."

# ── Consulta a API de posições abertas ───────────────────────────────────────
RESPONSE=$(curl -sf --max-time 10 "${APP_URL}/api/bots/performance/active" 2>/dev/null || echo "ERRO")

if [ "$RESPONSE" = "ERRO" ] || [ -z "$RESPONSE" ]; then
    log_warn "API inacessível (${APP_URL}/api/bots/performance/active)."
    log_warn "Não foi possível verificar posições abertas automaticamente."
    echo ""
    if [ "$FORCE" = "true" ]; then
        log_warn "FORCE=true — prosseguindo sem verificação."
        exit 0
    fi
    echo -e "  Certifique-se manualmente de que ${BOLD}não há posições abertas${RESET} na OKX antes de continuar."
    echo -n "  Continuar mesmo assim? (s/N): "
    read -r confirm
    if [[ "$confirm" =~ ^[Ss]$ ]]; then
        log_warn "Continuando por decisão do usuário — monitore as posições após o restart."
        exit 0
    fi
    log_err "Deploy cancelado pelo usuário."
    exit 1
fi

# ── Conta e exibe as posições abertas ────────────────────────────────────────
COUNT=$(echo "$RESPONSE" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(len(data))
except:
    print(0)
" 2>/dev/null || echo "0")

if [ "$COUNT" = "0" ] || [ -z "$COUNT" ]; then
    log_ok "Nenhuma posição aberta detectada. Deploy seguro."
    echo ""
    exit 0
fi

# ── Há posições abertas ───────────────────────────────────────────────────────
echo -e "${YELLOW}${BOLD}"
echo "  ┌──────────────────────────────────────────────────────────────┐"
echo "  │  ⚠️   ATENÇÃO: ${COUNT} POSIÇÃO(ÕES) ABERTA(S) DETECTADA(S)      │"
echo "  └──────────────────────────────────────────────────────────────┘"
echo -e "${RESET}"

echo "$RESPONSE" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for item in data:
    name     = item.get('name', '?')
    symbol   = item.get('symbol', '?')
    direc    = item.get('direction', '?')
    entry    = item.get('entry_price', 0)
    last     = item.get('last_price', 0)
    pnl_usd  = item.get('pnl_usd', 0)
    pnl_pct  = item.get('pnl_pct', 0)
    sign     = '+' if pnl_usd >= 0 else ''
    print(f'  • {name}')
    print(f'    Símbolo: {symbol}  |  Direção: {direc}')
    print(f'    Entrada: \${entry:.4f}  →  Atual: \${last:.4f}')
    print(f'    PnL: {sign}\${pnl_usd:.2f}  ({sign}{pnl_pct:.2f}%)')
    print()
" 2>/dev/null

echo -e "${YELLOW}  Motivo do risco:${RESET}"
echo "  O deploy faz  docker compose down  →  up, reiniciando o processo."
echo "  Se um fill de saída chegar durante o downtime (~60s), o banco"
echo "  não registra e a posição fica 'aberta' no DB com contexto errado."
echo "  Isso pode causar divergência DB↔OKX e SL desatualizado (como"
echo "  o problema encontrado hoje com B001/ETH)."
echo ""
echo -e "${BOLD}  Recomendação: feche as posições manualmente na OKX antes de deployar.${RESET}"
echo ""

if [ "$FORCE" = "true" ]; then
    log_warn "FORCE=true — prosseguindo com posições abertas. Monitore após o restart."
    echo ""
    exit 0
fi

echo -n "  Continuar mesmo assim? (s/N): "
read -r confirm
echo ""

if [[ "$confirm" =~ ^[Ss]$ ]]; then
    log_warn "Deploy com posições abertas — monitore os logs após o restart:"
    echo "  docker logs -f okx_strategy | grep 'Bot [0-9]'"
    echo ""
    exit 0
fi

log_err "Deploy cancelado. Feche as posições e execute novamente: bash deploy.sh"
echo ""
exit 1
