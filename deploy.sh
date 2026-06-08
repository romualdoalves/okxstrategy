#!/usr/bin/env bash
# =============================================================================
# OKXStrategy — Deploy Script for Hostinger VPS KVM4
# Domain: https://okx.tradixio.com
# Deploy path: /opt/okx-strategy
# =============================================================================
set -euo pipefail

DEPLOY_DIR="/opt/okx-strategy"
REPO_URL="https://github.com/romualdoalves/okxstrategy.git"
DOMAIN="okx.tradixio.com"

echo "========================================"
echo "  OKXStrategy Deploy Script"
echo "  Target: ${DOMAIN}"
echo "  Path:   ${DEPLOY_DIR}"
echo "========================================"

# ── 1. Ensure Docker + Docker Compose are installed ──────────────────────────
if ! command -v docker &>/dev/null; then
    echo "[ERROR] Docker not installed. Install first:"
    echo "  curl -fsSL https://get.docker.com | sh"
    exit 1
fi

if ! docker compose version &>/dev/null && ! docker-compose version &>/dev/null; then
    echo "[ERROR] Docker Compose not installed."
    exit 1
fi

COMPOSE_CMD="docker compose"
if ! docker compose version &>/dev/null; then
    COMPOSE_CMD="docker-compose"
fi

# ── 2. Ensure Traefik network exists ─────────────────────────────────────────
if ! docker network ls | grep -q "traefik-public"; then
    echo "[INFO] Creating traefik-public network..."
    docker network create traefik-public
else
    echo "[INFO] traefik-public network already exists."
fi

# ── 3. Ensure Traefik is running ─────────────────────────────────────────────
if ! docker ps --format '{{.Names}}' | grep -q "^traefik$"; then
    # Container existe mas está parado? Start ele.
    if docker ps -a --format '{{.Names}}' | grep -q "^traefik$"; then
        echo "[INFO] Traefik container exists but stopped. Starting..."
        docker start traefik
    else
        echo "[WARN] Traefik not found. Creating..."
        mkdir -p /opt/traefik
        cat > /opt/traefik/docker-compose.yml <<'TRAEFIK_EOF'
services:
  traefik:
    image: traefik:v3.0
    container_name: traefik
    restart: unless-stopped
    command:
      - --api.insecure=false
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --entrypoints.web.address=:80
      - --entrypoints.websecure.address=:443
      - --certificatesresolvers.myresolver.acme.tlschallenge=true
      - --certificatesresolvers.myresolver.acme.email=admin@tradixio.com
      - --certificatesresolvers.myresolver.acme.storage=/letsencrypt/acme.json
      - --log.level=INFO
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /opt/traefik/letsencrypt:/letsencrypt
    networks:
      - traefik-public

networks:
  traefik-public:
    external: true
TRAEFIK_EOF
        mkdir -p /opt/traefik/letsencrypt
        touch /opt/traefik/letsencrypt/acme.json
        chmod 600 /opt/traefik/letsencrypt/acme.json
        ${COMPOSE_CMD} -f /opt/traefik/docker-compose.yml up -d
    fi
    echo "[INFO] Traefik ready."
else
    echo "[INFO] Traefik already running."
fi

# ── 4. Clone or update repository ────────────────────────────────────────────
if [ -d "${DEPLOY_DIR}/.git" ]; then
    echo "[INFO] Updating existing repo..."
    git -C "${DEPLOY_DIR}" fetch origin
    git -C "${DEPLOY_DIR}" reset --hard origin/main || git -C "${DEPLOY_DIR}" reset --hard origin/master
else
    echo "[INFO] Cloning repository..."
    mkdir -p "${DEPLOY_DIR}"
    git clone "${REPO_URL}" "${DEPLOY_DIR}"
fi

# ── 5. Ensure .env exists ────────────────────────────────────────────────────
if [ ! -f "${DEPLOY_DIR}/.env" ]; then
    echo "[WARN] .env not found. Creating from template..."
    if [ -f "${DEPLOY_DIR}/.env.production.example" ]; then
        cp "${DEPLOY_DIR}/.env.production.example" "${DEPLOY_DIR}/.env"
    else
        cat > "${DEPLOY_DIR}/.env" <<'ENV_EOF'
# OKXStrategy Production Environment
# OKX API credentials are saved through the app connection flow, not .env.

EXCHANGE_PROVIDER=okx
OKX_DEMO=true

DEEPSEEK_API_KEY=
ETHERSCAN_API_KEY=

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=6753071411

POSTGRES_PASSWORD=crypto
DATABASE_URL=postgresql://crypto:crypto@postgres:5432/okx_strategy
ENV_EOF
    fi
    echo "[WARN] Please edit ${DEPLOY_DIR}/.env with real credentials!"
fi

# ── 6. Ensure Docker volumes exist ───────────────────────────────────────────
if ! docker volume ls | grep -q "okx_strategy_pgdata"; then
    echo "[INFO] Creating Docker volume okx_strategy_pgdata..."
    docker volume create okx_strategy_pgdata
else
    echo "[INFO] Docker volume okx_strategy_pgdata already exists."
fi

# ── 6.1. Backup e restauração de estratégias da Fábrica ──────────────────────
FACTORY_BACKUP="${DEPLOY_DIR}/.factory_backup"
if docker ps | grep -q "okx_strategy"; then
    echo "[INFO] Fazendo backup das estratégias da Fábrica..."
    mkdir -p "${FACTORY_BACKUP}"
    docker cp okx_strategy:/app/backend/strategies/factory/. "${FACTORY_BACKUP}/" 2>/dev/null || true
fi

# ── 7. Pré-deploy: verificar posições abertas ────────────────────────────────
if [ -f "${DEPLOY_DIR}/pre-deploy-check.sh" ]; then
    bash "${DEPLOY_DIR}/pre-deploy-check.sh" || exit 1
fi

# Verificação inline: alerta se há posições abertas no bot
if docker ps | grep -q "okx_strategy"; then
    echo "[INFO] Checking for open positions in running bot..."
    OPEN_POS=$(docker exec okx_strategy python -c \
        "from backend.bot_manager import manager; \
         open_pos = manager.get_open_positions_summary(); \
         print(open_pos)" 2>/dev/null || echo "[]")

    if [ "${OPEN_POS}" != "[]" ] && [ -n "${OPEN_POS}" ]; then
        echo ""
        echo "========================================"
        echo "  ⚠️  ATENÇÃO: POSIÇÕES ABERTAS DETECTADAS"
        echo "========================================"
        echo "${OPEN_POS}"
        echo ""
        echo "Recomendação: feche as posições manualmente na OKX antes de deployar."
        echo "Alternativa: o deploy continuará e o bot fará reconciliação no startup."
        echo ""
        read -p "Continuar deploy mesmo assim? [y/N]: " CONFIRM
        if [[ ! "${CONFIRM}" =~ ^[Yy]$ ]]; then
            echo "[INFO] Deploy cancelado pelo usuário."
            exit 0
        fi
        echo "[WARN] Deploy continuando com posições abertas..."
    else
        echo "[INFO] Nenhuma posição aberta detectada."
    fi

    # Graceful shutdown antes de parar os containers
    echo "[INFO] Requesting graceful shutdown..."
    docker exec okx_strategy python -c \
        "from backend import graceful_shutdown; graceful_shutdown()" 2>/dev/null || true
    sleep 5
fi

# ── 8. Build and deploy ──────────────────────────────────────────────────────
echo "[INFO] Building and starting containers..."
${COMPOSE_CMD} -f "${DEPLOY_DIR}/docker-compose.prod.yml" pull 2>/dev/null || true
${COMPOSE_CMD} -f "${DEPLOY_DIR}/docker-compose.prod.yml" build --no-cache
${COMPOSE_CMD} -f "${DEPLOY_DIR}/docker-compose.prod.yml" up -d

# ── 8.5. Restaurar estratégias da Fábrica ────────────────────────────────────
STRATEGIES_CHANGED=false
if [ -d "${FACTORY_BACKUP}" ] && [ "$(ls -A "${FACTORY_BACKUP}"/*.py 2>/dev/null)" ]; then
    echo "[INFO] Restaurando estratégias da Fábrica no novo container..."
    sleep 5
    docker cp "${FACTORY_BACKUP}/." okx_strategy:/app/backend/strategies/factory/
    rm -rf "${FACTORY_BACKUP}"
    STRATEGIES_CHANGED=true
    echo "[OK] Estratégias da Fábrica restauradas."
fi

# ── 8.6. Sincronizar estratégias do código-fonte (nativas migradas) ───────────
# Garante que estratégias nativas adicionadas ao repo também estejam presentes
# caso a restauração acima tenha sobrescrito alguma versão no container.
FACTORY_SRC="${DEPLOY_DIR}/backend/strategies/factory"
if [ -d "${FACTORY_SRC}" ]; then
    echo "[INFO] Sincronizando estratégias do código-fonte..."
    docker cp "${FACTORY_SRC}/." okx_strategy:/app/backend/strategies/factory/
    STRATEGIES_CHANGED=true
    echo "[OK] Estratégias sincronizadas."
fi

if [ "${STRATEGIES_CHANGED}" = true ]; then
    echo "[INFO] Reiniciando app para recarregar registry de estratégias..."
    docker restart okx_strategy >/dev/null
fi

# ── 9. Wait for healthcheck ──────────────────────────────────────────────────
echo "[INFO] Waiting for app healthcheck..."
for i in {1..30}; do
    if docker inspect --format='{{.State.Health.Status}}' okx_strategy 2>/dev/null | grep -q "healthy"; then
        echo "[OK] App is healthy!"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "[WARN] App healthcheck timed out. Check logs:"
        echo "  docker logs okx_strategy"
    fi
    sleep 2
done

# ── 10. Show status ──────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "  DEPLOYMENT STATUS"
echo "========================================"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "(NAMES|okx|traefik)" || docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
echo ""
echo "App URL:    https://${DOMAIN}"
echo "Health:     https://${DOMAIN}/api/health"
echo "Logs:       docker logs -f okx_strategy"
echo "DB logs:    docker logs -f okx_strategy_db"
echo ""
echo "If you see 404, check:"
echo "  1. Is the container healthy?  docker ps"
echo "  2. Traefik labels correct?    docker inspect okx_strategy | grep traefik"
echo "  3. App logs:                  docker logs okx_strategy"
echo "========================================"
