#!/usr/bin/env bash
# migrate_factory_to_git.sh — traz para o repositório as estratégias que só existem no VPS.
#
# O git é a fonte única do código das estratégias. Estratégias criadas pela Fábrica ficam no disco do container
# (e, nas versões antigas, numa cópia em strategies.code_py); este script copia para backend/strategies/factory/
# tudo que o repositório ainda não tem e tenta commitar e dar push.
#
# Uso (no VPS):   cd /opt/okx-strategy && git pull && bash scripts/migrate_factory_to_git.sh
# Idempotente: sem nada novo, não faz nada.
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/okx-strategy}"
CONTAINER="${CONTAINER:-okx_strategy}"
DB_CONTAINER="${DB_CONTAINER:-okx_strategy_db}"
DB_USER="${DB_USER:-crypto}"
DB_NAME="${DB_NAME:-okx_strategy}"
DEST="${DEPLOY_DIR}/backend/strategies/factory"
PATTERN='^([a-z]{2}[0-9]{3}|t000)\.py$'

[ -d "${DEST}" ] || { echo "[ERRO] ${DEST} não existe. Rode a partir do clone do repositório no VPS."; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
NEW=()

# 1) Arquivos que existem no container (verdade em execução) e não estão no repositório.
if docker ps --format '{{.Names}}' | grep -qx "${CONTAINER}"; then
    docker cp "${CONTAINER}:/app/backend/strategies/factory/." "${TMP}/"
    for f in "${TMP}"/*.py; do
        [ -e "$f" ] || continue
        b="$(basename "$f")"
        [[ "$b" =~ $PATTERN ]] || continue
        if [ ! -f "${DEST}/${b}" ]; then
            cp "$f" "${DEST}/${b}"
            NEW+=("${b}")
            echo "[+] ${b}  (container)"
        fi
    done
else
    echo "[AVISO] Container ${CONTAINER} não está rodando; só o banco será consultado."
fi

# 2) Estratégias só no banco (code_py) sem arquivo em lugar nenhum (ex.: desativadas).
IDS="$(docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${DB_NAME}" -At \
        -c "select lower(strategy_id) from strategies where code_py is not null and code_py <> ''" 2>/dev/null || true)"
for id in ${IDS}; do
    b="${id}.py"
    [[ "$b" =~ $PATTERN ]] || continue
    if [ ! -f "${DEST}/${b}" ]; then
        docker exec "${DB_CONTAINER}" psql -U "${DB_USER}" -d "${DB_NAME}" -At \
            -c "select code_py from strategies where lower(strategy_id)='${id}'" > "${DEST}/${b}"
        NEW+=("${b}")
        echo "[+] ${b}  (somente no banco)"
    fi
done

# 2b) Arquivos já copiados numa execução anterior, mas ainda sem commit (ex.: o push falhou por falta de credencial).
cd "${DEPLOY_DIR}"
while IFS= read -r f; do
    b="$(basename "$f")"
    [[ "$b" =~ $PATTERN ]] || continue
    case " ${NEW[*]:-} " in *" ${b} "*) ;; *) NEW+=("${b}"); echo "[+] ${b}  (já copiado, sem commit)";; esac
done < <(git ls-files --others --exclude-standard -- backend/strategies/factory)

if [ ${#NEW[@]} -eq 0 ]; then
    echo "Nada a migrar: todas as estratégias já estão no repositório."
    exit 0
fi

echo
echo "${#NEW[@]} arquivo(s) copiado(s) para ${DEST}."

# 3) Commit + push (precisa de credencial de escrita no GitHub; se não houver, deixa os arquivos prontos).
git add -- "${NEW[@]/#/backend/strategies/factory/}"
if git -c user.name="VPS migrate" -c user.email="vps@okx-strategy.local" \
       commit -q -m "Migrate Factory strategies from the VPS into git (single source of truth): ${NEW[*]}" \
   && git push -q origin HEAD:main; then
    echo "[OK] Commit e push feitos. Atualize seu clone local com: git pull"
else
    git reset -q --mixed HEAD~1 2>/dev/null || true       # mantém os arquivos, desfaz só o commit local
    TAR="/tmp/factory_migrated.tgz"
    tar czf "${TAR}" -C "${DEST}" "${NEW[@]}"
    echo "[AVISO] Push não foi possível daqui. Os arquivos estão em ${DEST} (não rastreados) e em ${TAR}."
    echo "        No seu PC:  scp root@<IP-DO-VPS>:${TAR} .   e envie o arquivo para o Claude / extraia na pasta do projeto."
    echo "        NÃO apague os arquivos antes de commitá-los."
fi
