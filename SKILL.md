---
name: okxstrategy
description: |
  OKXStrategy - Algorithmic crypto trading application for OKX only.

  Use this skill when asked about: OKXStrategy, OKX trading bots, strategies,
  Telegram operational alerts, OKX credentials via database, Docker/Traefik
  deployment, Hostinger VPS, or okx.tradixio.com.
---

# OKXStrategy - Project Status

**Date of this snapshot:** 2026-06-08
**Exchange:** OKX only
**Active API Key:** armazenada fora do repositório
**User location:** Brazil
**Deploy target:** Hostinger VPS KVM4 via Docker + Traefik
**Domain:** https://okx.tradixio.com
**DNS/Subdomínio:** `okx.tradixio.com` já foi criado no VPS.
**Repository:** https://github.com/romualdoalves/okxstrategy
**Deploy path:** `/opt/okx-strategy`
**GitHub Token:** nunca documentar tokens reais no repositório
**Key ID in use:** armazenado fora do repositório

---

## Changelog Recente

### 36. feat: Recomendação automática pós-backtest (2026-06-08)

- `frontend/src/pages/BotDetail.jsx` — bloco de recomendação aparece automaticamente após executar o backtest.
- Lógica em `getBacktestRecommendation()` (frontend puro, sem chamada de API):
  - **NÃO INICIAR** (vermelho): zero trades, PnL ≤ 0, ou Profit Factor < 1.0.
  - **CUIDADO** (amarelo): PnL positivo mas PF < 1.2, ou < 3 trades fechados, ou drawdown > lucro.
  - **INICIAR** (verde): PnL positivo, PF ≥ 1.2, ≥ 3 trades, drawdown ≤ lucro.
- Badge colorido com veredicto + bullets explicando cada ponto (positivos e problemas separados).
- Ícones lucide-react: `CheckCircle` / `AlertTriangle` / `XCircle`.
- Backtest já estava corrigido para SPOT-only desde commit `404df82`.

---

## REGRA ABSOLUTA — OKXStrategy Operacional

Esta aplicação deve ser tratada como **OKXStrategy**. Trabalhe somente no
repositório `E:\Dell Inspiron\W\Dev\Trading\OKXTrader\OKXStrategy`.

### Ritual obrigatório de entrega
- Sempre atualizar este `SKILL.md` quando houver mudança operacional, deploy,
  regra de segurança, banco de dados ou fluxo de trabalho.
- Sempre commitar e enviar as alterações para o GitHub antes de responder com
  comandos de deploy.
- Ao final de pedidos de deploy, responder somente com os comandos para executar
  no VPS.
- Não incluir `mkdir -p /opt/okx-strategy` nos comandos de deploy futuros: a
  pasta já foi criada no VPS.
- Nunca usar ou tocar no diretório raiz
  `E:\Dell Inspiron\W\Dev\Trading\OKXTrader\OKXStrategy`.

### Conexão OKX via banco
- As credenciais operacionais OKX devem ser cadastradas pela aplicação, via:
  - `GET /api/account/connection`
  - `POST /api/account/connect`
  - `POST /api/account/disconnect`
- `OKX_API_KEY` e `OKX_API_SECRET` não podem ser usados como dependência
  operacional no `.env`.
- `OKXExchange` e `OKXPrivateStream` devem ler chaves exclusivamente da tabela
  `settings`, com valores criptografados.
- Ao conectar uma nova conta OKX, o usuário deve confirmar a limpeza operacional
  local. A aplicação apaga bots, trades, signal logs, snapshots, relatórios e
  rejeições locais antes de salvar as novas chaves.
- Desconectar OKX remove as chaves do banco e para bots ativos.

### Regra OKX-only para estratégias
- Todas as estratégias devem operar somente na OKX.
- `EXCHANGE_PROVIDER` diferente de `okx` deve ser rejeitado pela aplicação.
- Não adicionar novas dependências operacionais, adapters, streams, rotas ou
  parâmetros que executem ordens em outra corretora.
- A fonte CEX local da estratégia A006 é OKX (`okx_price`, `use_okx_only`).
- O adapter legado de outra corretora foi removido do repositório OKXStrategy.

### Regra absoluta de stake fixo
- Toda entrada de qualquer bot deve usar stake fixo de **US$100**.
- O stake não é configurável no frontend, API, banco, backtest, otimização ou
  adapter da OKX.
- Qualquer `stake_usd` enviado por cliente/API deve ser ignorado ou sobrescrito
  para `100.0`.
- Migrações de startup devem forçar todos os bots existentes para
  `stake_usd = 100.0`.
- O monitor de conformidade deve marcar divergência quando uma posição aberta na
  OKX tiver notional real fora da tolerância do stake fixo de US$100, mesmo que
  direção e quantidade local estejam sincronizadas.
- Saldos spot órfãos encontrados na OKX só podem ser adotados por um bot se o
  notional estimado estiver dentro da tolerância do stake fixo de US$100. Saldos
  antigos ou maiores devem virar alerta de divergência/atenção, nunca posição
  normal do bot.
- Backtests também devem respeitar o modo operacional real: OKX spot-only,
  stake fixo de US$100, BUY abre posição comprada e SELL fecha posição comprada.
  SELL com bot flat deve ser ignorado, nunca simulado como short.
- O Dashboard deve exibir exposição aberta do app pelo notional real
  (`size * preço atual/entrada`) para comparação fiel com a OKX; o stake fixo é
  usado como fallback apenas quando tamanho/preço ainda não foram carregados.
- Nunca sugerir aumentar ou reduzir stake como ação operacional; ajustes devem
  ocorrer por ativo, estratégia, parâmetros, pausa ou desligamento do bot.

### S013 - Viana Mini-Índice
- Categoria: `S` / Estratégias de Estrutura, Price Action e Liquidez.
- ID: `S013`.
- Nome: `S013 - Viana Mini-Índice`.
- Descrição operacional: Price Action limpo com VWAP intradiária, candle de
  abertura, leitura de players OKX e confirmação por fechamento de força,
  Fair Value Gap ou barra ignorada.
- Critérios:
  - C1: VWAP acima da máxima do primeiro candle da sessão permite compra; VWAP
    abaixo da mínima permite venda; VWAP dentro do range mantém HOLD.
  - C2: rejeição/teste da VWAP no lado do viés.
  - C3: fechamento de força. Compra fecha acima da máxima do candle anterior;
    venda fecha abaixo da mínima do candle anterior.
  - C4: confirmação por FVG de 3 candles ou barra ignorada/inside candle.
  - C5: players de mercado via OKX Rubik Trading Statistics:
    `long-short-account-ratio` e `top-traders-position-ratio`.
  - C6: volume relativo mínimo.
  - C7: execução com SL/TP1 por ATR.
- Feed de players:
  - `backend/feeds/okx_market_players.py`.
  - Usa endpoints públicos da OKX, sem credenciais.
  - Divergência vendedora: varejo comprado (`ratio > 1.4`) e top traders
    vendidos (`shortRatio > 0.55`).
  - Divergência compradora: varejo vendido (`ratio < 0.8`) e top traders
    comprados (`longRatio > 0.55`).
- O parâmetro `require_market_players` permite tornar a confirmação de players
  obrigatória. Por padrão, a estratégia não bloqueia se a fonte pública estiver
  indisponível.

### Regra de um ativo por bot
- Cada símbolo OKX pode existir em apenas um bot.
- Validar duplicidade ao criar, editar, iniciar e trocar símbolo.
- O `BotManager` também deve manter trava runtime para impedir dois bots ativos
  no mesmo símbolo, mesmo que alguma rota futura ignore a validação.
- O banco deve manter índice/constraint único para `bots.symbol`.

### Auditoria de rejeições OKX
- Toda rejeição/falha crítica de ordem deve ser persistida em
  `order_rejections`.
- APIs obrigatórias:
  - `GET /api/order-rejections`
  - `PATCH /api/order-rejections/{id}`
- Rejeições de entrada, Stop Loss, trailing stop e fallback de proteção devem
  conter bot, símbolo, lado, tipo de ordem, `ordId`/`algoId` quando houver,
  motivo e payload bruto.

### Stop Loss e lucro garantido
- Para OKX API v5, o trailing stop nativo usa `/api/v5/trade/order-algo` com
  `ordType="move_order_stop"` e `callbackRatio`/`callbackSpread`, conforme a
  documentação oficial OKX. Não substituir por nomenclatura de outra corretora.
- Fills de Stop Loss/trailing devem ser reconhecidos por `ordId` quando a OKX
  gerar a ordem executável, e por `algoId` quando disponível.
- Se o trailing stop falhar, criar fallback de Stop Loss fixo e registrar a
  rejeição do trailing. Se o fallback também falhar, registrar rejeição crítica.

### Telegram operacional
- O botão de teste Telegram envia saldos oficiais da exchange:
  - PT-BR: `Enviar Saldos (Telegram)`
  - EN-US: `Send Balances (Telegram)`
- A rota legada `POST /api/system/telegram-test` permanece, mas envia snapshot
  oficial de saldo/posição, não mensagem genérica.
- Mensagens Telegram devem incluir timestamp, bot/símbolo quando aplicável,
  detalhes de ordem, saldo/posição oficial e eventos críticos.

### Deploy e banco
- O banco operacional deve ser `okx_strategy`.
- Nunca apagar, renomear ou recriar volumes Docker sem autorização explícita do
  usuário. Deploys devem preservar o banco OKX por padrão.

### Troubleshooting HTTPS/TLS
- O domínio oficial é `okx.tradixio.com` e aponta para `187.127.251.139`.
- Se o navegador mostrar "Não foi possível estabelecer uma conexão segura" ou
  "resposta inválida", as causas mais prováveis são:
  1. Traefik não está rodando ou não está escutando na porta 443.
  2. Firewall/VPS não liberou 80 e 443.
  3. O container `okx_strategy` não está conectado à rede `traefik-public`.
  4. O certificado Let's Encrypt ainda não foi emitido ou falhou nos logs.
  5. Outro serviço está respondendo na porta 443.
- Verificar sempre com `docker logs traefik` e com um `curl -vk
  https://okx.tradixio.com` no VPS.
- Em 2026-06-04, os logs do Traefik mostraram duas falhas ACME relevantes:
  - `NXDOMAIN looking up A/AAAA for okx.tradixio.com`: DNS ainda não existia ou
    não havia propagado.
  - `tls: no application protocol` em `2.57.91.91`: Let's Encrypt resolveu o
    domínio para IP errado/antigo, não para `187.127.251.139`, ou a porta 443
    estava chegando em serviço que não era o Traefik com TLS-ALPN.
- Antes de tentar novo deploy, confirmar de dentro do VPS que resolvers públicos
  (`1.1.1.1`, `8.8.8.8`) retornam `187.127.251.139` para o A record e que não
  existe AAAA incorreto. Se houver AAAA, ele deve apontar corretamente ou ser
  removido.
- Estado confirmado em 2026-06-04:
  - `okx.tradixio.com` resolve para `187.127.251.139`.
  - HTTP retorna `308 Permanent Redirect` para HTTPS.
  - HTTPS responde `200` com certificado Let's Encrypt para
    `CN=okx.tradixio.com`.
  - O container `okx_strategy` está servindo o frontend OKXStrategy via Uvicorn.
- `curl -I https://okx.tradixio.com/api/health` retornava `405` porque `-I`
  envia método `HEAD` e a rota só aceitava `GET`; a API agora aceita `HEAD` em
  `/api/health`.
- Se o VPS responde `curl -vk https://okx.tradixio.com` com TLS válido e `200`,
  mas o Chrome local mostra `ERR_SSL_PROTOCOL_ERROR`, verificar cache/DNS do
  cliente local ou proxy/ISP ainda resolvendo para IP antigo. O problema não é
  do certificado no VPS quando o `curl` no VPS mostra certificado Let's Encrypt
  válido e resposta `200`.
- Hard refresh (`Ctrl+Shift+R`) não corrige erro SSL quando a causa é cache DNS,
  cache de socket/SSL do navegador, proxy/ISP ou resolução pública divergente.

## REGRA ABSOLUTA — Conformidade App ↔ OKX Oficial

> **Esta regra tem prioridade sobre qualquer outra decisão técnica.**

A aplicação deve manter **100% de conformidade** com a OKX em todos os indicadores:

| Dimensão | O que deve ser idêntico |
|----------|------------------------|
| Balance | Equity, disponível, margem |
| Positions | Símbolo, quantidade, preço médio, P&L não realizado |
| Orders | Ordens abertas, status, tipo |
| Activities | Fills, fees — todos os registros |
| Histórico | Trades fechados, P&L realizado, corretagens |

**Qualquer discrepância, por menor que seja, exige:**
1. Identificar a causa raiz (WebSocket, REST polling, DB órfão, bug de cálculo)
2. Corrigir o código antes de retomar operações
3. Executar Reset Geral (parar bots + limpar DB + sincronizar OKX)
4. Reiniciar tudo do zero com conta limpa confirmada

**Mecanismos de conformidade já implementados:**
- WebSocket `orders` → fill confirmado em tempo real
- REST polling fallback → `_confirm_fill_price` (~2 min, 7 tentativas)
- `_reconcile_loop` → a cada 5 min, detecta posições no DB que a OKX não tem
- Ordem rejeitada/cancelada → deleta trade do banco e reseta o bot
- Reset Geral → `POST /api/account/reset` — liquida, limpa DB, sincroniza OKX

---

## 1. Two parallel implementations

| Aspect | OKXStrategy (ATIVO) |
|---|---|
| Entry point | `OKXStrategy/backend/main.py` → FastAPI + Uvicorn |
| Language | Python + aiohttp/websockets (OKX REST/WS nativo) |
| Persistence | PostgreSQL `okx_strategy` |
| Dashboards | React SPA (frontend integrado) |
| Execution | Contínuo via asyncio tasks |

> **⚠️ Deploy atual:** O deploy de produção é feito **SEMPRE** via Docker + Traefik
> no Hostinger VPS KVM4. A pasta de deploy é `/opt/okx-strategy` e o código
> é sincronizado a partir do repositório GitHub. O domínio de acesso é
> `https://okx.tradixio.com`.

---

## 2. Estratégias OKXStrategy

As estratégias estão em `OKXStrategy/backend/strategies/`.

### Phase 1 - Macro Filter
- **Funding rate** (Binance Futures public API, no auth): blocks longs if
  funding > `+0.01%` per 8h (too long-heavy).
- **Open Interest trend** (5-min bars, last 6): blocks longs if price rising +
  OI falling (short-covering rally, not real demand).

### Phase 2 - Volume Profile (VAH / POC / VAL)
- Builds a 50-bin volume histogram over the last 60 bars (5h of 5-min).
- **POC** = Point of Control (highest-volume level).
- **VAH / VAL** = Value Area High / Low - price boundaries containing 70% of
  the day's volume (expanded outward from POC).
- **Regime:** `BALANCED` if `VAL <= price <= VAH`, else `IMBALANCED`.

### Phase 3 - Entry Signals (two engines, regime-gated)

**Engine A - IMBALANCED (breakout/momentum)**
- Long breakout: `price > VAH` AND `prev_close <= VAH` AND `delta_z >= 2.0`
  AND `price > VWAP` AND `price > EMA50` AND macro OK
- Short breakdown: `price < VAL` AND `prev_close >= VAL` AND `delta_z <= -2.0`
  AND `price < VWAP` AND `price < EMA50` AND macro OK

**Engine B - BALANCED (mean reversion)**
- Buy at VAL: `price <= VAL + 0.3xATR` AND (`MFI < 30` OR bullish divergence)
- Sell at VAH: `price >= VAH - 0.3xATR` AND (`MFI > 70` OR bearish divergence)

Indicators computed: EMA50, VWAP (cumulative), ATR(14), MFI(14), Volume Delta,
Volume Delta Z-Score (20-bar rolling), vol_index (ATR/price).

### Phase 4 - Risk & Sizing
- **Fixed stake**: toda entrada usa `US$100 / price`, arredondado pelo `lotSz`
  do instrumento OKX spot.
- **Structural stop** (long): `POC - (2 ticks x $1)`. If price falls through POC,
  the thesis is dead.
- **Liquidity target**: next high-volume shelf above entry (from VP levels).

### Phase 5 - Safety Suite
- **Mercado crypto 24/7**: opera continuamente, sem restrição de horário.
- **Daily loss circuit breaker**: $3,000 USD (~3% of $100k account).
- **News blackout** (optional): CryptoPanic API - skipped if no key.
- **SPY correlation filter**: if BTC and SPY move opposite directions by >1%
  within 5 min, flag decorrelation.
- **Slippage buffer**: limit orders placed at `proposed_rate x 1.001` (+0.1%).

### Position rules (hard-coded)
```text
BUY_AMOUNT_USD       = 100.0    # fixed stake; never configurable
MAX_OPEN_POSITIONS   = 1        # one trade at a time
DAILY_LOSS_LIMIT_PCT = 0.03
```

---

## 3. File structure

```text
OKXTrader/
|- OKXStrategy/                 # 🟢 APLICAÇÃO PRINCIPAL (FastAPI + React + PG)
|  |- backend/
|  |  |- main.py               # FastAPI app
|  |  |- bot_manager.py        # Gerenciamento de bots
|  |  |- database.py           # PostgreSQL models
|  |  |- strategies/           # 30+ estratégias registradas
|  |  |  |- registry.py
|  |  |  `- ...
|  |  |- exchanges/            # Adapters (OKX)
|  |  |- feeds/                # Dados externos
|  |  |- notifications/        # Telegram
|  |  `- tests/                # Testes pytest
|  |- frontend/
|  |  |- src/
|  |  |  |- App.jsx
|  |  |  |- pages/
|  |  |  |- components/
|  |  |  `- i18n/
|  |  `- package.json
|  |- docker-compose.yml       # Dev
|  |- docker-compose.prod.yml  # Production
|  |- deploy.sh                # Deploy automatizado
|  |- Dockerfile
|  |- requirements.txt
|  `- SKILL.md                 # THIS FILE
|- OKXStrategy.bootstrap/       # Stack de desenvolvimento local
|- prompts/                     # Prompts para meta-optimizer
|- config/                      # Configs
|- fix_postgres.bat            # Utilitário PostgreSQL
|- _backend.bat                # Inicia backend local
|- _frontend.bat               # Inicia frontend local
|- run.bat                     # Inicia stack completa
|- start.bat                   # Inicia stack com Docker
|- create_db.bat               # Cria banco okx_strategy
`- .env                         # OKX_DEMO, TELEGRAM, etc.
```

---

## 4. OKXStrategy Production Deploy (ATIVO)

**Infraestrutura:** Hostinger VPS KVM4 — Ubuntu 22.04
**Método:** Docker Compose + Traefik (reverse proxy)
**Path no servidor:** `/opt/okx-strategy`
**Domínio:** https://okx.tradixio.com
**Subdomínio:** `okx.tradixio.com` já criado no VPS
**Repo:** https://github.com/romualdoalves/okxstrategy
**GitHub Token:** nunca documentar tokens reais no repositório
**Key ID:** armazenado fora do repositório

#### Estrutura de deploy
```text
/opt/okx-strategy/
├── docker-compose.prod.yml     # App + PostgreSQL (Traefik via labels)
├── deploy.sh                   # Script de deploy automatizado
├── .env                        # secrets (não versionado)
├── OKXStrategy/                # Código-fonte (FastAPI + React)
│   ├── backend/
│   ├── frontend/
│   ├── Dockerfile
│   └── compose.prod.yml        # compose interno (não usado no VPS)
└── traefik/                    # Traefik rodando separadamente
```

#### Arquitetura do container
O app é um **container único** que serve tanto a API FastAPI quanto o frontend React SPA:
- **Porta 8000** — FastAPI com catch-all `/{path}` que serve `index.html` para rotas do SPA
- **Assets** — montados em `/assets` via `StaticFiles`
- **API** — prefixo `/api/*` e `/ws` para WebSocket
- **PostgreSQL** — container separado na mesma rede `traefik-public`

#### Primeiro deploy (setup inicial no VPS)

Copie e cole o bloco inteiro no terminal do VPS:

```bash
# 1. Criar diretório de deploy e clonar
mkdir -p /opt/okx-strategy
git clone https://github.com/romualdoalves/OKXStrategy.git /opt/okx-strategy

# 2. Criar rede Traefik (se não existir)
docker network create traefik-public 2>/dev/null || true

# 3. Criar volumes Docker (se não existir)
docker volume create okx_strategy_pgdata 2>/dev/null || true
docker volume create okx_strategy_factory 2>/dev/null || true

# 4. Subir Traefik (se ainda não estiver rodando)
if ! docker ps | grep -q traefik; then
    mkdir -p /opt/traefik/letsencrypt
    touch /opt/traefik/letsencrypt/acme.json
    chmod 600 /opt/traefik/letsencrypt/acme.json
    cat > /opt/traefik/docker-compose.yml <<'EOF'
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
EOF
    docker compose -f /opt/traefik/docker-compose.yml up -d
fi

# 5. Criar .env a partir do template
cp /opt/okx-strategy/.env.production.example /opt/okx-strategy/.env
# EDITE .env com seus secrets reais:
# nano /opt/okx-strategy/.env

# 6. Build e deploy
docker compose -f /opt/okx-strategy/docker-compose.prod.yml build --no-cache
docker compose -f /opt/okx-strategy/docker-compose.prod.yml up -d

# 7. Verificar status
docker ps
docker logs -f okx_strategy
```

#### Deploys subsequentes (update rápido)

Use o script automatizado ou os comandos manuais:

**Via script:**
```bash
bash /opt/okx-strategy/deploy.sh
```

**Manual:**
```bash
# Pull do código
git -C /opt/okx-strategy pull origin main

# Rebuild e restart
docker compose -f /opt/okx-strategy/docker-compose.prod.yml down
docker compose -f /opt/okx-strategy/docker-compose.prod.yml build --no-cache
docker compose -f /opt/okx-strategy/docker-compose.prod.yml up -d

# Status
docker compose -f /opt/okx-strategy/docker-compose.prod.yml ps
docker logs -f okx_strategy
```

#### Serviços expostos via Traefik
| Serviço | Rota | Container:Port |
|---------|------|----------------|
| API | `okx.tradixio.com/api/*` | `okx_strategy:8000` |
| Frontend SPA | `okx.tradixio.com/` | `okx_strategy:8000` |
| WebSocket | `okx.tradixio.com/ws` | `okx_strategy:8000` |
| Health | `okx.tradixio.com/api/health` | `okx_strategy:8000` |

#### Banco de dados
- **PostgreSQL** rodando em container Docker (`okx_strategy_db`)
- Dados persistentes em volume Docker `okx_strategy_pgdata`
- **REGRA GLOBAL DO USUÁRIO:** Nunca exclua, altere o nome do volume ou resete o banco de dados sem perguntar explicitamente antes. Históricos e indicadores devem ser preservados por padrão em todos os deploys.
- Backup: `docker exec okx_strategy_db pg_dump -U crypto okx_strategy > backup.sql`
- Restore: `docker exec -i okx_strategy_db psql -U crypto -d okx_strategy < backup.sql`

#### Taxonomia Semântica de Estratégias
Todas as estratégias (nativas + Fábrica IA) usam **categorias semânticas**:

| Prefixo | Categoria | Significado | Exemplos |
|---------|-----------|-------------|----------|
| **TF** | Trend Following | Seguir a tendência | TF001 EMA+VWAP, TF003 MACD |
| **MR** | Mean Reversion | Reversão à média | MR001 ByeBot, MR002 Bollinger |
| **PA** | Price Action | Ação do preço | PA001 Pivot Sniper, PA002 ABCD |
| **SC** | Scalping | Execução rápida | SC001 Pattern Scalp, SC002 Viana |
| **RG** | Regime | Estado de mercado | RG001 Markov, RG002 Graph |
| **IF** | Information | Dados externos | IF001 On-chain, IF002 DEX Spread |
| **NW** | Network | Relações entre ativos | NW001 Influencers |
| **T**  | Test | Teste da plataforma | T000 |

- **NÃO EXISTE** mais prefixo F, FX, B, A, S, I ou M.
- **Arquivos:** `backend/strategies/factory/[a-z]{2}[0-9]{3}.py` (ex: `tf001.py`, `pa007.py`)
- **Persistência:** Volume Docker `okx_strategy_factory` montado em `/app/backend/strategies/factory`
- **Fábrica IA:** analisa a descrição e classifica automaticamente na categoria correta (TF/MR/PA/SC/RG/IF/NW)

#### Variáveis de ambiente críticas (`.env`)
```text
EXCHANGE_PROVIDER=okx
OKX_DEMO=true

DEEPSEEK_API_KEY=
ETHERSCAN_API_KEY=

TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=6753071411

POSTGRES_PASSWORD=crypto
DATABASE_URL=postgresql://crypto:crypto@postgres:5432/okx_strategy
```

#### Troubleshooting 404
Se `https://okx.tradixio.com` retornar 404:

1. **Container saudável?**
   ```bash
   docker ps                 # deve mostrar okx_strategy (healthy)
   docker logs okx_strategy  # verifique se uvicorn subiu na porta 8000
   ```

2. **Traefik está roteando?**
   ```bash
   docker inspect okx_strategy | grep -A5 traefik
   # Deve mostrar os labels traefik.http.routers.*
   ```

3. **Rede compartilhada?**
   ```bash
   docker network inspect traefik-public
   # Deve conter os containers traefik e okx_strategy
   ```

4. **Frontend build existe?**
   ```bash
   docker exec okx_strategy ls -la /app/backend/static_frontend/
   # Deve conter index.html e pasta assets/
   ```

---

## 5. OKXStrategy Application Stack (PRIMARY)

The `OKXStrategy/` directory contains the **main production application** — a
full-stack trading platform for OKX exchange.

### Architecture
| Layer | Tech | Purpose |
|-------|------|---------|
| Backend | FastAPI + SQLAlchemy + PostgreSQL | REST API, WebSocket, bot orchestration |
| Frontend | React + Vite + Tailwind + TanStack Query | Dashboard, bot management, charts |
| Data | PostgreSQL (Docker) + JSONL logs | Persistent state + signal history |
| External | OKX API, CoinGecko, DexScreener | Market data + execution |

### Key strategies in OKXStrategy
| ID | Name | Location |
|----|------|----------|
| A006 | DEX Spread Momentum | `strategies/dex_arbitrage_sentinel.py` |
| S008 | Liquidity Sweep | `strategies/price_action_liquidity_sweep.py` |
| B001-B005 | Basic strategies (EMA/VWAP, RSI/MA, etc.) | `strategies/*.py` |
| A001-A005 | Advanced strategies (CFM, ABCD, Multi-TF, etc.) | `strategies/*.py` |
| S001-S011 | Specialized strategies (Pivot, Scalp, etc.) | `strategies/*.py` |
| S012 | CTE – Captura e Transição de Estrutura | `strategies/cte_structure_capture.py` |
| I001-I006 | Intelligence strategies (Graph Regime, Influencers, Whale Flow, GEX, etc.) | `strategies/*.py` |
| M001 | Markov Regime | `strategies/markov_regime.py` |

### Running tests
```bash
python -m pytest /opt/okx-strategy/OKXStrategy/tests/ -v
```

---

## 8. Platform Review & Corrections (2026-05-25)

A complete system review was conducted, resolving critical bugs across the strategies, database, and startup scripts:
- **Liquidity Sweep Strategy Bug Fix**: Corrected support zone calculation in `price_action_liquidity_sweep.py` to retrieve the last established support (`support_price.dropna().iloc[-1]`) instead of checking only the last candle. Prepend candles in `test_liquidity_sweep.py` to satisfy lookback limits. All tests are now successfully passing (`2 passed`).
- **PostgreSQL Database & Migration Fixes**: Migrated from SQLite to PostgreSQL `okx_strategy`. Resolved `migrate_sqlite_to_pg.py` boolean conversion issues (mapping `0`/`1` SQLite integers to PG native `boolean` for columns `demo`, `active`, `tp1_done`) and missing tables handling.
- **UX Performance Page Correction**: Modified historical performance ranking inside `backend/main.py` to filter by `trades > 0` instead of `win_rate > 0`, allowing losing bots to be successfully tracked on the dashboard.
- **Batch Path Corrections**: Fixed hardcoded outdated paths in `start_bot.bat`, `run_15min_bot.bat`, and `start_strategy3.bat` to use dynamic Windows directory resolution `%~dp0`.
- **DEX Spread Momentum Strategy (A006)**: New strategy added to OKXStrategy. Uses cross-exchange price spreads (CoinGecko, DexScreener vs OKX) as a momentum indicator. Emits directional BUY/SELL signals — does NOT execute real arbitrage. Includes `DexPriceFeed` aggregator and 11 unit tests.
- **Influencers and Followers Strategy (I005)**: New Intelligence strategy using Graph Data Science to model the market as a Liquidity Transmission Network. Calculates causality (Lead-Lag) to identify the Influencer via PageRank, and automatically triggers buys on the most dependent Follower when the leader exhibits an impulsive breakout.

---

## 9. Bug Fixes & Features (2026-05-26)

### Critical Fixes
- **I005 crash fix**: `nx.pagerank` raised `PowerIterationFailedConvergence` (unhandled) → bot stopped with `status=error`. Wrapped in try/except in `update_graph`. Also rewrote `compute_with_context` — it was waiting for `context["price_matrix"]` which BotManager never provides; now stores price matrix in `self._last_price_matrix` during `update_graph` and uses it in `compute_with_context`. Added real ATR calculation from bot candles (was always 0).
- **OKX time_in_force fix**: `market_order` usa `"gtc"` (good till cancel) conforme OKX API v5.
- **OKX API key lazy loading**: Module-level headers frozen at import time com strings vazias no Docker → 401. Replaced com funções lazy `_headers()`, `_base_url()`.

### Strategy Audit Fixes
- **A005 (CycleShift)**: Returned `None` when 1W data not yet loaded → silent UI. Now returns `StrategyResult(HOLD, "Aguardando dados semanais N/26 barras 1W")`.
- **A003 (MultiTF)**: Same fix — returns `StrategyResult(HOLD, "Aquecendo...")` during warmup instead of `None`.

### Multi-Bot Same-Asset Protection
- Backend: `/api/bots` POST and PATCH now reject duplicate symbols with HTTP 400.
- Frontend NewBot + EditBot: used assets shown in gray with "EM USO" badge, preserving ranking order.

### Trade History Page Improvements
- `/api/trades` now returns `bot_name` and `closed_at` (was only `bot_id`). Default limit raised 100 → 200.
- `TradeTable` component: added Bot name column, Event column with human-readable labels (Stop Loss, Trailing Stop, Shadow TP, etc.), locale-aware number formatting, cleaner type badges.

### Telegram Notifications
- Added `build_exit_msg()` to `notifications/message_builder.py` for position close events.
- `_on_position_closed` in `bot_manager.py` now fires exit notification (covers SW trailing stop, manual liquidate, exchange WS execution paths).
- Added `build_order_confirmed_msg` and `build_order_failed_msg` to `notifications/__init__.py` exports.
- Added `/api/system/telegram-status` (token/chat_id presence check) and `/api/system/telegram-test` (sends real test message) to `main.py`.
- Added `getTelegramStatus` and `testTelegram` to `frontend/src/api.js`.
- Dashboard now shows a **Telegram card** at the bottom: active/inactive status, token preview, "Testar agora" button, setup instructions.
- Telegram bot: `t.me/OKX_StrategyBot`. Configure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `/opt/okx-strategy/.env`.
- Do not commit the Telegram bot token. Keep the real token only in local/VPS `.env`.
- Known issue: `sed` commands can truncate bot tokens at the `:` character if written incorrectly. Prefer editing `.env` via `nano /opt/okx-strategy/.env` or use a quoted command that preserves the full token.

### I005 Stability Fix (2026-05-27)
- `update_graph` called `self.engine.select_strategy_targets()` without try/except — any convergence error crashed the bot task.
- Fixed: wrapped in `try/except Exception` with `log.warning(...)`. Bot now continues gracefully if `select_strategy_targets` fails.
- Em 2026-06-04, a I005 passou a expor seus critérios reais no contrato oficial (`criteria_met/criteria_total`): alvos do grafo, candles dos alvos, impulso do líder, atraso/RSI do seguidor e alinhamento entre o seguidor escolhido e o símbolo configurado no bot. A estratégia agora usa `atr_multiplier`, `rsi_threshold` e `tp_multiplier` no gatilho, pode emitir `BUY` ou `SELL`, e bloqueia se o grafo quer operar um seguidor diferente do símbolo do bot.

### Activities Page — Dados Oficiais OKX (2026-05-27)
- **Nova rota:** `/activities` no menu lateral.
- **Backend:** `GET /api/activities?date=YYYY-MM-DD` — busca fills da OKX, equity do dia e histórico de portfólio. Correlaciona fills com bots por símbolo e com trades do banco por timing (±5 min). Computa P&L por round-trip a partir dos fills reais.
- **Frontend `Activities.jsx`:** 4 stat cards (Equity OKX, P&L dia OKX, P&L app banco, P&L fills), banner de reconciliação que destaca discrepâncias, sparkline de equity intraday, tabela de fills com expand inline mostrando Order ID / status / match no banco, tabela de trades do banco do dia.
- **Novos métodos em `OKXExchange`:** `get_activities()`, `get_account_summary()`, `get_portfolio_history()`.

### P&L Calculation Bug Fix (2026-05-27)
- **Root cause:** `_on_position_closed` in `bot_manager.py` used `abs(price - entry_price)` to compute `price_diff`, removing the sign from the difference. This caused **LONG losing trades to be reported as profit** (and SHORT winning trades as loss) in the app.
- **Fixed:** Replaced the two-line formula with `pnl = size * ct_size * (price - self._entry_price) * self._direction`. The signed difference times direction gives the correct sign for all combinations of LONG/SHORT × profit/loss.
- **Affected file:** `backend/bot_manager.py`, function `_on_position_closed`.
- **Note:** Historical trade records already persisted in the database with incorrect P&L values are NOT retroactively corrected. Use `/api/account/reset?clear_history=true` to wipe and restart clean if desired.

### OKX API Optimization — Phase 1 + F2.3 (2026-05-28)

#### F1.1 — Real WebSocket `orders` (OKXPrivateStream)
- **Problem:** `OKXPrivateStream` inicialmente usava polling REST, com delays de 3–60 segundos para confirmação de fills. Exit P&L calculations usavam preços estimados (candle close) em vez de preços reais de fill.
- **Fix:** Implementada conexão WebSocket real para `wss://wspap.okx.com:8443/ws/v5/private` (demo) ou `wss://ws.okx.com:8443/ws/v5/private` (live).
  - Autentica com API key/secret/passphrase via `login` message
  - Subscreve ao canal `orders`
  - Em eventos `fill`, traduz formato OKX para evento interno (`ordId`, `fillPx`, `fillSz`, `state="filled"`) e passa para `_handle_order_event`
  - `_monitor_orders` em `bot_manager.py` lida com reconexão automaticamente (15s backoff)
- **Affected file:** `backend/exchanges/okx.py`, class `OKXPrivateStream`

#### F1.3 — filled_avg_price in exit polling
- **Problem:** `_sw_exit` and `manual_liquidate` only polled 5 times × 2s = 10s. If fill confirmation was slow, P&L was calculated with estimated price.
- **Fix:** Increased polling from 5 to 12 attempts (24s total). Added early-exit guard (`if self._direction == 0: return`) to skip REST polling when WebSocket already handled the close. Added order-first check: from attempt 2 onwards, checks order `status == "filled"` directly (not just position gone) to capture `filled_avg_price` faster.
- **Affected file:** `backend/bot_manager.py`, functions `_sw_exit` and `manual_liquidate`

#### F2.3 — Cross-day Activities P&L (FIFO)
- **Problem:** `/api/activities` only fetched fills for `target_date`. If a BUY was placed on a previous day, the SELL fill today had no matching BUY → `fill_pnl = None` (showing $0 round-trip P&L in Activities page).
- **Fix:**
  - `get_activities()` in `OKXExchange` now accepts `after` and `until` parameters for date-range queries (mutually exclusive with `date`).
  - `/api/activities` endpoint: queries DB for earliest open trade. If it was opened on a prior day, fetches ALL fills since that date (`after=earliest_date T00:00:00Z`). Runs FIFO across all fills. Only today's fills are returned in the response, but their P&L is correctly calculated against prior-day BUYs.
  - Reconciliation banner shows `cross_day_fifo: true` + `fifo_since` date when cross-day mode is active.
  - Frontend banner displays "FIFO cross-day ativo: fills carregados desde YYYY-MM-DD" when applicable.
- **Affected files:** `backend/exchanges/okx.py`, `backend/main.py`, `frontend/src/pages/Activities.jsx`

#### F1.4 — _confirm_fill_price otimizado (2026-05-28)
- **Problem:** `_confirm_fill_price` usava `[3,5,10,15,30] + [60]*420` = ~7 horas de polling REST para confirmar o fill de entrada. Consumia 420 chamadas de API desnecessariamente enquanto o WebSocket já entregava o fill em segundos.
- **Fix (parte 1):** `_handle_order_event` agora também captura fills de **entrada** (quando `ord_id == _entry_ord_id` e a direção do fill é a mesma da posição). Atualiza `_entry_price`, persiste no banco, envia broadcast `order_confirmed` e notificação Telegram — tudo em tempo real via WebSocket.
- **Fix (parte 2):** `_confirm_fill_price` reduzido para um fallback de `[3,5,10,15,30,60,120]` (~4 min total). Só aciona broadcast/Telegram se o `_entry_price` ainda diferir (indica que o WebSocket não chegou antes). Verifica `ord_id == _entry_ord_id` para não corrigir com dados de ordem obsoleta.
- **Affected file:** `backend/bot_manager.py`, funções `_handle_order_event` e `_confirm_fill_price`

---

## 10. New Strategy — GEX Gamma Exposure Regime (2026-05-30)

Nova estratégia de inteligência implementada e totalmente integrada ao framework:

- **GEX Gamma Exposure Regime (I006)**: Estratégia Intelligence baseada no indicador GEX (Gamma Exposure) do mercado de opções de BTC. Trabalha por causalidade — mede o posicionamento dos market makers em opções (API pública Deribit, sem autenticação) e classifica o mercado em dois regimes de gama:
  - **Gama Positivo** (calls OI > puts OI ATM): market makers atuam contra-tendência (amortecimento) → lateralidade → estratégia de range (compra no suporte de OI, vende na resistência).
  - **Gama Negativo** (puts OI > calls OI ATM): market makers atuam a favor da tendência (amplificação) → explosividade → estratégia de breakout (compra acima de resistência, vende abaixo de suporte).
  - **Fallback automático**: quando dados Deribit indisponíveis, estima regime via BBW + ratio ATR.

### Arquivos novos/modificados
| Arquivo | Tipo | Descrição |
|---------|------|-----------|
| `backend/feeds/gex_feed.py` | Novo | Feed Deribit: book summary de opções BTC, computa GEX proxy, max pain, níveis de suporte/resistência por OI, PCR, pressão negativa de pico. Cache 5 min. |
| `backend/strategies/gex_gamma_exposure.py` | Novo | Estratégia I006 — `needs_gex_context = True`, timeframe recomendado 1h, 8 parâmetros configuráveis, 4 critérios de explainability, SL/TP adaptados ao regime. |
| `backend/bot_manager.py` | Modificado | Import `GexFeed`, campos `_gex_feed`/`_gex_snapshot`, método `_update_gex_context()`, injeção de `gex_data` em `_strategy_context()`, chamadas nos dois pontos de execução (warmup + loop por candle). |
| `backend/strategies/registry.py` | Modificado | `"I006": GexGammaExposureStrategy` registrado. |

### Parâmetros da estratégia I006
| Parâmetro | Default | Descrição |
|-----------|---------|-----------|
| `level_proximity_pct` | 0.8 | % de ATR de proximidade ao nível de OI para acionar gatilho |
| `vol_spike_mult` | 1.4 | Multiplicador de volume médio para confirmar agressão |
| `atr_period` | 14 | Período do ATR para gestão de risco |
| `sl_mult_pos` | 1.2 | Multiplicador ATR para SL em Gama Positivo (mercado amortecido) |
| `sl_mult_neg` | 2.0 | Multiplicador ATR para SL em Gama Negativo (mercado explosivo) |
| `tp_rr_ratio` | 2.0 | Relação R:R para TP1 |
| `bbw_period` | 20 | Período das Bollinger Bands para estimativa fallback |
| `bbw_neg_mult` | 1.5 | BBW acima de (mult × média) → Gama Negativo estimado |

---

## 11. Reset Geral (2026-06-01)

Em 2026-06-01 foi feito um **Reset Geral** da plataforma:

- **Exchange**: OKX
- **Modo**: Demo (paper trading OKX)
- **Credenciais**: armazenadas no banco (tabela `settings`), criptografadas
- **Endpoint demo**: `https://www.okx.com` com header `x-simulated-trading: 1`
- **Endpoint live**: `https://www.okx.com`
- **Estado do banco**: banco PostgreSQL `okx_strategy` preservado
- **Estado do código**: nenhuma mudança de código nesta release

---

## 12. Nova Estratégia — S012 CTE (2026-06-01)

- **S012 - CTE – Captura e Transição de Estrutura**: Estratégia de reversão institucional multi-timeframe baseada em price action.
  - HTF (1H): detecta varredura de liquidez (SSL/BSL) com pavio de rejeição
  - LTF (5M): confirma CHoCH (quebra da estrutura descendente/ascendente)
  - Aguarda recuo para zona Fibonacci 61.8%–78.6% antes de entrar
  - 5 critérios progressivos na barra de status da UI
  - Stop: extremo de rejeição HTF ± buffer ATR; TP1: R:R configurável; TP2: liquidez oposta HTF
  - Arquivo: `backend/strategies/cte_structure_capture.py`
  - Registrada como `"S012"` em `registry.py`
  - Timeframe recomendado: 5m (com contexto 1H via `extra_timeframes`)

---

## 13. Correção Telegram e OKX Private WS (2026-06-04)

- `TELEGRAM_CHAT_ID` operacional oficial: `6753071411`.
- Bot Telegram oficial validado via `getMe`: `@OKX_StrategyBot`.
- O token real do Telegram não deve ser versionado no GitHub; mantenha-o somente no `.env` local/VPS.
- A tela de Dashboard agora mostra `TELEGRAM_CHAT_ID=6753071411` nas instruções do servidor.
- `/api/system/telegram-test` continua enviando snapshot oficial de saldos, mas agora retorna a causa real quando o envio falha.
- Quando o Telegram retorna `Bad Request: chat not found`, a causa operacional esperada é: o usuário `6753071411` ainda não abriu o bot configurado em `TELEGRAM_BOT_TOKEN`, não enviou `/start`, ou o token no VPS pertence a outro bot.
- Logs Telegram agora incluem `chat_id` e prévia segura do token para facilitar auditoria sem expor segredo completo.
- `OKXPrivateStream` demo usa o endpoint oficial `wss://wspap.okx.com:8443/ws/v5/private`, sem `brokerId=9999`, para evitar rejeição HTTP 403 no handshake.
- `OKXPrivateStream` envia headers explícitos de handshake (`User-Agent`/`Origin`) e, quando o WS privado é recusado com HTTP 403, o bot continua operando com confirmação REST e aplica backoff progressivo para não inundar os logs.
- Deploy no VPS deve atualizar somente `.env`, código e containers. Não resetar banco, não apagar volumes e não parar bots manualmente fora do ciclo normal de recriação do container.

---

## 14. Critérios de Ordem (2026-06-04)

- Além dos critérios da estratégia, cada bot agora expõe `runtime.order_criteria`.
- A ordem só é enviada quando os critérios da estratégia geram `BUY`/`SELL` e todos os critérios de ordem bloqueantes estão verdes.
- Critérios de ordem implementados no backend: sinal executável, runtime ativo, circuit breaker livre, posição local flat, nenhuma entrada pendente, direção permitida para o mercado, calendário livre, conexão OKX disponível, tamanho de ordem válido, SL/TP coerentes e posição OKX flat.
- Se algum critério de ordem falhar, o backend atualiza `hold_reason`, transmite `order_criteria` por WebSocket e não envia a ordem.
- Quando todos os critérios de ordem estão atendidos, `_enter()` é chamado de forma síncrona no candle fechado e revalida imediatamente antes do envio da ordem.
- A UI mostra uma checklist separada **Critérios de Ordem / Order Criteria** nos cards e no detalhe do bot, evitando que “Critérios de Entrada” verdes sejam confundidos com ordem executável.
- A checklist de entrada do frontend deve priorizar o placar oficial emitido pelo backend (`_criteria_met`, `_criteria_total`, `_signal`, `_hold_reason`) quando disponível. O frontend não deve declarar “todos os critérios OK” apenas por heurísticas locais se o backend ainda retornou `HOLD`.
- Sem sinal `BUY`/`SELL`, os critérios operacionais O2-O11 ficam dormentes (`status=”none”`) e não entram no placar. A UI deve mostrar o gate de ordem como “Aguardando sinal”; o checklist operacional só passa a variar/contar quando existe uma ordem candidata.

## 15. Fix: I005 Checklist Estático (2026-06-04)

- **Problema**: O checklist estático do I005 em `StrategyChecklist.jsx` exibia apenas 2 critérios (`regime === 'trending'` e `regime_conf >= 0.7`), ambos verdes assim que o grafo encontrava um Influenciador, mesmo sem Seguidor identificado ou sem impulso. Isso causava “Todos os critérios OK ✓” enquanto o `Critérios de Ordem` mostrava “Aguardando sinal” — inconsistência confusa para o usuário.
- **Causa raiz**: Quando `compute_with_context` retorna cedo (sem Seguidor encontrado), o `StrategyResult` tem `criteria_total=0`, então o frontend cai no checklist estático em vez do `officialEntryCriteria`. O checklist estático anterior não refletia as condições reais de execução.
- **Fix**: O checklist estático do I005 em `frontend/src/components/StrategyChecklist.jsx` agora exibe 4 critérios reais:
  - C1 Líder: verde se grafo identificou Influenciador (`regime === 'trending'`)
  - C2 Seguidor: verde se símbolo do bot é o Seguidor; **vermelho** se o grafo quer operar outro par; amarelo se aguardando
  - C3 Impulso: verde se `leader_impulse_ratio ≥ 2.0`; amarelo se ratio calculado mas insuficiente; cinza se sem dados
  - C4 RSI Seguidor: verde se `follower_rsi < 62`; amarelo se calculado mas fora da zona; cinza se sem dados
- O “Todos OK ✓” agora só aparece quando as 4 condições estão verdes simultaneamente.
- Estratégias não foram alteradas; a correção é exclusivamente no frontend.

## 16. Estratégia A007 — Linda Raschke Swing Trade (2026-06-04)

- **ID:** `A007`
- **Categoria:** Autorais (A)
- **Arquivo:** `backend/strategies/linda_raschke.py`
- **Timeframe principal:** `1D` (diário — gatilho)
- **Timeframe extra:** `1W` (semanal — filtros ADX + Donchian)
- **Direções:** BUY e SELL (espelhado)
- **Critérios (4):**
  - C1 ADX(14) semanal ≥ 25 e subindo — ativo tendencial
  - C2 Donchian 52 semanas — preço renovando máxima (BUY) ou mínima (SELL) histórica de 52 semanas
  - C3 Recuo à EMA 21 diária — preço tocou a média nas últimas `ema_touch_bars` (default: 5) velas
  - C4 Gatilho: fechamento acima da máxima semanal anterior OU EMA 8 virando na direção
- **SL:** mínima do candle de sinal (BUY) / máxima (SELL)
- **TP:** entry ± `tp_multiplier` × amplitude do candle de sinal (default: 2×)
- **Parâmetros configuráveis:** `adx_period`, `adx_threshold`, `donchian_period`, `ema_fast`, `ema_slow`, `ema_touch_bars`, `tp_multiplier`
- **Indicadores expostos:** `adx_w`, `adx_rising`, `donchian_upper`, `donchian_lower`, `donchian_break`, `ema21`, `ema8`, `ema8_slope`, `touched_ema21`, `dist_ema21_pct`, `prev_week_high`, `prev_week_low`, `trigger_type`

## 17. Fix: Checklist "Todos OK" Falso — A001, A003, A004 (2026-06-04)

## 18. Fábrica de Estratégias (2026-06-05)

## 19. Fix: Trailing Stop em Recuperação e Orphan Recovery (2026-06-05)

- **Problema**: O bot TF005 (ETH-USDT, Joe Di Napoli) fechou uma posição via `WS_EXECUTION` com apenas Stop Loss fixo ($1526.36), sem usar trailing stop. O PnL foi +$6.24 bruto / +$4.95 líquido, mas poderia ter sido maior com trailing stop ativo.
- **Causa raiz (1)**: `tp1_price = 0` no banco de dados. O trailing stop (SW-TS) só era ativado após TP1 ser atingido (`tp1_done=True`). Com TP1 zerado, o trailing stop nunca foi criado.
- **Causa raiz (2)**: Orphan recovery (quando o bot reinicia e encontra posição na OKX mas não no banco) criava trade com `"tp1_price": 0.0`, sem calcular um TP1 razoável.
- **Causa raiz (3)**: `_recover_state()` só restaurava trailing stop quando `tp1_done=True`. Se TP1 nunca foi atingido, a posição ficava sem proteção dinâmica após restart.
- **Fix em `backend/bot_manager.py`:**
  - **`_recover_state()`**: Remove condição `if last_trade.tp1_done`. Agora sempre recria o trailing stop (SW-TS) quando há posição aberta, independente de TP1. O `tp1_done` ainda é restaurado do banco para controle de lógica de TP1, mas o trailing stop é ativado sempre.
  - **Orphan recovery (2 lugares)**: Calcula `orphan_tp1 = adopted_px * (1.02 if LONG else 0.98)` e salva no banco. Isso garante que TP1 exista e o gatilho de +1% possa ativar o trailing stop nativo da OKX.
  - **`get_status()`**: Proteção contra lista de candles vazia — retorna status básico quando `_candles` está vazio, evitando erro no `/api/monitor`.
- **Fix em `backend/main.py`:**
  - Adicionado `from datetime import datetime, timezone` no topo do arquivo. O endpoint `/api/monitor` usava `datetime.now(timezone.utc)` sem import, causando `NameError`.
- **Arquivos alterados:** `backend/bot_manager.py`, `backend/main.py`
- **Comportamento após fix:**
  - Posições abertas sempre terão trailing stop ativo, mesmo após restart
  - Orphan recovery calcula TP1 automaticamente (2% do preço de entrada)
  - Monitoramento `/api/monitor` funciona mesmo quando bots ainda não receberam candles
- **Deploy:** rebuild do container `okx_strategy` com `docker compose -f docker-compose.prod.yml up -d --build okx_strategy`

## 20. Fix: Timestamp do Telegram (2026-06-05)

- **Problema**: Mensagens do Telegram mostravam apenas hora (`19:52 UTC`), sem data. Dificultava rastrear eventos em dias anteriores.
- **Causa**: `_now_utc()` em `backend/notifications/message_builder.py` usava formato `%H:%M UTC`.
- **Fix**: Alterado para `%Y-%m-%d %H:%M:%S UTC` — agora mostra data completa + hora + segundos.
- **Arquivo alterado:** `backend/notifications/message_builder.py`

## 21. Fix: Fábria IA — StrategyInfo extra_timeframes (2026-06-05)

- **Problema**: Erro `StrategyInfo.__init__() got an unexpected keyword argument 'extra_timeframes'` ao gerar estratégias pela Fábrica IA. O planner incluía `extra_timeframes` no JSON, e o generator às vezes passava isso para `StrategyInfo()`, mas o dataclass não tinha esse campo.
- **Fix**: Adicionado `extra_timeframes: list[str] = field(default_factory=list)` ao `StrategyInfo` em `backend/strategies/base.py`.
- **Arquivo alterado:** `backend/strategies/base.py`

## 22. Fix: Fábrica IA — Validação de ID de estratégia (2026-06-05)

- **Problema**: Erro `ID de estratégia inválido — deve começar com F` ao implantar estratégias pela Fábrica IA. A validação em `main.py` ainda exigia prefixo `F` (sistema antigo), mas o sistema foi migrado para prefixos semânticos (TF/MR/PA/SC/RG/IF/NW/T).
- **Fix**: Atualizada validação em `/api/strategy-factory/deploy` para aceitar prefixos semânticos válidos.
- **Arquivo alterado:** `backend/main.py`

## 26. Fix: Fábrica IA — _CandleBar mock com .time (2026-06-06)

- **Problema**: Código gerado pela IA usava `candle.time` (atributo inexistente no mock), causando erro na validação. O botão "Corrigir" funcionava, mas exigia uma rodada extra de correção.
- **Fix**: Adicionada property `time` ao `_CandleBar` mock que retorna `self.epoch`, igual às properties `timestamp`. Agora o mock suporta os 3 nomes: `.epoch`, `.timestamp`, `.time`.
- **Arquivo alterado:** `backend/strategy_factory/validator.py`

## 28. Fix: Fábrica IA — Reutilização de IDs deletados (2026-06-06)

- **Problema**: Ao excluir PA008 e recriar uma estratégia da mesma categoria, foi atribuído PA009 em vez de reutilizar PA008.
- **Causa**: O `assign_next_id` não estava considerando arquivos órfãos no disco (estratégias que existem como arquivo `.py` mas não estão no banco).
- **Fix**: Adicionada verificação de arquivos órfãos no disco. Agora o algoritmo considera: IDs ativos no registry + IDs do banco com arquivo + IDs órfãos no disco. Também adicionados logs para debug.
- **Arquivo alterado:** `backend/strategy_factory/deployer.py`

## 29. Fix: Botão excluir estratégia não aparecia para estratégias da Fábrica IA (2026-06-06)

- **Problema**: O botão de excluir estratégia (lixeira) só aparecia para IDs começando com `F` (sistema antigo). Estratégias da Fábrica IA com prefixos semânticos (TF, MR, PA, SC, RG, IF, NW) não mostravam o botão.
- **Fix**: Alterada a condição para usar regex que aceita todos os prefixos válidos: `^(TF|MR|PA|SC|RG|IF|NW|F|FX)\d`.
- **Arquivo alterado:** `frontend/src/pages/Strategies.jsx`

## 30. Fix: Fábrica IA — criteria_total ainda =1 após correção (2026-06-06)

- **Problema**: Mesmo após clicar "Corrigir", a IA às vezes mantém `criteria_total=1` quando o plano tem múltiplos critérios.
- **Fix**: Adicionado lembrete mais explícito no prompt de correção (`fix_code`) sobre `criteria_total` deve ser igual ao número exato de critérios.
- **Arquivo alterado:** `backend/strategy_factory/generator.py`

## 32. Fix: Critérios da estratégia não apareciam no frontend (2026-06-06)

- **Problema**: O checklist de entrada mostrava apenas 1 critério genérico ("DIREÇÃO SPOT") em vez dos critérios reais da estratégia (3 critérios para PA008). O frontend não tinha acesso aos critérios do plano.
- **Causa**: `list_strategies()` não retornava os critérios, e `StrategyInfo` não tinha campo `criteria`.
- **Fix**: 
  - Adicionado `criteria: list[dict]` ao `StrategyInfo` dataclass
  - `list_strategies()` agora retorna `criteria` para cada estratégia
  - O frontend pode usar os critérios do plano para montar o checklist correto
- **Arquivos alterados:** `backend/strategies/base.py`, `backend/strategies/registry.py`

## 33. Fix: Critérios da estratégia no checklist do frontend (2026-06-06)

- **Problema**: O checklist mostrava "C1 Backend", "C2 Backend" em vez dos nomes reais dos critérios (ex: "ORB Definido", "Rompimento Confirmado", "Reteste").
- **Fix em backend:**
  - `_runtime_indicators()` agora inclui `_criteria_names` com os nomes dos critérios da estratégia
  - `StrategyInfo` tem campo `criteria` para armazenar os critérios do plano
  - `list_strategies()` retorna `criteria` para cada estratégia
  - Generator injeta `criteria` automaticamente no StrategyInfo se a IA omitir
- **Fix em frontend:**
  - `officialEntryCriteria()` usa `_criteria_names` para mostrar nomes reais dos critérios
- **Arquivos alterados:** `backend/bot_manager.py`, `backend/strategies/base.py`, `backend/strategies/registry.py`, `backend/strategy_factory/generator.py`, `frontend/src/components/StrategyChecklist.jsx`

## 34. Fix: Cores no Histórico de Trades — Melhor/Pior Trade (2026-06-06)

- **Problema**: Melhor trade sempre aparecia verde e pior trade sempre vermelho, mesmo quando os valores eram negativos/positivos.
- **Fix**: A cor agora depende do valor: verde para positivo, vermelho para negativo, em ambos os cards.
- **Arquivo alterado:** `frontend/src/pages/Trades.jsx`

## 35. Migração: criteria para estratégias nativas (2026-06-06)

- **Objetivo**: Unificar tratamento de critérios entre estratégias nativas e Fábrica IA.
- **Script**: `migrate_criteria.py` — adiciona `criteria` ao `StrategyInfo` de estratégias que já usam `criteria_total`.
- **Resultado**: 14 estratégias migradas automaticamente (if003, mr003, mr004, pa003, pa006, rg001, rg003, sc002, tf006, tf012, etc.).
- **Estratégias pendentes**: 21 estratégias simples que não usam `criteria_total` precisam de revisão manual para adicionar critérios.
- **Arquivos alterados:** Vários em `backend/strategies/factory/`

## 31. Fix: criteria_total corrigido automaticamente pós-geração (2026-06-06)

- **Problema**: Mesmo com instruções explícitas no prompt, a IA continuava gerando `criteria_total=1` quando o plano tinha múltiplos critérios.
- **Fix**: Adicionada correção automática via regex após a geração e após a correção. Substitui qualquer `criteria_total=NUMERO` pelo número correto de critérios do plano. A IA pode errar, mas o backend corrige antes de entregar o código.
- **Arquivo alterado:** `backend/strategy_factory/generator.py`

## 27. Fix: Fábrica IA — criteria_total=1 quando plano tem múltiplos critérios (2026-06-06)

- **Problema**: Estratégias geradas pela Fábrica IA (ex: PA008) tinham `criteria_total=1` mesmo quando o plano definia múltiplos critérios (ex: 3 ou 4). Isso fazia o checklist do frontend mostrar "1/1 critérios OK" em vez do número correto.
- **Causa**: A IA ignorava a regra 6 do prompt que exige `criteria_total` igual ao número exato de critérios.
- **Fix**: Adicionada verificação no validator que rejeita `criteria_total=1` quando o plano tem mais de 1 critério. A mensagem de erro instrui a IA a corrigir.
- **Arquivo alterado:** `backend/strategy_factory/validator.py`

## 23. Fix: Fábrica IA — _CandleBar mock sem atributo epoch (2026-06-05)

- **Problema**: Erro `'_CandleBar' object has no attribute 'time'` (ou `'epoch'`) ao validar estratégias geradas pela Fábrica IA. O `_CandleBar` mock usado no validator tinha `timestamp` em vez de `epoch`, diferente do `CandleBar` real.
- **Fix**: Alterado `_CandleBar` para usar `epoch` (int em ms) igual ao `CandleBar` real. Também adicionado timestamp sequencial em ms na geração de candles sintéticos.
- **Arquivos alterados:** `backend/strategy_factory/validator.py`

## 24. Fix: Fábrica IA — _CandleBar mock com .timestamp (2026-06-05)

- **Problema**: Após corrigir para `.epoch`, código gerado pela IA que usa `.timestamp` no candle falhou com `'_CandleBar' object has no attribute 'timestamp'`.
- **Fix**: Adicionada property `timestamp` ao `_CandleBar` mock que retorna `self.epoch`, mantendo compatibilidade com ambos os nomes.
- **Arquivo alterado:** `backend/strategy_factory/validator.py`

## 25. Fix: Fábrica IA — Logs de debug no endpoint /fix (2026-06-05)

- **Problema**: Botão "Corrigir erros" na Fábrica IA não funcionava, mas não havia logs suficientes para diagnosticar.
- **Fix**: Adicionados logs detalhados no endpoint `/api/strategy-factory/fix` para rastrear o fluxo de correção.
- **Arquivo alterado:** `backend/main.py`

- **Menu lateral:** "Fábrica IA" / "AI Factory" → `/strategy-factory`
- **Página:** `frontend/src/pages/StrategyFactory.jsx` — wizard de 5 etapas
- **IA:** KIMI (Moonshot AI) — API OpenAI-compatible em `https://api.moonshot.cn/v1`
  - `KIMI_API_KEY` — chave secreta (somente no `.env` do VPS, nunca commitada)
  - `KIMI_BASE_URL` — default `https://api.moonshot.cn/v1`
  - `KIMI_MODEL` — default `moonshot-v1-32k`
- **Módulo backend:** `backend/strategy_factory/` — kimi_client, planner, generator, validator, deployer
- **Banco:** tabela `strategies` — registra todas as estratégias (nativas + Fábrica IA)
- **IDs:** namespace semântico (TF/MR/PA/SC/RG/IF/NW + número sequencial)
- **Hot-load:** estratégias são registradas no `REGISTRY` em memória sem restart via `importlib`
- **Auto-descoberta:** `registry.py` carrega automaticamente `strategies/factory/*.py` no startup
- **Classificação semântica:** a Fábrica IA analisa a descrição e escolhe a categoria correta automaticamente
- **Validação:** executa estratégia em sandbox com 250 candles sintéticos, 12 verificações
- **Rotas:** `POST /api/strategy-factory/plan|generate|validate|deploy`, `GET|DELETE /api/strategy-factory/strategies`
- **Frontend:** sem modificações no `StrategyChecklist.jsx` — usa o path `officialEntryCriteria` (criteria_met/criteria_total) automaticamente
- **Segurança:** denylist de imports perigosos + verificação AST antes de exec()
- **REGRA:** nunca commitar a `KIMI_API_KEY` real; mantê-la somente no `.env` local/VPS

- **Problema**: Três estratégias mostravam "Todos os critérios OK ✓" nos Critérios de Entrada enquanto o bot permanecia em HOLD nos Critérios de Ordem. O checklist do frontend verificava presença de indicadores (≠ 0) mas não alinhamento direcional entre eles.
- **Estratégias afetadas e causa raiz:**
  - **A003** (Multi-Timeframe Trend): C2 mostrava verde para qualquer `trend_1h ≠ 0` (Bull ou Bear), independente do `bias_4h`. C3 mostrava verde para qualquer cruzamento MACD. Cenário falso-OK: `bias=Bull + trend=Bear + trigger_bear` → tudo verde, HOLD por conflito de direção.
  - **A001** (CFM): C4 mostrava verde para `|rsi_slope| > 1` sem verificar se a direção do slope combina com a tendência EMA. Cenário falso-OK: `trend_up=1 + rsi_slope=-3` → tudo verde, HOLD pois RSI não confirma momentum.
  - **A004** (Mean Reversion): C2 mostrava verde para `cross_up OR cross_down` sem verificar se o cruzamento combina com a zona ativa. Cenário falso-OK: `in_buy_zone=true + cross_down=true` → tudo verde, HOLD por cruzamento na direção errada.
- **Fix em `frontend/src/components/StrategyChecklist.jsx`:**
  - **A003**: C2 fica vermelho com detalhe "Conflito: alta ≠ baixa" quando `bias ≠ trend`; C3 só verde quando o trigger aponta na mesma direção do bias.
  - **A001**: C4 fica verde apenas quando slope confirma a direção EMA (`tUp && slope>0 && rsi>50` ou `tDown && slope<0 && rsi<50`); vermelho com detalhe "Momentum contrário à tendência EMA" quando oposto.
  - **A004**: C2 fica verde apenas quando cruzamento alinhado com a zona (`buyZone && cross_up` ou `sellZone && cross_down`); amarelo com detalhe "Cruzamento na direção errada — aguardando reversão" no conflito.
- **Estratégias verificadas sem este bug:** A002, A005, A006, A007, B002.
- **Arquivo alterado:** `frontend/src/components/StrategyChecklist.jsx` (somente frontend).
