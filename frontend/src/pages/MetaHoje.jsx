import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Target, Play, RefreshCw, AlertTriangle, CheckCircle2, XCircle, Clock, ShieldAlert, Info, Bot as BotIcon, Eye,
} from 'lucide-react'
import { getGoalStatus, startGoalScan, createBot } from '../api'
import { buildBotPayload } from '../botPayload'

const fmtMoney = n => `${n >= 0 ? '+' : '−'}US$ ${Math.abs(Number(n)).toFixed(2).replace('.', ',')}`
const fmtPct = (n, d = 1) => `${Number(n).toFixed(d).replace('.', ',')}%`
const fmtMillions = n => `US$ ${(Number(n) / 1e6).toLocaleString('pt-BR', { maximumFractionDigits: 0 })} mi`

function fmtAge(seconds) {
  if (seconds == null) return ''
  if (seconds < 90) return 'agora há pouco'
  if (seconds < 3600) return `há ${Math.round(seconds / 60)} min`
  return `há ${Math.round(seconds / 3600)} h`
}

// 1% ao dia, composto, durante um ano
const COMPOUND_YEAR = (1.01 ** 365 - 1) * 100

function Step({ n, title, children }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-accent/20 text-xs font-bold text-accent">{n}</span>
        <h3 className="text-sm font-bold text-white">{title}</h3>
      </div>
      <div className="space-y-1.5 text-xs leading-relaxed text-white/75">{children}</div>
    </div>
  )
}

function Rule({ icon: Icon, tone = 'text-accent', children }) {
  return (
    <li className="flex gap-2.5 text-xs leading-relaxed text-white/80">
      <Icon size={14} className={`mt-0.5 shrink-0 ${tone}`} />
      <span>{children}</span>
    </li>
  )
}

function Metric({ label, value, tone = 'text-white' }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-muted">{label}</p>
      <p className={`text-sm font-bold ${tone}`}>{value}</p>
    </div>
  )
}

function CandidateCard({ row, rank, criteria, qualifies, onCreate, creating, created, error }) {
  const nav = useNavigate()
  const inTop5 = row.in_top5_now !== false
  const canCreate = qualifies && inTop5 && !created
  return (
    <div className={`rounded-xl border p-4 ${qualifies ? 'border-green-500/30 bg-green-500/[0.06]' : 'border-white/10 bg-white/[0.03]'}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-wider text-muted">
            {qualifies ? `#${rank} candidata` : 'Não atende aos critérios'}
          </p>
          <h3 className="text-base font-bold text-white">
            {row.strategy_id} <span className="font-normal text-white/60">· {row.strategy_name.replace(new RegExp('^' + row.strategy_id + '\\s*[-–]\\s*'), '')}</span>
          </h3>
          <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted">
            <span className="font-mono text-white/80">{row.symbol}</span>
            <span>· timeframe {row.timeframe}</span>
            <span className={`rounded border px-1.5 py-0.5 text-[10px] font-bold ${inTop5 ? 'border-green-500/30 text-green-400' : 'border-red-500/40 text-red-300'}`}>
              {inTop5 ? 'no top 5 agora' : 'saiu do top 5'}
            </span>
          </p>
        </div>
        <div className="text-right">
          <p className="text-[10px] uppercase tracking-wider text-muted">Chance REAL de +{criteria.target_pct}% bruto em 24 h</p>
          <p className={`text-3xl font-black leading-none ${qualifies ? 'text-green-400' : 'text-yellow-300'}`}>{fmtPct(row.chance_pct)}</p>
          <p className="mt-1 text-[10px] text-muted">
            piso com 95% de confiança: <strong className="text-white/80">{fmtPct(row.chance_lb_pct)}</strong>
          </p>
        </div>
      </div>

      <p className="mt-3 text-[11px] text-white/60">
        Em {row.windows_hit} das {row.windows_total} janelas de 24 h testadas um bot que começasse zerado teria batido a meta bruta
        <strong> e ainda ficado com líquido positivo</strong> (≈ {Math.round(row.independent_windows)} janelas independentes em {Math.round(row.history_days)} dias de histórico).
      </p>

      <div className="mt-3 rounded-lg border border-white/10 bg-white/[0.03] p-2.5 text-[11px] text-white/70">
        <p className="mb-1 flex items-center gap-1.5 font-bold uppercase tracking-wider text-[10px] text-muted"><Eye size={11} /> Bruto x real</p>
        <p>
          Se ignorássemos as taxas, a chance seria <strong className="text-white">{fmtPct(row.gross_chance_pct)}</strong>.
          {row.windows_illusory > 0
            ? <> Em <strong className="text-yellow-300">{row.windows_illusory}</strong> janelas o bruto chegou a +{criteria.target_pct}% mas as taxas deixaram o líquido em zero ou negativo: são <strong className="text-yellow-300">acertos ilusórios</strong> e não contam.</>
            : <> Nenhum acerto foi ilusório: em todas as janelas em que o bruto bateu a meta, o líquido ficou positivo.</>}
        </p>
      </div>

      <div className="mt-3 rounded-lg border border-white/10 bg-white/[0.03] p-2.5">
        <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-muted">Como terminam as sessões de 24 h (simulação com as regras do bot)</p>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-6">
          <Metric label="Meta bruta, líquido positivo" value={fmtPct(100 * row.windows_hit / row.windows_total)} tone="text-green-400" />
          <Metric label="Meta bruta, líquido ≤ 0 (ilusão)" value={fmtPct(100 * row.windows_illusory / row.windows_total)} tone={row.windows_illusory > 0 ? 'text-yellow-300' : 'text-white'} />
          <Metric label="Stop loss" value={fmtPct(row.stop_loss_pct)} tone={row.stop_loss_pct > 0 ? 'text-bear' : 'text-white'} />
          <Metric label="Operou, sem chegar à meta" value={fmtPct(100 * row.windows_expired / row.windows_total)} />
          <Metric label="Sem ordem em 24 h" value={fmtPct(100 * row.windows_no_order / row.windows_total)} />
          <Metric label="Líquido médio por sessão" value={`${row.expected_net_pct > 0 ? '+' : ''}${Number(row.expected_net_pct).toFixed(2).replace('.', ',')}%`}
            tone={row.expected_net_pct > 0 ? 'text-bull' : 'text-bear'} />
        </div>
        <p className="mt-1.5 text-[10px] text-muted/70">
          O líquido médio por sessão conta todas as sessões, inclusive as que terminam em stop loss. Se for negativo, a estratégia perde dinheiro mesmo batendo a meta com frequência.
        </p>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Metric label="Lucro líquido no período" value={fmtMoney(row.total_profit)} tone={row.total_profit > 0 ? 'text-bull' : 'text-bear'} />
        <Metric label="Profit Factor (líquido)" value={row.profit_factor.toFixed(2).replace('.', ',')} tone={row.profit_factor >= criteria.min_profit_factor ? 'text-bull' : 'text-bear'} />
        <Metric label="Operações" value={row.trades_count} tone={row.trades_count >= criteria.min_trades ? 'text-white' : 'text-bear'} />
        <Metric label="Acerto" value={fmtPct(row.win_rate, 0)} />
        <Metric label="Maior queda" value={fmtMoney(-row.max_drawdown)} tone="text-bear" />
      </div>

      {!qualifies && row.unmet?.length > 0 && (
        <ul className="mt-3 space-y-1">
          {row.unmet.map(msg => (
            <li key={msg} className="flex gap-2 text-[11px] text-red-200/90"><XCircle size={12} className="mt-0.5 shrink-0 text-red-400" />{msg}</li>
          ))}
        </ul>
      )}

      {qualifies && (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {created ? (
            <>
              <span className="flex items-center gap-1.5 text-xs font-bold text-green-400"><CheckCircle2 size={14} /> Bot criado.</span>
              <button type="button" onClick={() => nav(`/bots/${created.id}`)} className="btn flex items-center gap-1.5 bg-white/10 text-xs text-white hover:bg-white/15">
                <BotIcon size={13} /> Abrir o bot
              </button>
              <span className="text-[11px] text-muted">A janela de 24 h só começa quando você clicar em <strong>Iniciar</strong>.</span>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() => onCreate(row)}
                disabled={!canCreate || creating}
                title={!inTop5 ? 'Este ativo saiu do top 5: refaça a busca.' : undefined}
                className="btn flex items-center gap-2 bg-accent font-bold text-black hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Target size={14} />
                {creating ? 'Criando…' : `Criar bot com meta de ${criteria.target_pct}% bruto em 24 h`}
              </button>
              {!inTop5 && <span className="text-[11px] text-red-300">O ativo saiu do top 5 desde a busca. Refaça a busca para atualizar.</span>}
            </>
          )}
          {error && <span className="text-[11px] text-red-300">{error}</span>}
        </div>
      )}
    </div>
  )
}

export default function MetaHoje() {
  const qc = useQueryClient()
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState('')
  const [creatingKey, setCreatingKey] = useState('')
  const [created, setCreated] = useState({})
  const [createError, setCreateError] = useState({})

  const { data } = useQuery({
    queryKey: ['goal-status'],
    queryFn: getGoalStatus,
    refetchInterval: q => (q.state.data?.state === 'running' ? 3000 : 30_000),
  })

  const c = data?.criteria ?? {
    target_pct: 1, net_equivalent_pct: 0.8, window_hours: 24, min_trades: 10, min_profit_factor: 1.2, min_chance_pct: 20,
    top_n: 3, min_independent_windows: 10, fee_rate_pct: 0.1, exit_slippage_pct: 0.05, exit_margin_pct: 0.05,
    strategies_considered: 0, history_cap: { '1m': 3000, '5m': 3000, '15m': 3000, '1h': 4400 },
  }
  const netEq = String(c.net_equivalent_pct).replace('.', ',')
  const state = data?.state ?? 'idle'
  const running = state === 'running'
  const progress = data?.progress ?? { done: 0, total: 0, current: null }
  const pct = progress.total ? Math.round((100 * progress.done) / progress.total) : 0
  const top = data?.top ?? []
  const closest = data?.closest ?? []
  const assets = data?.assets ?? { top5: [], used: [], free: [] }

  async function scan(force) {
    setStarting(true)
    setStartError('')
    try {
      await startGoalScan(force)
      await qc.invalidateQueries({ queryKey: ['goal-status'] })
    } catch (err) {
      setStartError(err.message)
    } finally {
      setStarting(false)
    }
  }

  async function createGoalBot(row) {
    const key = `${row.strategy_id}:${row.symbol}`
    setCreatingKey(key)
    setCreateError(prev => ({ ...prev, [key]: '' }))
    try {
      const bot = await createBot(buildBotPayload(row, row.symbol, {
        name: `META ${c.target_pct}% BRUTO · ${row.strategy_id} (${row.symbol})`,
        goal_pct: c.target_pct,
        goal_window_hours: c.window_hours,
      }))
      setCreated(prev => ({ ...prev, [key]: bot }))
      qc.invalidateQueries({ queryKey: ['bots'] })
    } catch (err) {
      setCreateError(prev => ({ ...prev, [key]: err.message }))
    } finally {
      setCreatingKey('')
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-bold"><Target size={22} className="text-accent" /> Meta 1% Bruto (24 h)</h1>
        <p className="text-sm text-muted">
          Procura, entre as estratégias intradiárias, as que mais se aproximam de +{c.target_pct}% <strong>bruto</strong> em {c.window_hours} horas
          sem deixar o resultado líquido no vermelho, e cria o bot já com a meta configurada.
        </p>
      </div>

      <div className="rounded-xl border border-yellow-500/30 bg-yellow-500/10 p-4">
        <p className="mb-1.5 flex items-center gap-2 text-sm font-bold text-yellow-200"><ShieldAlert size={16} /> Leia antes: a meta é agressiva, e é BRUTA</p>
        <ul className="list-disc space-y-1 pl-5 text-xs leading-relaxed text-yellow-100/85">
          <li>
            <strong>Bruto</strong> é o ganho de preço <em>antes</em> das taxas. Depois de pagar as taxas ({c.fee_rate_pct}% por lado), +{c.target_pct}% bruto
            vira ≈ <strong>+{netEq}% líquido</strong> numa operação só, e menos que isso se forem várias. O dinheiro que sobra é o líquido.
          </li>
          <li>
            +{c.target_pct}% <strong>por dia</strong>, composto, são ≈ <strong>+{COMPOUND_YEAR.toLocaleString('pt-BR', { maximumFractionDigits: 0 })}% ao ano</strong>
            {' '}(US$ 100 viram ≈ US$ {(100 + COMPOUND_YEAR).toLocaleString('pt-BR', { maximumFractionDigits: 0 })}). Nenhuma estratégia conhecida sustenta isso.
          </li>
          <li>O que esta página faz é <strong>medir no passado recente</strong> quantas vezes cada estratégia teria chegado lá em 24 h. É evidência, não promessa.</li>
          <li>Pode haver perda: o stop loss existe e a meta pode não ser atingida.</li>
        </ul>
      </div>

      <section className="rounded-xl border border-accent/30 bg-accent/[0.06] p-4">
        <h2 className="mb-2 flex items-center gap-2 text-sm font-bold text-white"><Eye size={16} className="text-accent" /> Critério para sair da ilusão</h2>
        <p className="mb-2 text-xs leading-relaxed text-white/75">
          Uma meta bruta é fácil de “bater” no papel: basta fazer muitas operações pequenas, e as taxas comem o resultado sem que o número bruto mostre isso.
          Por isso três regras separam acerto de ilusão:
        </p>
        <ol className="list-decimal space-y-1.5 pl-5 text-xs leading-relaxed text-white/80">
          <li>
            <strong>Um acerto só é REAL se, no instante em que o bruto chega a +{c.target_pct}%, o líquido da sessão for positivo.</strong>{' '}
            Se o líquido estiver em zero ou negativo, é um <strong>acerto ilusório</strong>: não entra na chance e aparece à parte, com a contagem.
          </li>
          <li>
            <strong>A estratégia só entra no Top {c.top_n} com resultado LÍQUIDO positivo no histórico</strong>: lucro líquido total acima de zero, Profit Factor
            líquido ≥ {String(c.min_profit_factor).replace('.', ',')} e <strong>resultado líquido médio por sessão de 24 h positivo, contando também as sessões que terminam em stop loss</strong>.
            Bater a meta com frequência não basta se os stops custam mais do que as metas rendem.
          </li>
          <li>
            <strong>Bruto e líquido aparecem lado a lado, sempre.</strong> No card do bot, no resultado do backtest e no aviso final. Se o bot parar com a meta bruta
            batida mas o líquido ≤ 0, o card avisa expressamente que foi uma ilusão.
          </li>
        </ol>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-bold uppercase tracking-wider text-muted">Como funciona</h2>
        <div className="grid gap-3 md:grid-cols-2">
          <Step n={1} title="A meta">
            <p>
              <strong>+{c.target_pct}% bruto</strong> sobre o stake fixo de US$ 100 = <strong>US$ {(c.target_pct).toFixed(2).replace('.', ',')}</strong> de variação de preço
              acumulada na sessão, <em>antes</em> das taxas ({c.fee_rate_pct}% por lado). Equivale a ≈ <strong>US$ {netEq} líquidos</strong>.
            </p>
            <p>“Hoje” = <strong>{c.window_hours} horas corridas</strong> a partir do momento em que você clica em Iniciar, não o dia do calendário.</p>
          </Step>
          <Step n={2} title="Quais ativos">
            <p>Somente o <strong>Top 5</strong> pares spot X-USDT por volume em 24 h na OKX (sem stablecoins). A lista é dinâmica e muda ao longo do dia.</p>
            <p>Entram só os que <strong>ainda não têm bot</strong>. Ao criar o bot, o servidor confere o top 5 <em>daquele momento</em> e recusa um ativo que saiu do ranking.</p>
          </Step>
          <Step n={3} title="Como medimos a chance">
            <p>
              Backtest <strong>estrito</strong> (o mesmo motor do Scanner: taxas, slippage, stop e trailing) das {c.strategies_considered || 'dezenas de'} estratégias
              validadas em {c.timeframes?.join(', ') || '1m a 1h'}, no timeframe em que o estudo as validou, nos candles recentes (até {c.history_cap['15m']} velas de 15 min ≈ 31 dias; 1h ≈ 6 meses).
            </p>
            <p>
              Uma janela de 24 h começa a cada hora. Em cada uma o bot “começa zerado” e só contam operações <strong>inteiras</strong> dentro dela, seguindo as
              regras do bot: vende quando o <em>pico</em> da operação cobre o que falta da meta, e a sessão termina em stop loss se uma operação fecha abaixo da entrada.
              <strong> Chance real</strong> = % das janelas em que o ganho <em>bruto</em> chegou a +{c.target_pct}% <em>e</em> o líquido ficou positivo.
              O <strong>piso (95%)</strong> desconta a incerteza de uma amostra curta: janelas de hora em hora se sobrepõem, então a amostra real é bem menor.
            </p>
          </Step>
          <Step n={4} title="Quem entra no Top 3">
            <p>A estratégia precisa cumprir <strong>tudo</strong>:</p>
            <ul className="list-disc space-y-0.5 pl-4">
              <li>ao menos <strong>{c.min_trades} operações</strong> no histórico;</li>
              <li><strong>lucro líquido positivo</strong> no período;</li>
              <li><strong>Profit Factor líquido ≥ {String(c.min_profit_factor).replace('.', ',')}</strong>;</li>
              <li><strong>resultado líquido médio por sessão de 24 h positivo</strong>, contando as sessões que terminam em stop loss;</li>
              <li><strong>chance real ≥ {c.min_chance_pct}%</strong> (ao menos 1 janela em cada 5 bate a meta bruta com líquido positivo);</li>
              <li><strong>histórico mínimo</strong> de {c.min_independent_windows} janelas de 24 h independentes (≈ {c.min_independent_windows} dias): com menos que isso a chance medida não significa nada.</li>
            </ul>
            <p>Se ninguém cumprir, a página diz isso. Não inventamos candidatas.</p>
          </Step>
        </div>
      </section>

      <section className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
        <h2 className="mb-3 text-sm font-bold uppercase tracking-wider text-muted">O que o bot faz quando tem uma meta</h2>
        <ul className="space-y-2">
          <Rule icon={Target} tone="text-green-400">
            <strong>Para ao atingir a meta bruta.</strong> Quando o ganho bruto da sessão (realizado + aberto) cobre +{c.target_pct}%, o bot vende a mercado
            (com folga de {c.exit_margin_pct}% para o slippage) e para. Só a posição que ele abriu é vendida. Ao parar, registra o resultado <strong>bruto e líquido</strong>.
          </Rule>
          <Rule icon={Clock} tone="text-yellow-300">
            <strong>Para se não emitir nenhuma ordem em {c.window_hours} h.</strong> Se a estratégia não der sinal na janela, o bot encerra sozinho.
          </Rule>
          <Rule icon={ShieldAlert} tone="text-blue-300">
            <strong>Com posição aberta, não para por tempo.</strong> Passadas as {c.window_hours} h, ele segue na operação e só para por <em>meta atingida</em> ou <em>stop loss</em>.
          </Rule>
          <Rule icon={XCircle} tone="text-red-300">
            <strong>Saída com prejuízo encerra a sessão.</strong> Stop loss (saída abaixo do preço de entrada) fecha a posição e para o bot: a meta daquela janela falhou.
          </Rule>
          <Rule icon={RefreshCw} tone="text-accent">
            <strong>O trailing stop continua valendo</strong> na posição, até a meta ser atingida. Uma saída por trailing com ganho pequeno <em>não</em> encerra: se ainda houver tempo, o bot busca o que falta.
          </Rule>
          <Rule icon={Info} tone="text-muted">
            Cada <strong>Iniciar</strong> abre uma janela nova de {c.window_hours} h. Reiniciar o servidor (deploy) não zera a janela em andamento.
            O algoritmo da estratégia não muda: a meta só decide <em>quando vender</em> e <em>quando parar</em>. Timeframe, parâmetros e stop do bot são fixos.
          </Rule>
        </ul>
      </section>

      <section className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-bold text-white">Buscar candidatas</h2>
            <p className="text-xs text-muted">
              Roda um backtest por estratégia e ativo livre. Leva alguns minutos; o resultado fica em cache por 30 min
              (e é refeito se o top 5 mudar).
            </p>
          </div>
          <button
            type="button"
            onClick={() => scan(state === 'done' || state === 'error')}
            disabled={starting || running}
            className="btn flex items-center gap-2 bg-accent font-bold text-black hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {running ? <div className="h-4 w-4 animate-spin rounded-full border-2 border-black border-t-transparent" />
              : state === 'done' ? <RefreshCw size={15} /> : <Play size={15} />}
            {running ? 'Buscando…' : state === 'done' ? 'Refazer a busca' : 'Buscar candidatas agora'}
          </button>
        </div>
        {startError && <p className="mt-2 text-xs text-red-300">{startError}</p>}

        {running && (
          <div className="mt-4">
            <div className="mb-1 flex justify-between text-[11px] text-muted">
              <span>{progress.done} de {progress.total} backtests{progress.current ? ` · agora: ${progress.current}` : ''}</span>
              <span>{pct}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-white/10">
              <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${pct}%` }} />
            </div>
          </div>
        )}

        {assets.top5.length > 0 && (
          <div className="mt-4 text-xs text-white/70">
            <p className="mb-1.5 font-bold uppercase tracking-wider text-muted">Top 5 no momento da busca</p>
            <div className="flex flex-wrap gap-2">
              {assets.top5.map((a, i) => {
                const used = assets.used.includes(a.symbol)
                return (
                  <span key={a.symbol} title={`${fmtMillions(a.volume_usd_24h)} em 24 h`}
                    className={`rounded border px-2 py-1 font-mono ${used ? 'border-white/10 text-muted line-through' : 'border-accent/40 text-accent'}`}>
                    #{i + 1} {a.symbol}{used ? ' · tem bot' : ''}
                  </span>
                )
              })}
            </div>
            <p className="mt-1.5 text-[11px] text-muted">
              Avaliados: {assets.free.length ? assets.free.join(', ') : 'nenhum (todos já têm bot)'}.
              {data?.age_seconds != null && ` Busca concluída ${fmtAge(data.age_seconds)}.`}
            </p>
          </div>
        )}
      </section>

      {state === 'error' && (
        <div className="flex gap-2 rounded-xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" /> A busca falhou: {data?.error}
        </div>
      )}

      {state === 'done' && data?.note && (
        <div className="rounded-xl border border-white/10 bg-white/[0.03] p-4 text-sm text-white/80">{data.note}</div>
      )}

      {state === 'done' && !data?.note && (
        <section className="space-y-4">
          <p className="text-xs text-muted">
            {data.evaluated} combinações estratégia × ativo avaliadas · {data.qualified} atendem aos critérios
            {data.failed > 0 ? ` · ${data.failed} não puderam ser avaliadas (sem dados suficientes)` : ''}.
          </p>

          {top.length > 0 ? (
            <>
              <h2 className="text-sm font-bold uppercase tracking-wider text-green-400">Top {top.length} candidatas</h2>
              {top.map((row, i) => {
                const key = `${row.strategy_id}:${row.symbol}`
                return (
                  <CandidateCard
                    key={key} row={row} rank={i + 1} criteria={c} qualifies
                    onCreate={createGoalBot} creating={creatingKey === key}
                    created={created[key]} error={createError[key]}
                  />
                )
              })}
            </>
          ) : (
            <div className="rounded-xl border border-yellow-500/30 bg-yellow-500/10 p-4">
              <p className="flex items-center gap-2 text-sm font-bold text-yellow-200"><AlertTriangle size={16} /> Nenhuma estratégia atende aos critérios agora.</p>
              <p className="mt-1.5 text-xs leading-relaxed text-yellow-100/85">
                No histórico recente, nenhuma combinação reuniu ao mesmo tempo lucro líquido positivo, Profit Factor líquido ≥ {String(c.min_profit_factor).replace('.', ',')}, resultado líquido médio por sessão positivo,
                {' '}{c.min_trades}+ operações e chance <strong>real</strong> de pelo menos {c.min_chance_pct}% de bater +{c.target_pct}% bruto em 24 h.
                Isso é coerente com a matemática da meta: +{c.target_pct}% por dia é muito mais do que as estratégias do app entregam.
                Abaixo estão as que mais se aproximaram, com o bruto e o real lado a lado, só para você ver o quão longe ficaram.
                <strong> Elas não têm botão de criar.</strong>
              </p>
            </div>
          )}

          {top.length === 0 && closest.length > 0 && (
            <>
              <h2 className="text-sm font-bold uppercase tracking-wider text-muted">Mais próximas (não recomendadas)</h2>
              {closest.map(row => (
                <CandidateCard key={`${row.strategy_id}:${row.symbol}`} row={row} rank={0} criteria={c} qualifies={false} />
              ))}
            </>
          )}
        </section>
      )}

      <p className="pb-4 text-[11px] leading-relaxed text-muted">
        Análise feita com dados passados; resultados passados não garantem o futuro e isto não é recomendação financeira.
        O app é spot, sem alavancagem, com stake fixo de US$ 100 por bot.
      </p>
    </div>
  )
}
