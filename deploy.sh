#!/usr/bin/env bash
# =============================================================================
# OKXStrategy — Deploy Script for Hostinger VPS KVM4
# Domain: https://okx.tradixio.com
# Deploy path: /opt/okx-strategy
# =============================================================================
set -euo pipefail

# `comando | grep -q` é frágil com `set -o pipefail`: o grep fecha o pipe assim que acha o texto, o comando
# recebe SIGPIPE e o pipeline "falha" de forma intermitente (ex.: tentava recriar uma rede que já existia).
# Aqui a saída é capturada primeiro e só depois filtrada.
has_line() {
    local pattern="$1"; shift
    local out
    out="$("$@" 2>/dev/null || true)"
    grep -q -- "$pattern" <<<"$out"
}

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
if ! has_line "traefik-public" docker network ls; then
    echo "[INFO] Creating traefik-public network..."
    docker network create traefik-public
else
    echo "[INFO] traefik-public network already exists."
fi

# ── 3. Ensure Traefik is running ─────────────────────────────────────────────
if ! has_line "^traefik$" docker ps --format '{{.Names}}'; then
    # Container existe mas está parado? Start ele.
    if has_line "^traefik$" docker ps -a --format '{{.Names}}'; then
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
if ! has_line "okx_strategy_pgdata" docker volume ls; then
    echo "[INFO] Creating Docker volume okx_strategy_pgdata..."
    docker volume create okx_strategy_pgdata
else
    echo "[INFO] Docker volume okx_strategy_pgdata already exists."
fi

# ── 6.1. Estratégias da Fábrica só no container? (git é a fonte única do código) ─
# Toda estratégia precisa estar no repositório: a imagem é reconstruída e o container recriado, e o que
# existir só no disco do container se perderia. Para trazer essas estratégias para o git:
#   bash scripts/migrate_factory_to_git.sh
if has_line "okx_strategy" docker ps; then
    ONLY_IN_CONTAINER=""
    for f in $(docker exec okx_strategy sh -c 'ls /app/backend/strategies/factory' 2>/dev/null | grep -E '^([a-z]{2}[0-9]{3}|t000)\.py$' || true); do
        [ -f "${DEPLOY_DIR}/backend/strategies/factory/${f}" ] && continue
        # Arquivo que já esteve no git e foi removido de propósito (estratégia excluída): não é "só do container".
        [ -n "$(git -C "${DEPLOY_DIR}" log --oneline -1 -- "backend/strategies/factory/${f}" 2>/dev/null)" ] && continue
        ONLY_IN_CONTAINER="${ONLY_IN_CONTAINER} ${f}"
    done
    if [ -n "${ONLY_IN_CONTAINER}" ]; then
        echo ""
        echo "[ERRO] Estratégias que existem só no container (fora do git):${ONLY_IN_CONTAINER}"
        echo "       Deploy ABORTADO para não perdê-las. Rode antes:"
        echo "         bash ${DEPLOY_DIR}/scripts/migrate_factory_to_git.sh"
        exit 1
    fi
fi

# ── 7. Pré-deploy: verificar posições abertas ────────────────────────────────
if [ -f "${DEPLOY_DIR}/pre-deploy-check.sh" ]; then
    bash "${DEPLOY_DIR}/pre-deploy-check.sh" || exit 1
fi

# Verificação inline: alerta se há posições abertas no bot
if has_line "okx_strategy" docker ps; then
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

# ── 9. Wait for healthcheck ──────────────────────────────────────────────────
echo "[INFO] Waiting for app healthcheck..."
for i in {1..30}; do
    if has_line "^healthy$" docker inspect --format='{{.State.Health.Status}}' okx_strategy; then
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
