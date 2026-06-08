---
name: okxstrategy
description: |
  OKXStrategy - Algorithmic crypto trading application for OKX only.

  Use this skill when asked about: OKXStrategy, OKX trading bots, strategies,
  Telegram operational alerts, OKX credentials via database, Docker/Traefik
  deployment, Hostinger VPS, or okx.tradixio.com.
---

# OKXStrategy — Referência do Projeto

**Exchange:** OKX only (spot)
**Repository:** https://github.com/romualdoalves/okxstrategy
**Deploy path (VPS):** `/opt/okx-strategy`
**Domain:** https://okx.tradixio.com · VPS IP: `187.127.251.139`
**Telegram:** `@OKX_StrategyBot` · `TELEGRAM_CHAT_ID=6753071411`

---

## REGRA ABSOLUTA — Operacional

Trabalhe somente no repositório `E:\Dell Inspiron\W\Dev\Trading\OKXTrader\OKXStrategy`.

### Ritual obrigatório de entrega
- Sempre atualizar este `SKILL.md` quando houver mudança operacional, deploy,
  regra de segurança, banco de dados ou fluxo de trabalho. Sem histórico — apenas
  o estado atual da app.
- Sempre commitar e enviar para o GitHub antes de responder com comandos de deploy.
- Ao final de pedidos de deploy, responder somente com os comandos para o VPS.
- A pasta `/opt/okx-strategy` já existe no VPS — não incluir `mkdir` nos comandos.

### Conexão OKX via banco
- Credenciais cadastradas via `POST /api/account/connect` — nunca via `.env`.
- `OKXExchange` e `OKXPrivateStream` leem chaves exclusivamente da tabela `settings`
  (criptografadas).
- Ao conectar nova conta: app apaga bots, trades, signal logs, snapshots e rejeições
  locais antes de salvar as novas chaves.
- Desconectar remove as chaves e para bots ativos.

### Regra OKX-only
- Todas as estratégias operam somente na OKX. `EXCHANGE_PROVIDER != okx` é rejeitado.
- Não adicionar adapters, streams ou rotas para outra corretora.

### Stake fixo US$100 — inviolável
- Toda entrada usa exatamente US$100. Não configurável em nenhuma camada.
- Qualquer `stake_usd` recebido por API é sobrescrito para `100.0`.
- Startup força `stake_usd = 100.0` em todos os bots existentes.
- Backtest: stake fixo US$100, SPOT-only, SELL com bot flat é ignorado (nunca short).
- Dashboard exibe exposição pelo notional real (`size × preço`); stake é fallback
  apenas quando preço não foi carregado ainda.
- Nunca sugerir alterar stake como ação operacional.

### Um ativo por bot
- Cada símbolo pode existir em apenas um bot (validado em create/edit/start).
- `BotManager` mantém trava runtime. Banco tem constraint único em `bots.symbol`.

### Baseline de holdings pré-existentes
- Ao criar um bot, `POST /api/bots` faz snapshot do saldo spot do ativo na OKX e
  armazena em `bots.baseline_balance` (quantidade bruta do ativo, ex: 1.0 BTC).
- A detecção de divergências e o orphan recovery usam `(saldo_okx − baseline)` como
  referência, não o saldo bruto. Holdings pré-existentes (demo ou reais) são ignorados.
- Se `(saldo_okx − baseline) ≈ $100` → posição órfã legítima, adotada pelo bot.
- Se `(saldo_okx − baseline) ≤ 0` → bot fica FLAT sem alertas de divergência.
- Exibido no card do bot no Dashboard (componente `BotCard.jsx`).
- Bots criados antes desta feature têm `baseline_balance = 0` — recriar para corrigir.

### Auditoria de rejeições
- Toda rejeição crítica de ordem persistida em `order_rejections`.
- APIs: `GET /api/order-rejections`, `PATCH /api/order-rejections/{id}`.

### Stop Loss e trailing stop
- OKX API v5: trailing stop usa `ordType="move_order_stop"` com
  `callbackRatio`/`callbackSpread`.
- Se trailing falhar → fallback SL fixo + registra rejeição do trailing.
- `_recover_state()` sempre recria trailing stop ao reiniciar, independente de `tp1_done`.
- Orphan recovery calcula `tp1 = entry × 1.02 (LONG) / 0.98 (SHORT)`.

### Telegram
- Bot: `@OKX_StrategyBot` · chat_id: `6753071411`.
- Token real só no `.env` do VPS — nunca commitar.
- `/api/system/telegram-test` envia snapshot de saldo/posição (não mensagem genérica).
- Se `Bad Request: chat not found`: usuário não enviou `/start` para o bot ou token
  pertence a outro bot.
- Editar token no VPS: `nano /opt/okx-strategy/.env` — não usar `sed` (trunca no `:`).
- Timestamps em mensagens: `%Y-%m-%d %H:%M:%S UTC`.

### Deploy e banco
- Banco operacional: `okx_strategy` (PostgreSQL, volume `okx_strategy_pgdata`).
- **Nunca** apagar, renomear ou recriar volumes Docker sem autorização explícita.
- Deploys preservam banco por padrão — apenas rebuild do container `okx_strategy`.

---

## REGRA ABSOLUTA — Conformidade App ↔ OKX

> Prioridade máxima sobre qualquer outra decisão técnica.

Qualquer discrepância exige: identificar causa → corrigir código → Reset Geral →
reiniciar com conta limpa.

**Mecanismos implementados:**
- WS `orders` → fill confirmado em tempo real (`OKXPrivateStream`)
- `_confirm_fill_price` → fallback REST ~4 min, 7 tentativas
- `_reconcile_loop` → a cada 15 s, detecta orphans e desyncs
- Ordem rejeitada/cancelada → deleta trade e reseta bot
- Reset Geral → `POST /api/account/reset`

**P&L:** `pnl = size * ct_size * (price - entry_price) * direction`
(sinal correto para todas as combinações LONG/SHORT × ganho/perda).

---

## Arquitetura

| Layer | Tech |
|-------|------|
| Backend | FastAPI + SQLAlchemy + PostgreSQL (asyncio) |
| Frontend | React + Vite + Tailwind + TanStack Query |
| Exchange | OKX REST/WS via `backend/exchanges/okx.py` |
| Feeds | CoinGecko, DexScreener, Deribit GEX, OKX Rubik |
| Notificações | Telegram (`backend/notifications/`) |
| IA | KIMI Moonshot (`backend/strategy_factory/`) |

**Container único** (porta 8000): FastAPI serve `/api/*`, `/ws` e catch-all SPA.
**PostgreSQL** em container separado, mesma rede `traefik-public`.

```text
OKXStrategy/
├── backend/
│   ├── main.py                    # FastAPI app + todos os endpoints REST
│   ├── bot_manager.py             # Orquestração de bots (BotRunner + BotManager)
│   ├── backtest_engine.py         # BacktestEngine, backtest_score(), backtest_recommendation()
│   ├── database.py                # SQLAlchemy models (BotModel.baseline_balance incluído)
│   ├── exchanges/okx.py           # OKXExchange + OKXPrivateStream
│   ├── strategies/
│   │   ├── registry.py            # REGISTRY + auto-descoberta factory/
│   │   ├── base.py                # StrategyBase, StrategyResult, StrategyInfo
│   │   └── factory/               # Estratégias da Fábrica IA (volume Docker)
│   ├── strategy_factory/          # kimi_client, planner, generator, validator, deployer
│   ├── feeds/                     # gex_feed, okx_market_players, onchain_monitor…
│   └── notifications/             # Telegram message_builder
├── frontend/src/
│   ├── pages/
│   │   ├── Dashboard.jsx          # Lista de bots com BotCard
│   │   ├── BotDetail.jsx          # Detalhes do bot + backtest individual + Likert
│   │   ├── Monitor.jsx            # Monitoramento em tempo real + botão Liquidar
│   │   ├── BatchBacktest.jsx      # Scanner: backtest de toda uma categoria de estratégias
│   │   ├── Strategies.jsx
│   │   ├── Activities.jsx
│   │   └── StrategyFactory.jsx    # Wizard 5 etapas (IA)
│   └── components/
│       ├── BotCard.jsx            # Card do bot (inclui baseline_balance)
│       ├── BacktestLikert.jsx     # Escala visual 0–10 compartilhada (BotDetail + Scanner)
│       ├── StrategyChecklist.jsx
│       └── Chart.jsx
├── docker-compose.prod.yml
├── Dockerfile
└── SKILL.md
```

---

## Banco de dados — colunas relevantes

| Tabela | Coluna | Descrição |
|--------|--------|-----------|
| `bots` | `baseline_balance` | Qtde bruta do ativo spot na OKX ao criar o bot (ex: 1.0 BTC). Migrations automáticas no startup. |
| `bots` | `stake_usd` | Sempre 100.0 — sobrescrito no create/edit/startup. |
| `settings` | `key/value` | Credenciais OKX criptografadas (nunca em `.env`). |
| `order_rejections` | — | Auditoria de ordens rejeitadas/canceladas. |

Migrations adicionais são executadas via lista `migrations` em `database.py` usando
`ALTER TABLE … ADD COLUMN IF NOT EXISTS` — seguro em redeployments.

---

## Backtest

- **Engine:** `BacktestEngine` em `backend/backtest_engine.py`. 500 candles, stake $100, saldo inicial $1.000 (buffer de simulação).
- **SPOT-only:** BUY abre long, SELL fecha long, SELL com bot flat é ignorado.
- **`backtest_score(r)`:** Score contínuo 0–10.00. PF como driver principal
  (PF=1.0→3.0, 2.0→5.5, 3.0→8.0), ajustado por trades (±1.0), drawdown (±0.5) e win rate (±0.3).
- **`backtest_recommendation(r)`:** Retorna `{verdict, level, score, reasons[]}`.
  Zonas: NÃO INICIAR (0–3.33) · CUIDADO (3.33–6.67) · INICIAR (6.67–10).
- **`BacktestLikert.jsx`:** Componente visual compartilhado — track com 3 zonas coloridas,
  marcador branco na posição exata, score com 2 decimais + badge categórico.
- **Percentuais nos bullets:** referenciados ao stake de $100 (não ao saldo simulado de $1.000).
- **Estratégias context-dependent** (`needs_gex_context`, `needs_graph_context`): aparecem
  como N/A no scanner — não podem ser backtestadas sem feed ao vivo.

### Scanner de Backtest (`/batch-backtest`)
- Usuário escolhe categoria (TF/MR/PA/SC/RG/IF/NW) + ativo e clica "Executar".
- `POST /api/backtest/category`: filtra REGISTRY por prefixo, agrupa por `recommended_timeframe`,
  busca candles uma vez por TF, roda backtests em paralelo (`asyncio.gather`).
- Resultado ordenado: INICIAR → CUIDADO → NÃO INICIAR → N/A; dentro de cada grupo por PF desc.
- Cada card mostra Likert + métricas + bullets expansíveis + botão **Criar Bot** (navega para
  `/bots/new?strategy=ID&symbol=ATIVO` com campos pré-preenchidos).

---

## Estratégias

### Taxonomia semântica

| Prefixo | Categoria | Exemplos |
|---------|-----------|---------|
| **TF** | Trend Following | TF001 EMA+VWAP, TF003 MACD, TF005 Di Napoli |
| **MR** | Mean Reversion | MR001 ByeBot, MR004 Hilega Milega |
| **PA** | Price Action | PA001 Pivot Sniper, PA002 ABCD, PA003 OTR |
| **SC** | Scalping | SC001 Pattern Scalp, SC003 Whale Flow VWAP |
| **RG** | Regime | RG001 Markov, RG002 Graph Regime |
| **IF** | Information | IF001 Onchain Whale, IF003 Event-Driven Flow |
| **NW** | Network | NW001 Influencers & Followers |
| **T** | Test | T000 |

- Prefixos F, FX, B, A, S, I, M **não existem mais**.
- Arquivos: `backend/strategies/factory/[a-z]{2}[0-9]{3}.py`
- Volume Docker `okx_strategy_factory` → `/app/backend/strategies/factory`

### Critérios de ordem
O backend expõe `runtime.order_criteria` — a ordem só é enviada quando BUY/SELL
**e** todos os critérios de ordem estão verdes: runtime ativo, circuit breaker livre,
posição flat, sem entrada pendente, direção permitida, calendário livre, OKX conectada,
tamanho válido, SL/TP coerentes, posição OKX flat.

- Sem sinal BUY/SELL os critérios O2–O11 ficam dormentes (`status="none"`).
- Frontend usa `_criteria_met/_criteria_total/_criteria_names` do backend — nunca heurísticas locais.

### Fábrica IA
- Menu "Fábrica IA" → `/strategy-factory` (wizard 5 etapas: plan → generate → validate → deploy → hot-load).
- `KIMI_API_KEY` só no `.env` — nunca commitar.
- Hot-load sem restart via `importlib`; auto-descoberta no startup.
- Validação: sandbox 250 candles sintéticos, 12 checks, AST safety.
- `_CandleBar` mock suporta `.epoch`, `.timestamp`, `.time`.
- `criteria_total` corrigido automaticamente por regex pós-geração.
- Delete button aceita regex `^(TF|MR|PA|SC|RG|IF|NW|F|FX)\d`.
- `assign_next_id` considera arquivos órfãos no disco para reutilizar IDs deletados.

### S013 — Viana Mini-Índice
- Price Action + VWAP intradiária + players OKX + confirmação por FVG ou barra ignorada.
- Feed: `backend/feeds/okx_market_players.py` (endpoints públicos OKX, sem auth).
  - Divergência vendedora: varejo comprado (`ratio > 1.4`) + top traders vendidos (`shortRatio > 0.55`).
  - Divergência compradora: varejo vendido (`ratio < 0.8`) + top traders comprados (`longRatio > 0.55`).
- `require_market_players=true` bloqueia se feed indisponível.

---

## Deploy

### Atualização rápida (uso frequente)
```bash
git -C /opt/okx-strategy pull origin main
docker compose -f /opt/okx-strategy/docker-compose.prod.yml build --no-cache okx_strategy
docker compose -f /opt/okx-strategy/docker-compose.prod.yml up -d okx_strategy
docker compose -f /opt/okx-strategy/docker-compose.prod.yml ps
docker logs -f okx_strategy
```

### Primeiro setup (VPS virgem)
```bash
git clone https://github.com/romualdoalves/okxstrategy.git /opt/okx-strategy
docker network create traefik-public 2>/dev/null || true
docker volume create okx_strategy_pgdata 2>/dev/null || true
docker volume create okx_strategy_factory 2>/dev/null || true
cp /opt/okx-strategy/.env.production.example /opt/okx-strategy/.env
nano /opt/okx-strategy/.env
docker compose -f /opt/okx-strategy/docker-compose.prod.yml build --no-cache
docker compose -f /opt/okx-strategy/docker-compose.prod.yml up -d
docker logs -f okx_strategy
```

### Variáveis de ambiente (`.env` do VPS)
```text
EXCHANGE_PROVIDER=okx
OKX_DEMO=true

KIMI_API_KEY=          # Fábrica IA — nunca commitar
DEEPSEEK_API_KEY=
ETHERSCAN_API_KEY=

TELEGRAM_BOT_TOKEN=    # nunca commitar
TELEGRAM_CHAT_ID=6753071411

POSTGRES_PASSWORD=crypto
DATABASE_URL=postgresql://crypto:crypto@postgres:5432/okx_strategy
```

---

## Troubleshooting

### HTTPS/TLS
- `okx.tradixio.com` → `187.127.251.139`. HTTP redireciona 308 → HTTPS. TLS válido.
- Diagnóstico no VPS: `docker logs traefik` e `curl -vk https://okx.tradixio.com`.
- `ERR_SSL_PROTOCOL_ERROR` no browser local mas curl no VPS retorna 200: problema de
  cache DNS/socket do cliente — não é falha no servidor.
- `/api/health` aceita GET e HEAD.

### Container 404 ou não sobe
```bash
docker ps                                                    # okx_strategy healthy?
docker logs okx_strategy                                     # uvicorn porta 8000?
docker network inspect traefik-public                        # ambos na rede?
docker exec okx_strategy ls /app/backend/static_frontend/   # index.html presente?
```

### Banco
```bash
# Backup
docker exec okx_strategy_db pg_dump -U crypto okx_strategy > backup.sql
# Restore
docker exec -i okx_strategy_db psql -U crypto -d okx_strategy < backup.sql
```

### Divergência OKX falsa
Se o Monitor mostrar "Divergência" para um bot com holdings pré-existentes:
1. O bot foi criado antes da feature `baseline_balance` — tem `baseline_balance = 0`.
2. Solução: delete o bot e recrie — o snapshot capturará o saldo atual como baseline.
3. Reset Geral (`POST /api/account/reset`) limpa o alerta mas não corrige o baseline.
