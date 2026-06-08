---
name: okxstrategy
description: |
  OKXStrategy - Algorithmic crypto trading application for OKX only.

  Use this skill when asked about: OKXStrategy, OKX trading bots, strategies,
  Telegram operational alerts, OKX credentials via database, Docker/Traefik
  deployment, Hostinger VPS, or okx.tradixio.com.
---

# OKXStrategy — Project State

**Last updated:** 2026-06-08
**Exchange:** OKX only (spot)
**Repository:** https://github.com/romualdoalves/okxstrategy
**Deploy path (VPS):** `/opt/okx-strategy`
**Domain:** https://okx.tradixio.com · VPS IP: `187.127.251.139`
**Telegram:** `@OKX_StrategyBot` · `TELEGRAM_CHAT_ID=6753071411`

---

## Changelog Recente

### 2026-06-08 — feat: Botão Liquidar na página Monitor
- Coluna "Ações" adicionada à tabela de bots em `Monitor.jsx`.
- Botão "Liquidar" visível apenas para bots com `direction !== 'FLAT'`.
- Chama `POST /api/bots/{bot_id}/liquidate` (endpoint já existia no backend).
- Loading por linha (não bloqueia outros bots), banner de feedback verde/vermelho.
- Confirmação via `window.confirm` antes de enviar a ordem.

### 2026-06-08 — feat: Recomendação automática pós-backtest
- `BotDetail.jsx` — `getBacktestRecommendation()` exibe badge colorido após backtest.
- **INICIAR** (verde): PnL > 0, PF ≥ 1.2, ≥ 3 trades, drawdown ≤ lucro.
- **CUIDADO** (amarelo): passa os hard stops mas tem alguma ressalva.
- **NÃO INICIAR** (vermelho): zero trades, PnL ≤ 0, ou PF < 1.0.

### 2026-06-08 — fix: Backtest reescrito para SPOT-only
- BUY abre long; SELL fecha long; SELL com bot flat é ignorado (nunca short).
- Stake fixo US$100, saldo inicial US$1,000.
- Métricas: Win Rate, Profit Factor, Max Drawdown, posição aberta + PnL não realizado.

### 2026-06-05/06 — feat: Fábrica de Estratégias IA
- Wizard 5 etapas: plan → generate → validate → deploy → hot-load.
- IA: KIMI `moonshot-v1-32k` — `KIMI_API_KEY` só no `.env`, nunca commitada.
- IDs semânticos auto-atribuídos: TF/MR/PA/SC/RG/IF/NW + número sequencial.
- Sandbox: 250 candles sintéticos, 12 verificações, AST safety check.
- `criteria_total` corrigido automaticamente por regex pós-geração.

### 2026-05-28 — feat: WebSocket real de fills + Activities cross-day FIFO
- `OKXPrivateStream` canal `orders` para confirmação de fill em tempo real.
- `_confirm_fill_price` reduzido a 7 tentativas (~4 min) como fallback REST.
- Activities: FIFO cross-day carrega fills desde o dia da entrada mais antiga aberta.

---

## REGRA ABSOLUTA — OKXStrategy Operacional

Trabalhe somente no repositório `E:\Dell Inspiron\W\Dev\Trading\OKXTrader\OKXStrategy`.

### Ritual obrigatório de entrega
- Sempre atualizar este `SKILL.md` quando houver mudança operacional, deploy,
  regra de segurança, banco de dados ou fluxo de trabalho.
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
- Backtest: stake fixo US$100, SPOT-only, SELL com bot flat é ignorado.
- Dashboard exibe exposição pelo notional real (`size × preço`); stake é fallback
  apenas quando preço não foi carregado ainda.
- Orphan recovery: adota saldo spot da OKX só se notional ≤ tolerância de US$100;
  caso contrário, vira alerta de divergência.
- Nunca sugerir alterar stake como ação operacional.

### Um ativo por bot
- Cada símbolo pode existir em apenas um bot (validado em create/edit/start).
- `BotManager` mantém trava runtime. Banco tem constraint único em `bots.symbol`.

### Auditoria de rejeições
- Toda rejeição crítica de ordem persistida em `order_rejections`.
- APIs: `GET /api/order-rejections`, `PATCH /api/order-rejections/{id}`.

### Stop Loss e trailing stop
- OKX API v5: trailing stop usa `ordType="move_order_stop"` com
  `callbackRatio`/`callbackSpread`.
- Se trailing falhar → fallback SL fixo + registra rejeição do trailing.
- `_recover_state()` sempre recria trailing stop ao reiniciar, independente de
  `tp1_done`.
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

### S013 — Viana Mini-Índice
- Price Action + VWAP intradiária + players OKX + confirmação por FVG ou barra ignorada.
- Feed: `backend/feeds/okx_market_players.py` (endpoints públicos OKX, sem auth).
  - Divergência vendedora: varejo comprado (`ratio > 1.4`) + top traders vendidos
    (`shortRatio > 0.55`).
  - Divergência compradora: varejo vendido (`ratio < 0.8`) + top traders comprados
    (`longRatio > 0.55`).
- `require_market_players=true` bloqueia se feed indisponível.

---

## REGRA ABSOLUTA — Conformidade App ↔ OKX

> Prioridade máxima sobre qualquer outra decisão técnica.

Qualquer discrepância exige: identificar causa → corrigir código → Reset Geral →
reiniciar com conta limpa.

**Mecanismos implementados:**
- WS `orders` → fill confirmado em tempo real (`OKXPrivateStream`)
- `_confirm_fill_price` → fallback REST ~4 min, 7 tentativas
- `_reconcile_loop` → a cada 5 min, detecta posições DB que a OKX não tem
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
│   ├── main.py                  # FastAPI app
│   ├── bot_manager.py           # Orquestração de bots
│   ├── backtest_engine.py       # Backtest SPOT-only
│   ├── database.py              # SQLAlchemy models
│   ├── exchanges/okx.py         # OKXExchange + OKXPrivateStream
│   ├── strategies/
│   │   ├── registry.py          # REGISTRY + auto-descoberta factory/
│   │   ├── base.py              # StrategyBase, StrategyResult, StrategyInfo
│   │   └── factory/             # Estratégias da Fábrica IA (volume Docker)
│   ├── strategy_factory/        # kimi_client, planner, generator, validator, deployer
│   ├── feeds/                   # gex_feed, okx_market_players, onchain_monitor…
│   └── notifications/           # Telegram message_builder
├── frontend/src/
│   ├── pages/                   # Dashboard, BotDetail, Strategies, Activities…
│   └── components/              # StrategyChecklist, BotCard, Chart…
├── docker-compose.prod.yml
├── Dockerfile
└── SKILL.md
```

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

### Critérios de ordem (separados dos critérios da estratégia)
O backend expõe `runtime.order_criteria` — a ordem só é enviada quando BUY/SELL
**e** todos os critérios de ordem estão verdes: runtime ativo, circuit breaker livre,
posição flat, sem entrada pendente, direção permitida, calendário livre, OKX conectada,
tamanho válido, SL/TP coerentes, posição OKX flat.

- Sem sinal BUY/SELL os critérios O2-O11 ficam dormentes (`status="none"`).
- Frontend usa `_criteria_met/_criteria_total/_criteria_names` do backend — nunca
  heurísticas locais.

### Fábrica IA
- Menu "Fábrica IA" → `/strategy-factory` (wizard 5 etapas).
- `KIMI_API_KEY` só no `.env` — nunca commitar.
- Hot-load sem restart via `importlib`; auto-descoberta no startup.
- Validação: sandbox 250 candles sintéticos, 12 checks, AST safety.
- `_CandleBar` mock suporta `.epoch`, `.timestamp`, `.time`.
- `criteria_total` corrigido automaticamente por regex pós-geração.
- Delete button aceita regex `^(TF|MR|PA|SC|RG|IF|NW|F|FX)\d`.
- `assign_next_id` considera arquivos órfãos no disco para reutilizar IDs deletados.

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
# Subir Traefik separadamente se necessário (traefik:v3.0, portas 80/443, acme TLS)
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
