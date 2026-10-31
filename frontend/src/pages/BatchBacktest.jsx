import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ScanSearch, Play, HelpCircle, ChevronDown, ChevronUp, PlusCircle, CheckSquare, Square, ShieldCheck, AlertTriangle, Clock } from 'lucide-react'
import BacktestLikert from '../components/BacktestLikert'
import { getStrategies, getBots, createBot, getTopAssets } from '../api'
import { authFetch } from '../auth'
import { buildBotPayload } from '../botPayload'

const CATEGORIES = [
  { id: 'TF', label: 'Trend Following',  description: 'Estratégias que seguem a tendência (EMA, MACD, SuperTrend…)' },
  { id: 'MR', label: 'Mean Reversion',   description: 'Reversão à média (Bollinger, RSI extremos…)' },
  { id: 'PA', label: 'Price Action',     description: 'Ação do preço (Pivot, ABCD, OTR, CTE…)' },
  { id: 'SC', label: 'Scalping',         description: 'Execução rápida em movimentos curtos' },
  { id: 'RG', label: 'Regime',           description: 'Detectam o estado do mercado (Markov, Graph…)' },
  { id: 'IF', label: 'Information',      description: 'Usam dados externos (On-chain, DEX, eventos…)' },
  { id: 'NW', label: 'Network',          description: 'Relações entre ativos (Influencers & Followers)' },
]

const ALL_ASSETS = '__ALL_ASSETS__'          // opção "Todos os ativos" (os livres, sem bot, do top 5)
const prefixOf = id => (id || '').match(/^([A-Z]+)/i)?.[1]?.toUpperCase()
const SECONDS_PER_STRATEGY = 15              // referência medida: ~15 s por estratégia (2 workers)
const CONSISTENT_MIN_ASSETS = 3              // aprovada em >= 3 ativos = "consistente"

const VERDICT_CFG = {
  INICIAR:       { color: 'text-green-400',  bg: 'bg-green-500/15',  border: 'border-green-500/30'  },
  CUIDADO:       { color: 'text-yellow-400', bg: 'bg-yellow-500/15', border: 'border-yellow-500/30' },
  'NÃO INICIAR': { color: 'text-red-400',    bg: 'bg-red-500/15',    border: 'border-red-500/30'    },
  'N/A':         { color: 'text-gray-400',   bg: 'bg-white/5',       border: 'border-white/10'      },
}

const COVERAGE_CFG = {
  full:        { label: 'Estrito',          color: 'text-green-300',  bg: 'bg-green-500/10',  border: 'border-green-500/25',  icon: ShieldCheck },
  partial:     { label: 'Parcial',          color: 'text-yellow-300', bg: 'bg-yellow-500/10', border: 'border-yellow-500/25', icon: AlertTriangle },
  unsupported: { label: 'Não contemplado',  color: 'text-gray-300',   bg: 'bg-white/5',       border: 'border-white/10',      icon: HelpCircle },
}

function MetricPill({ label, value, positive }) {
  const color = positive === true ? 'text-green-400' : positive === false ? 'text-red-400' : 'text-white'
  return (
    <div className="flex flex-col items-center px-3 py-1.5 rounded-lg bg-white/5 min-w-[72px]">
      <span className="text-[10px] text-muted uppercase tracking-wider mb-0.5">{label}</span>
      <span className={`text-sm font-bold font-mono ${color}`}>{value}</span>
    </div>
  )
}

function ScorePill({ label, value, tone = 'white' }) {
  const color = tone === 'green'
    ? 'text-green-400'
    : tone === 'yellow'
    ? 'text-yellow-400'
    : tone === 'red'
    ? 'text-red-400'
    : 'text-white'
  return (
    <div className="flex flex-col px-3 py-2 rounded-lg bg-black/20 border border-white/8 min-w-[92px]">
      <span className="text-[10px] text-muted uppercase tracking-wider mb-0.5">{label}</span>
      <span className={`text-sm font-bold font-mono ${color}`}>{value}</span>
    </div>
  )
}

function RobustnessBlock({ robust, gate }) {
  if (!robust || !robust.all) return null
  const minTrades = gate?.min_trades ?? 30
  const { all, in_sample: ins, out_of_sample: oos, bootstrap: boot } = robust
  if (!all.trades) return null

  const oosTone = !robust.oos_conclusive ? 'white' : robust.oos_positive ? 'green' : 'red'
  const oosText = !robust.oos_conclusive
    ? `inconclusivo (${oos.trades} trades)`
    : `${oos.profit >= 0 ? '+' : ''}$${oos.profit.toFixed(2)} · PF ${oos.profit_factor.toFixed(2)} · ${oos.trades} trades`
  const pTone = boot.prob_pf_gt_1 >= 0.95 ? 'green' : boot.prob_pf_gt_1 >= 0.8 ? 'yellow' : 'red'

  return (
    <div className="rounded-lg border border-white/10 bg-black/20 p-2 mb-2">
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <span className="text-[11px] font-bold text-white/80">Robustez da amostra</span>
        <span className="text-[10px] text-muted">INICIAR exige ≥ {minTrades} trades neste timeframe e fora da amostra positivo</span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <ScorePill label="Fora da amostra (últimos 30%)" value={oosText} tone={oosTone} />
        <ScorePill label="Dentro da amostra (70%)" value={`PF ${ins.profit_factor.toFixed(2)} · ${ins.trades} trades`} />
        <ScorePill label="PF pessimista (5º pct.)" value={boot.pf_p5.toFixed(2)} tone={boot.pf_p5 >= 1 ? 'green' : 'red'} />
        <ScorePill label="Chance de PF > 1" value={`${Math.round(boot.prob_pf_gt_1 * 100)}%`} tone={pTone} />
      </div>
      {all.trades < minTrades && (
        <p className="text-[11px] text-yellow-300/90 mt-1.5">
          Poucos trades ({all.trades}; mínimo de {minTrades} neste timeframe): o resultado pode ser sorte.
        </p>
      )}
      {gate?.failed?.length > 0 && all.trades >= minTrades && (
        <p className="text-[11px] text-yellow-300/90 mt-1.5">{gate.failed[0]}.</p>
      )}
      {all.trades >= minTrades && (
        <p className="text-[11px] mt-1.5 text-white/55">
          Amostra de {all.trades} trades em {robust.bars_evaluated} candles.
        </p>
      )}
    </div>
  )
}

// mm:ss (ou h:mm:ss) a partir de milissegundos
function formatElapsed(ms) {
  const total = Math.max(0, Math.floor((ms || 0) / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const sec = total % 60
  const mm = String(m).padStart(h ? 2 : 1, '0')
  const ss = String(sec).padStart(2, '0')
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

// "Adequada para iniciar": veredito INICIAR com a robustez aprovada (>= 30 trades e fora da amostra positivo).
function isSuitableToStart(r) {
  const rec = r?.recommendation
  return rec?.verdict === 'INICIAR' && !!rec?.robust_gate?.applied && !!rec?.robust_gate?.passed
}

// Contagem por faixa de resultado (usada na barra de progresso, enquanto o scan roda).
const EMPTY_TALLY = { INICIAR: 0, CUIDADO: 0, 'NÃO INICIAR': 0, 'N/A': 0, suitable: 0 }
function tallyVerdicts(rows) {
  const t = { ...EMPTY_TALLY }
  for (const r of rows || []) {
    const v = VERDICT_CFG[r.recommendation?.verdict] ? r.recommendation.verdict : 'N/A'
    t[v] += 1
    if (isSuitableToStart(r)) t.suitable += 1
  }
  return t
}
function sumTallies(items) {
  return items.reduce((acc, it) => {
    for (const k of Object.keys(EMPTY_TALLY)) acc[k] += it.tally?.[k] || 0
    return acc
  }, { ...EMPTY_TALLY })
}

function TallyChips({ tally, size = 'text-[10px]' }) {
  const chips = [
    ['INICIAR', 'bg-green-500/15 text-green-400 border-green-500/30'],
    ['CUIDADO', 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30'],
    ['NÃO INICIAR', 'bg-red-500/15 text-red-400 border-red-500/30'],
    ['N/A', 'bg-white/5 text-gray-400 border-white/10'],
  ]
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {chips.map(([k, cls]) => (
        <span key={k} className={`px-2 py-0.5 rounded-full ${size} font-bold border ${cls} ${tally[k] ? '' : 'opacity-40'}`}>{tally[k]} {k}</span>
      ))}
      <span className={`px-2 py-0.5 rounded-full ${size} font-bold border bg-accent/15 text-accent border-accent/30 ${tally.suitable ? '' : 'opacity-40'}`}
        title="INICIAR com robustez aprovada (mínimo de trades por timeframe e fora da amostra positivo)">{tally.suitable} adequadas</span>
    </span>
  )
}

function median(values) {
  if (!values.length) return 0
  const v = [...values].sort((a, b) => a - b)
  const mid = Math.floor(v.length / 2)
  return v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2
}

// Quantos ativos aprovam cada estratégia: uma estratégia que passa em vários ativos é muito mais
// crível do que uma que passa em um só (com muitos testes, passar uma vez pode ser sorte).
function AssetConsistency({ rows }) {
  const by = {}
  for (const r of rows) {
    if (!r.recommendation || r.recommendation.verdict === 'N/A') continue
    const entry = by[r.strategy_id] || (by[r.strategy_id] = { id: r.strategy_id, name: r.strategy_name, assets: [] })
    entry.assets.push({
      symbol: r.symbol,
      verdict: r.recommendation.verdict,
      score: r.recommendation.score ?? 0,
      suitable: isSuitableToStart(r),
    })
  }
  const list = Object.values(by).map(e => ({
    ...e,
    approved: e.assets.filter(a => a.suitable).length,
    medianScore: median(e.assets.map(a => a.score)),
  })).sort((a, b) => b.approved - a.approved || b.medianScore - a.medianScore)
  if (!list.length) return null

  const anyApproved = list.some(e => e.approved > 0)
  const chip = (a) => a.suitable
    ? 'bg-green-500/20 text-green-300 border-green-500/40'
    : a.verdict === 'CUIDADO'
    ? 'bg-yellow-500/10 text-yellow-300 border-yellow-500/25'
    : 'bg-red-500/10 text-red-300/80 border-red-500/20'

  return (
    <div className="card p-4 mb-4">
      <div className="flex items-center justify-between gap-2 mb-2">
        <span className="text-sm font-bold">Consistência entre ativos</span>
        <span className="text-[11px] text-muted">aprovada = INICIAR com robustez aprovada · consistente = em {CONSISTENT_MIN_ASSETS}+ ativos</span>
      </div>
      {!anyApproved && (
        <p className="text-xs text-yellow-300/90 mb-2">
          Nenhuma estratégia passou nos critérios em nenhum dos ativos. É o resultado mais comum e o mais honesto.
        </p>
      )}
      <div className="space-y-1.5 max-h-72 overflow-y-auto">
        {list.slice(0, 15).map(e => (
          <div key={e.id} className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-mono font-bold w-14 shrink-0">{e.id}</span>
            <span className={`w-28 shrink-0 ${e.approved >= CONSISTENT_MIN_ASSETS ? 'text-green-300 font-bold' : e.approved > 0 ? 'text-white' : 'text-muted'}`}>
              {e.approved} de {e.assets.length} ativos{e.approved >= CONSISTENT_MIN_ASSETS ? ' · consistente' : ''}
            </span>
            <span className="text-muted w-20 shrink-0">nota med. {e.medianScore.toFixed(2)}</span>
            <span className="flex flex-wrap gap-1">
              {e.assets.map(a => (
                <span key={a.symbol} title={`${a.symbol}: ${a.verdict} (nota ${Number(a.score).toFixed(2)})${a.suitable ? ' · robustez aprovada' : ''}`}
                  className={`px-1.5 py-0.5 rounded border font-mono text-[10px] ${chip(a)}`}>
                  {a.symbol.replace('-USDT', '')}{a.suitable ? ' ✔' : ''}
                </span>
              ))}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

function resultKey(r, fallbackSymbol = '') {
  return `${r.strategy_id}:${r.symbol || fallbackSymbol}`
}

async function readErrorMessage(res) {
  const text = await res.text().catch(() => '')
  if (!text) return `HTTP ${res.status}`
  try {
    const err = JSON.parse(text)
    return err.detail || err.message || text || `HTTP ${res.status}`
  } catch {
    return text || `HTTP ${res.status}`
  }
}

async function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

async function requestCategoryScan(payload, maxAttempts = 3) {
  let lastError = null
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      const res = await authFetch('/api/backtest/category', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!res.ok) {
        const message = await readErrorMessage(res)
        return { ok: false, status: res.status, message, attempts: attempt }
      }
      const data = await res.json()
      return { ok: true, data, attempts: attempt }
    } catch (err) {
      lastError = err
      const isLast = attempt === maxAttempts
      if (!isLast) {
        // Backoff curto para falhas transitórias de rede/proxy.
        await sleep(500 * attempt)
        continue
      }
    }
  }
  return {
    ok: false,
    status: 0,
    message: `Falha de rede após ${maxAttempts} tentativas: ${lastError?.message || 'erro desconhecido'}`,
    attempts: maxAttempts,
  }
}

function ResultCard({ r, symbol, selected, disabled, disabledReason, onToggle }) {
  const [open, setOpen] = useState(false)
  const nav = useNavigate()
  const rec = r.recommendation
  const cfg = VERDICT_CFG[rec.verdict] || VERDICT_CFG['N/A']
  const hasMetrics = r.trades_count !== undefined
  const isNA = rec.verdict === 'N/A'
  const predictive = r.predictive || null
  const predScore = rec.predictive_score ?? predictive?.predictive_score
  const historicalScore = rec.historical_score ?? rec.score
  const canSelect = !isNA && !disabled && !rec.study_retired     // aposentada pelo estudo: a criação do bot a recusaria
  const coverage = r.coverage || { level: isNA ? 'unsupported' : 'partial', reasons: [] }
  const coverageCfg = COVERAGE_CFG[coverage.level] || COVERAGE_CFG.partial
  const CoverageIcon = coverageCfg.icon

  function handleCreateBot() {
    nav(`/bots/new?strategy=${r.strategy_id}&symbol=${encodeURIComponent(symbol)}`)
  }

  return (
    <div className={`rounded-xl border ${cfg.border} ${cfg.bg} p-4`}>
      {/* Header */}
      <div className="flex items-center gap-3 mb-2">
        <div className="flex-1 min-w-0">
          <span className="font-bold text-white">{r.strategy_id}</span>
          <span className="ml-2 text-sm text-muted truncate">{r.strategy_name}</span>
        </div>
        <span className="text-[10px] text-muted font-mono shrink-0">{r.timeframe}</span>
        <span
          title={(coverage.reasons || []).join(' | ')}
          className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold border ${coverageCfg.bg} ${coverageCfg.border} ${coverageCfg.color}`}
        >
          <CoverageIcon size={11} />
          {coverage.label || coverageCfg.label}
        </span>
        {isNA && (
          <span className="flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-bold text-gray-400 border border-white/10">
            <HelpCircle size={11} />N/A
          </span>
        )}
        {!isNA && (
          <button
            onClick={() => canSelect && onToggle?.()}
            disabled={!canSelect}
            title={disabledReason}
            className={`flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-bold border transition-colors shrink-0 ${
              selected
                ? 'bg-green-500/15 text-green-400 border-green-500/30'
                : canSelect
                ? 'bg-white/5 text-muted border-white/10 hover:text-white hover:border-white/20'
                : 'bg-white/3 text-muted/50 border-white/8 cursor-not-allowed'
            }`}
          >
            {selected ? <CheckSquare size={13} /> : <Square size={13} />}
            Marcar
          </button>
        )}
        {!isNA && (
          <button
            onClick={handleCreateBot}
            className="flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs font-bold bg-accent/15 text-accent border border-accent/30 hover:bg-accent/25 transition-colors shrink-0"
          >
            <PlusCircle size={13} />
            Criar Bot
          </button>
        )}
      </div>

      {/* Likert scale (non-N/A strategies) */}
      {!isNA && <BacktestLikert score={rec.score ?? 0} />}
      {!isNA && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-2 mb-2">
          <ScorePill label="Score Final" value={(rec.score ?? 0).toFixed(2)} tone={(rec.score ?? 0) >= 6.67 ? 'green' : (rec.score ?? 0) >= 3.33 ? 'yellow' : 'red'} />
          <ScorePill label="Histórico" value={(historicalScore ?? 0).toFixed(2)} />
          <ScorePill label="Preditivo" value={predScore != null ? Number(predScore).toFixed(2) : '—'} tone={Number(predScore ?? 0) >= 6.67 ? 'green' : Number(predScore ?? 0) >= 3.33 ? 'yellow' : 'red'} />
          <ScorePill label="Confiança" value={predictive?.confidence != null ? `${Math.round(predictive.confidence * 100)}%` : '—'} />
        </div>
      )}

      {/* Metrics row */}
      {hasMetrics && (
        <div className="flex flex-wrap gap-2 mt-2 mb-2">
          <MetricPill label="PnL" value={`${r.total_profit >= 0 ? '+' : ''}$${r.total_profit?.toFixed(2)}`} positive={r.total_profit > 0} />
          <MetricPill label="Trades" value={r.trades_count} />
          <MetricPill label="Win Rate" value={`${r.win_rate?.toFixed(1)}%`} positive={r.win_rate >= 50} />
          <MetricPill label="Prof. Factor" value={r.profit_factor?.toFixed(2)} positive={r.profit_factor >= 1.2} />
          <MetricPill label="Drawdown" value={`$${r.max_drawdown?.toFixed(2)}`} positive={false} />
          <MetricPill label="Saldo Final" value={`$${r.final_balance?.toFixed(2)}`} />
          {predictive?.regime && <MetricPill label="Regime" value={predictive.regime} />}
        </div>
      )}

      {hasMetrics && <RobustnessBlock robust={r.robust} gate={r.recommendation?.robust_gate} />}

      {/* Reasons toggle */}
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1 text-xs text-muted hover:text-white transition-colors mt-1"
      >
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        {open ? 'Ocultar detalhes' : 'Ver detalhes'}
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          <ul className="space-y-1">
            {rec.reasons.map((reason, i) => (
              <li key={i} className="text-xs text-white/70 flex gap-2">
                <span className={`shrink-0 ${cfg.color}`}>·</span>
                <span>{reason}</span>
              </li>
            ))}
          </ul>
          {(coverage.reasons || []).length > 0 && (
            <div className={`rounded-lg border ${coverageCfg.border} ${coverageCfg.bg} p-2`}>
              <div className={`flex items-center gap-1.5 text-[11px] font-bold ${coverageCfg.color}`}>
                <CoverageIcon size={12} />
                Cobertura do Backtest: {coverage.label || coverageCfg.label}
              </div>
              <ul className="mt-1 space-y-0.5">
                {coverage.reasons.map((reason, i) => (
                  <li key={i} className="text-[11px] text-white/65 flex gap-2">
                    <span className="shrink-0">·</span>
                    <span>{reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {predictive && (
            <div className="rounded-lg border border-accent/20 bg-accent/5 p-2">
              <div className="flex items-center justify-between gap-2 text-[11px] font-bold text-accent">
                <span>Modelo Preditivo: {predictive.model || 'rule_based'}</span>
                <span>{Number(predictive.predictive_score ?? 0).toFixed(2)}/10 · {Math.round(Number(predictive.confidence ?? 0) * 100)}%</span>
              </div>
              <ul className="mt-1 space-y-0.5">
                {(predictive.reasons || []).map((reason, i) => (
                  <li key={i} className="text-[11px] text-white/65 flex gap-2">
                    <span className="shrink-0 text-accent">·</span>
                    <span>{reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function BatchBacktest() {
  const qc = useQueryClient()
  const [category, setCategory] = useState('TF')
  const [symbol, setSymbol]     = useState('')
  const [loading, setLoading]   = useState(false)
  const [results, setResults]   = useState(null)
  const [error, setError]       = useState(null)
  const [selectedRows, setSelectedRows] = useState({})
  const [bulkCreating, setBulkCreating] = useState(false)
  const [bulkMessage, setBulkMessage] = useState(null)
  const [scanProgress, setScanProgress] = useState(null)
  const [now, setNow] = useState(Date.now())   // relógio do temporizador (atualiza 1x/s durante o scan)
  const [lastScanMs, setLastScanMs] = useState(null)
  const [stopWhenSuitable, setStopWhenSuitable] = useState(false)   // parar na 1ª estratégia adequada para iniciar

  const { data: strategies = [] } = useQuery({ queryKey: ['strategies'], queryFn: getStrategies })
  const { data: bots = [] }       = useQuery({ queryKey: ['bots'],       queryFn: getBots       })
  const { data: topData }         = useQuery({ queryKey: ['top-assets'], queryFn: getTopAssets, staleTime: 60_000 })
  const SYMBOLS = topData?.assets?.map(a => a.symbol) ?? []          // só o top 5; sem ranking, nenhum ativo

  const strategyCounts = strategies.reduce((acc, st) => {
    const prefix = prefixOf(st.id)
    if (prefix) acc[prefix] = (acc[prefix] || 0) + 1
    return acc
  }, {})
  const categoryStrategies = strategies
    .filter(st => prefixOf(st.id) === category)
    .sort((a, b) => a.id.localeCompare(b.id))
  // Estratégias marcadas da categoria: começam todas marcadas (e voltam a todas ao trocar de categoria)
  const [pickedIds, setPickedIds] = useState(() => new Set(categoryStrategies.map(st => st.id)))
  const botStrategyIds = new Set(bots.map(b => b.strategy_id))
  const usingTopAssets = Boolean(topData?.assets?.length)               // false = OKX inacessível, lista reserva

  const usedSymbols = new Set(bots.map(b => b.symbol))
  const isAllAssets = symbol === ALL_ASSETS
  const freeAssets = SYMBOLS.filter(s => !usedSymbols.has(s))          // ativos sem bot (os únicos que dá para operar)
  const pickedCount = pickedIds.size
  const estimateMin = isAllAssets
    ? Math.max(1, Math.round((pickedCount * freeAssets.length * SECONDS_PER_STRATEGY) / 60))
    : null
  const selectedList = Object.values(selectedRows)


  // Ao escolher a categoria (ou quando a lista de estratégias chega), todas as dela vêm marcadas.
  useEffect(() => {
    setPickedIds(new Set(categoryStrategies.map(st => st.id)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category, strategies.length])

  function toggleStrategy(id) {
    setPickedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  useEffect(() => {
    if (!loading) return undefined
    setNow(Date.now())
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [loading])

  function setCategoryProgress(catId, status, extra = {}) {
    setScanProgress(prev => {
      if (!prev) return prev
      const stamp = Date.now()
      const items = prev.items.map(item =>
        item.id === catId
          ? {
              ...item, status, ...extra,
              ...(status === 'running' ? { startedAt: stamp, finishedAt: null } : {}),
              ...(status === 'done' || status === 'failed' ? { finishedAt: stamp } : {}),
            }
          : item
      )
      const completed = items.filter(item => ['done', 'failed', 'skipped'].includes(item.status)).length
      const current = items.find(item => item.status === 'running')?.id || null
      return { ...prev, items, completed, current }
    })
  }

  const run = async () => {
    const scanStartedAt = Date.now()
    setLoading(true)
    setLastScanMs(null)
    setScanProgress(null)
    setResults(null)
    setSelectedRows({})
    setBulkMessage(null)
    setError(null)
    try {
      if (pickedIds.size === 0) {
        throw new Error('Selecione ao menos uma estratégia para executar.')
      }
      const assetMode = symbol === ALL_ASSETS
      // unidade de scan: um ativo (modo "Todos os ativos") ou a categoria escolhida
      const categoriesToScan = assetMode ? freeAssets : [category]
      if (!categoriesToScan.length) {
        throw new Error(assetMode ? 'Nenhum ativo livre (sem bot) para escanear.' : 'Nenhuma estratégia elegível para escanear.')
      }
      setScanProgress({
        total: categoriesToScan.length,
        completed: 0,
        current: null,
        startedAt: scanStartedAt,
        unitKind: assetMode ? 'asset' : 'category',
        items: categoriesToScan.map(cat => ({ id: cat, status: 'pending', resultCount: 0, error: '', startedAt: null, finishedAt: null })),
      })
      const merged = []
      const failedCategories = []
      let stopInfo = null

      for (const unit of categoriesToScan) {
        const cat = assetMode ? category : unit        // categoria da requisição
        const sym = assetMode ? unit : symbol          // ativo da requisição
        setCategoryProgress(unit, 'running', { error: '' })
        const scan = await requestCategoryScan(
          { category: cat, symbol: sym, include_strategies: [...pickedIds], ...(stopWhenSuitable ? { stop_when_suitable: true } : {}) },
          3,
        )
        if (!scan.ok) {
          const message = scan.message
          setCategoryProgress(unit, 'failed', { error: message })
          if (categoriesToScan.length > 1) {
            failedCategories.push(`${unit}: ${message}`)
            continue
          }
          throw new Error(message)
        }
        const data = scan.data
        setCategoryProgress(unit, 'done', { resultCount: (data.results || []).length, tally: tallyVerdicts(data.results) })
        merged.push(...(data.results || []).map(r => ({ ...r, category: cat, symbol: sym })))
        if (stopWhenSuitable) {
          const hit = (data.results || []).filter(isSuitableToStart)
            .sort((a, b) => (b.recommendation?.score ?? 0) - (a.recommendation?.score ?? 0))[0]
          if (hit) {
            stopInfo = { strategy: hit.strategy_id, score: hit.recommendation.score, category: cat, symbol: sym, skipped: data.skipped || 0 }
            // categorias que ainda não rodaram ficam marcadas como ignoradas
            setScanProgress(prev => {
              if (!prev) return prev
              const items = prev.items.map(item => item.status === 'pending' ? { ...item, status: 'skipped' } : item)
              const completed = items.filter(item => ['done', 'failed', 'skipped'].includes(item.status)).length
              return { ...prev, items, completed, current: null }
            })
            break
          }
        }
      }
      if (!merged.length && failedCategories.length) {
        throw new Error(`Nenhum resultado retornado. ${failedCategories.join(' | ')}`)
      }
      setResults({
        category,
        symbol: assetMode ? ALL_ASSETS : symbol,
        multiAsset: assetMode,
        assetCount: categoriesToScan.length,
        failedCategories,
        stopInfo,
        total: merged.length,
        results: merged.sort((a, b) => {
          const verdictA = VERDICT_CFG[a.recommendation?.verdict] ? a.recommendation?.verdict : 'N/A'
          const verdictB = VERDICT_CFG[b.recommendation?.verdict] ? b.recommendation?.verdict : 'N/A'
          const orderA = { INICIAR: 0, CUIDADO: 1, 'NÃO INICIAR': 2, 'N/A': 3 }[verdictA] ?? 3
          const orderB = { INICIAR: 0, CUIDADO: 1, 'NÃO INICIAR': 2, 'N/A': 3 }[verdictB] ?? 3
          if (orderA !== orderB) return orderA - orderB
          return (b.recommendation?.score || 0) - (a.recommendation?.score || 0)
        }),
      })
    } catch (e) {
      setScanProgress(prev => {
        if (!prev) return prev
        const items = prev.items.map(item => {
          if (item.status === 'pending' || item.status === 'running') {
            return { ...item, status: 'failed', error: item.error || e.message, finishedAt: Date.now() }
          }
          return item
        })
        const completed = items.filter(item => item.status === 'done' || item.status === 'failed').length
        return { ...prev, items, completed, current: null }
      })
      setError(e.message)
    } finally {
      setLastScanMs(Date.now() - scanStartedAt)
      setLoading(false)
    }
  }

  function selectionBlockReason(row, rowSymbol) {
    if (usedSymbols.has(rowSymbol)) return 'Já existe um bot usando este ativo'
    const duplicateSymbol = selectedList.some(item => item.symbol === rowSymbol && item.key !== resultKey(row, rowSymbol))
    if (duplicateSymbol) return 'Só é permitido um bot por ativo'
    return ''
  }

  function toggleSelection(row, rowSymbol) {
    const key = resultKey(row, rowSymbol)
    const disabledReason = selectionBlockReason(row, rowSymbol)
    if (!selectedRows[key] && disabledReason) return
    setSelectedRows(prev => {
      if (prev[key]) {
        const next = { ...prev }
        delete next[key]
        return next
      }
      return {
        ...prev,
        [key]: { key, ...row, symbol: rowSymbol },
      }
    })
  }

  function selectEligible(rows, fallbackSymbol) {
    const next = {}
    const symbols = new Set()
    for (const row of rows) {
      const rowSymbol = row.symbol || fallbackSymbol
      const rec = row.recommendation || {}
      if (!rowSymbol || rec.verdict === 'N/A' || rec.study_retired) continue
      if (usedSymbols.has(rowSymbol) || symbols.has(rowSymbol)) continue
      symbols.add(rowSymbol)
      next[resultKey(row, rowSymbol)] = { key: resultKey(row, rowSymbol), ...row, symbol: rowSymbol }
    }
    setSelectedRows(next)
  }

  async function createSelectedBots() {
    const rows = selectedList
    if (!rows.length) return
    setBulkCreating(true)
    setBulkMessage(null)
    setError(null)
    const created = []
    const failed = []
    for (const row of rows) {
      try {
        const bot = await createBot(buildBotPayload(row, row.symbol))
        created.push({ row, bot })
      } catch (err) {
        failed.push({ row, error: err.message })
      }
    }
    qc.invalidateQueries({ queryKey: ['bots'] })
    setSelectedRows({})
    setBulkCreating(false)
    setBulkMessage({
      type: failed.length ? 'warning' : 'success',
      text: failed.length
        ? `${created.length} bot(s) criados; ${failed.length} falharam: ${failed.map(f => `${f.row.strategy_id}/${f.row.symbol}: ${f.error}`).join(' | ')}`
        : `${created.length} bot(s) criados com sucesso.`,
    })
  }

  const counts = results ? {
    INICIAR:       results.results.filter(r => r.recommendation.verdict === 'INICIAR').length,
    CUIDADO:       results.results.filter(r => r.recommendation.verdict === 'CUIDADO').length,
    'NÃO INICIAR': results.results.filter(r => r.recommendation.verdict === 'NÃO INICIAR').length,
    'N/A':         results.results.filter(r => r.recommendation.verdict === 'N/A').length,
  } : null
  const coverageCounts = results ? {
    full:        results.results.filter(r => r.coverage?.level === 'full').length,
    partial:     results.results.filter(r => r.coverage?.level === 'partial').length,
    unsupported: results.results.filter(r => r.coverage?.level === 'unsupported').length,
  } : null

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <ScanSearch size={24} className="text-accent" />
        <div>
          <h1 className="text-2xl font-bold">Scanner de Backtest</h1>
          <p className="text-sm text-muted">Backtest Estrito + score preditivo: histórico OKX, regime recente, volatilidade, liquidez e momentum</p>
        </div>
      </div>

      {bulkMessage && (
        <div className={`mb-4 rounded-lg border p-3 text-sm ${
          bulkMessage.type === 'success'
            ? 'border-green-500/30 bg-green-500/10 text-green-300'
            : 'border-yellow-500/30 bg-yellow-500/10 text-yellow-300'
        }`}>
          {bulkMessage.text}
        </div>
      )}

      {/* Controls */}
      <div className="card p-5 mb-6 space-y-4">
        <div className="rounded-lg border border-green-500/20 bg-green-500/5 p-3 text-xs text-white/70">
          <div className="flex items-center gap-2 font-bold text-green-300 mb-1">
            <ShieldCheck size={14} />
            Modo Estrito + Preditivo ativado
          </div>
          O score final combina backtest histórico, oportunidade preditiva recente, robustez da amostra e cobertura operacional. Estratégias “Não contemplado” dependem de contexto externo histórico que a app ainda não reconstrói.
        </div>

        {/* Category selector */}
        <div>
          <label className="text-xs text-muted uppercase tracking-wider mb-2 block">Categoria</label>
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
            {CATEGORIES.map(cat => {
              const count    = strategyCounts[cat.id] ?? 0
              const catBots  = bots.filter(b => (b.strategy_id || '').startsWith(cat.id))
              const isActive = category === cat.id
              return (
                <button
                  key={cat.id}
                  onClick={() => setCategory(cat.id)}
                  title={cat.description}
                  className={`px-3 py-2 rounded-lg text-sm font-bold border transition-all ${
                    isActive
                      ? 'bg-accent/20 border-accent/50 text-accent'
                      : 'bg-white/5 border-white/10 text-muted hover:text-white hover:border-white/20'
                  }`}
                >
                  {cat.id}
                  <div className="text-[9px] font-normal opacity-70 truncate">{cat.label}</div>
                  {count > 0 && (
                    <div className={`text-[9px] font-bold mt-0.5 ${isActive ? 'text-accent/80' : 'text-muted/60'}`}>
                      {count} {count === 1 ? 'estratégia' : 'estratégias'}
                    </div>
                  )}
                  {catBots.length > 0 && (
                    <div className="mt-1 pt-1 border-t border-white/10 text-left">
                      <span className={`text-[8px] font-bold ${isActive ? 'text-green-400/90' : 'text-green-400/60'}`}>
                        {catBots.length} bot{catBots.length > 1 ? 's' : ''}
                      </span>
                      <div className={`text-[8px] font-mono leading-tight truncate ${isActive ? 'text-white/60' : 'text-muted/50'}`}>
                        {catBots.map(b => b.strategy_id).join(', ')}
                      </div>
                    </div>
                  )}
                </button>
              )
            })}
          </div>
          {category && (
            <p className="text-xs text-muted mt-2">{CATEGORIES.find(c => c.id === category)?.description}</p>
          )}

          {categoryStrategies.length > 0 && (
            <div className="mt-3 rounded-lg border border-white/10 bg-white/[0.03] p-3">
              <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                <span className="text-xs text-muted uppercase tracking-wider">Estratégias de {category}</span>
                <div className="flex items-center gap-3 text-[11px]">
                  <span className="text-muted">{pickedCount} de {categoryStrategies.length} selecionadas</span>
                  <button
                    type="button"
                    onClick={() => setPickedIds(new Set(categoryStrategies.map(st => st.id)))}
                    disabled={loading}
                    className="text-accent hover:underline disabled:opacity-50"
                  >
                    Selecionar todas
                  </button>
                  <button
                    type="button"
                    onClick={() => setPickedIds(new Set())}
                    disabled={loading}
                    className="text-muted hover:text-white hover:underline disabled:opacity-50"
                  >
                    Limpar
                  </button>
                </div>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5 max-h-60 overflow-y-auto pr-1">
                {categoryStrategies.map(st => {
                  const checked = pickedIds.has(st.id)
                  return (
                    <label
                      key={st.id}
                      title={st.description || st.name}
                      className={`flex items-center gap-2 rounded-md border px-2 py-1.5 text-xs cursor-pointer select-none transition-colors ${
                        checked ? 'border-accent/40 bg-accent/10 text-white' : 'border-white/10 bg-white/5 text-muted hover:text-white'
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleStrategy(st.id)}
                        disabled={loading}
                        className="accent-[rgb(var(--accent-rgb))]"
                      />
                      <span className="font-mono font-bold shrink-0">{st.id}</span>
                      <span className="truncate text-[11px] opacity-80">{(st.name || '').replace(new RegExp('^' + st.id + '\\s*[-–]\\s*'), '')}</span>
                      {botStrategyIds.has(st.id) && (
                        <span className="ml-auto shrink-0 text-[9px] font-bold text-green-400/80">bot</span>
                      )}
                    </label>
                  )
                })}
              </div>
            </div>
          )}
        </div>

        {/* Symbol */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[160px]">
            <label className="text-xs text-muted uppercase tracking-wider mb-0.5 block">
              {usingTopAssets ? 'Top 5 Ativos' : 'Top 5 Ativos — indisponível agora'}
            </label>
            <p className="text-[11px] text-muted/70 mb-2">
              {usingTopAssets
                ? 'Os 5 pares spot X-USDT de maior volume em 24h na OKX (sem stablecoins). Ativos que já têm bot ficam desabilitados.'
                : 'Não foi possível consultar o ranking da OKX. Sem ele nenhum ativo é oferecido: o app nunca usa ativos fora do top 5.'}
            </p>
            <div className="flex gap-2 flex-wrap">
              {SYMBOLS.map((s, idx) => {
                const inUse = usedSymbols.has(s)
                const info = topData?.assets?.find(a => a.symbol === s)
                return (
                  <button
                    key={s}
                    onClick={() => !inUse && setSymbol(s)}
                    disabled={inUse}
                    title={inUse
                      ? 'Já existe um bot usando este ativo'
                      : info ? `#${idx + 1} em volume · US$ ${(info.volume_usd_24h / 1e6).toFixed(0)} mi em 24h · ${info.change_24h_pct >= 0 ? '+' : ''}${info.change_24h_pct}%` : undefined}
                    className={`px-2.5 py-1 rounded text-xs font-mono border transition-all ${
                      inUse
                        ? 'opacity-35 cursor-not-allowed bg-white/3 border-white/8 text-muted line-through'
                        : symbol === s
                        ? 'bg-accent/20 border-accent/50 text-accent'
                        : 'bg-white/5 border-white/10 text-muted hover:text-white'
                    }`}
                  >
                    <span className="opacity-50 mr-1">#{idx + 1}</span>{s}
                  </button>
                )
              })}
              <button
                onClick={() => setSymbol(ALL_ASSETS)}
                disabled={!freeAssets.length}
                title={`Roda as estratégias marcadas nos ${freeAssets.length} ativos do top 5 que não têm bot e compara a consistência entre eles`}
                className={`px-2.5 py-1 rounded text-xs font-bold border transition-all ${
                  !freeAssets.length
                    ? 'opacity-35 cursor-not-allowed bg-white/3 border-white/8 text-muted'
                    : isAllAssets
                    ? 'bg-accent/20 border-accent/50 text-accent'
                    : 'bg-white/5 border-white/10 text-muted hover:text-white'
                }`}
              >
                Todos os ativos ({freeAssets.length})
              </button>
            </div>
            {isAllAssets && (
              <p className="text-[11px] text-muted mt-1.5">
                {pickedCount} estratégia(s) de {category} em {freeAssets.length} ativos sem bot ({freeAssets.map(a => a.replace('-USDT', '')).join(', ')}).
                {estimateMin != null && ` Estimativa grosseira: ~${estimateMin} min (referência de ${SECONDS_PER_STRATEGY} s por estratégia).`}
              </p>
            )}
          </div>

          <label className="flex items-start gap-2 text-sm text-white/80 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={stopWhenSuitable}
              onChange={e => setStopWhenSuitable(e.target.checked)}
              disabled={loading}
              className="mt-0.5 accent-[rgb(var(--accent-rgb))]"
            />
            <span>
              Parar assim que encontrar uma estratégia adequada para iniciar.
              <span className="block text-[11px] text-muted">
                Adequada = veredito INICIAR com robustez aprovada (mínimo de trades por timeframe e fora da amostra positivo). Se nenhuma atender, o scan roda por inteiro.
              </span>
            </span>
          </label>

          <button
            onClick={run}
            disabled={loading || !symbol || pickedCount === 0 || (isAllAssets && !freeAssets.length)}
            className="btn flex items-center gap-2 bg-accent text-black font-bold hover:bg-accent/90 shadow-[0_0_20px_rgba(var(--accent-rgb),0.3)] disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading
              ? <div className="w-4 h-4 border-2 border-black border-t-transparent rounded-full animate-spin" />
              : <Play size={15} />
            }
            {loading
              ? 'Executando…'
              : isAllAssets
              ? `Executar ${pickedCount} estratégia(s) em Todos os Ativos`
              : `Executar ${pickedCount} estratégia(s) selecionada(s)`}
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {loading && (
        <div className="card p-5 mb-6 space-y-4">
          <div className="flex items-center gap-2 text-muted">
            <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
            <p className="text-sm">Executando backtest de {pickedCount} estratégia(s) {isAllAssets ? `de ${category} em ${freeAssets.length} ativos` : `de ${category}`}…</p>
          </div>
          <p className="text-xs text-muted">Isso pode levar alguns segundos</p>

          {scanProgress && (
            <>
              <div>
                <div className="flex items-center justify-between text-xs text-muted mb-1.5">
                  <span>Progresso geral</span>
                  <span className="flex items-center gap-3">
                    <span className="flex items-center gap-1 font-mono text-white" title="Tempo decorrido desde o início do scan">
                      <Clock size={12} className="text-accent" />
                      {formatElapsed(now - scanProgress.startedAt)}
                    </span>
                    <span>{scanProgress.completed}/{scanProgress.total}</span>
                  </span>
                </div>
                <div className="h-2 rounded-full bg-white/10 overflow-hidden">
                  <div
                    className="h-2 bg-accent transition-all"
                    style={{ width: `${Math.round((scanProgress.completed / Math.max(1, scanProgress.total)) * 100)}%` }}
                  />
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <span className="text-[10px] text-muted">Resultados até agora:</span>
                  <TallyChips tally={sumTallies(scanProgress.items)} size="text-xs" />
                </div>
              </div>

              <div className="space-y-2">
                {scanProgress.items.map(item => {
                  const meta = CATEGORIES.find(c => c.id === item.id)
                  const isRunning = item.status === 'running'
                  const isDone = item.status === 'done'
                  const isFailed = item.status === 'failed'
                  const isSkipped = item.status === 'skipped'
                  return (
                    <div key={item.id} className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
                      <div className="flex items-center justify-between gap-2 mb-1">
                        <div className="text-xs font-bold text-white">{item.id} {meta ? `- ${meta.label}` : ''}</div>
                        <div className={`flex items-center gap-2 text-[10px] font-bold ${isDone ? 'text-green-400' : isFailed ? 'text-red-400' : isRunning ? 'text-accent' : 'text-muted'}`}>
                          {item.startedAt && (
                            <span className="font-mono font-normal text-white/70">
                              {formatElapsed((item.finishedAt || now) - item.startedAt)}
                            </span>
                          )}
                          <span>{isDone ? `Concluida (${item.resultCount})` : isFailed ? 'Falhou' : isSkipped ? 'Ignorada (estratégia adequada encontrada)' : isRunning ? 'Executando' : 'Pendente'}</span>
                        </div>
                      </div>
                      <div className="h-1.5 rounded-full bg-white/10 overflow-hidden">
                        <div
                          className={`h-1.5 transition-all ${isDone ? 'bg-green-400' : isFailed ? 'bg-red-400' : isSkipped ? 'bg-white/20' : isRunning ? 'bg-accent animate-pulse' : 'bg-transparent'}`}
                          style={{ width: isDone || isFailed || isSkipped ? '100%' : isRunning ? '55%' : '0%' }}
                        />
                      </div>
                      {isDone && item.tally && (
                        <div className="mt-1.5"><TallyChips tally={item.tally} /></div>
                      )}
                      {isFailed && item.error && (
                        <div className="text-[10px] text-red-300/90 mt-1 truncate" title={item.error}>{item.error}</div>
                      )}
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </div>
      )}

      {/* Summary badges */}
      {results && counts && (
        <>
          {results.stopInfo && (
            <div className="mb-4 rounded-lg border border-green-500/30 bg-green-500/10 p-3 text-sm text-green-300">
              Scan interrompido: {results.stopInfo.strategy} ({results.multiAsset ? `${results.stopInfo.symbol}, ` : ''}categoria {results.stopInfo.category}, nota {Number(results.stopInfo.score).toFixed(2)}) está adequada para iniciar: veredito INICIAR com robustez aprovada.
              {results.stopInfo.skipped > 0 && ` ${results.stopInfo.skipped} estratégia(s) dessa categoria não chegaram a rodar.`}
              {' '}{results.multiAsset ? 'Os demais ativos' : 'As demais categorias'} não foram escaneados.
            </div>
          )}

          {results.failedCategories?.length > 0 && (
            <div className="mb-4 rounded-lg border border-yellow-500/30 bg-yellow-500/10 p-3 text-sm text-yellow-300">
              {results.multiAsset ? 'Alguns ativos falharam' : 'Algumas categorias falharam'} e foram ignorados: {results.failedCategories.join(' | ')}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3 mb-4">
            <span className="text-sm text-muted">{results.total} estratégias · {results.multiAsset ? `${results.assetCount} ativos` : results.symbol}</span>
            {lastScanMs != null && (
              <span className="flex items-center gap-1 text-xs text-muted" title="Duração total do último scan">
                <Clock size={12} /> {formatElapsed(lastScanMs)}
              </span>
            )}
            {coverageCounts && (
              <>
                <span className="px-2.5 py-1 rounded-full text-xs font-bold bg-green-500/10 text-green-300 border border-green-500/25">
                  {coverageCounts.full} estrito
                </span>
                {coverageCounts.partial > 0 && (
                  <span className="px-2.5 py-1 rounded-full text-xs font-bold bg-yellow-500/10 text-yellow-300 border border-yellow-500/25">
                    {coverageCounts.partial} parcial
                  </span>
                )}
                {coverageCounts.unsupported > 0 && (
                  <span className="px-2.5 py-1 rounded-full text-xs font-bold bg-white/5 text-gray-300 border border-white/10">
                    {coverageCounts.unsupported} não contemplado
                  </span>
                )}
              </>
            )}
            <button
              onClick={() => selectEligible(results.results, results.symbol)}
              className="px-2.5 py-1 rounded border border-white/10 bg-white/5 text-xs text-muted hover:text-white"
            >
              Marcar elegíveis
            </button>
            <button
              onClick={() => setSelectedRows({})}
              className="px-2.5 py-1 rounded border border-white/10 bg-white/5 text-xs text-muted hover:text-white"
            >
              Limpar seleção
            </button>
            <button
              onClick={createSelectedBots}
              disabled={!selectedList.length || bulkCreating}
              className="px-2.5 py-1 rounded border border-accent/30 bg-accent/15 text-xs font-bold text-accent hover:bg-accent/25 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {bulkCreating ? 'Criando…' : `Criar ${selectedList.length || ''} bot(s)`}
            </button>
            {counts['INICIAR'] > 0 && (
              <span className="px-2.5 py-1 rounded-full text-xs font-bold bg-green-500/15 text-green-400 border border-green-500/30">
                {counts['INICIAR']} INICIAR
              </span>
            )}
            {counts['CUIDADO'] > 0 && (
              <span className="px-2.5 py-1 rounded-full text-xs font-bold bg-yellow-500/15 text-yellow-400 border border-yellow-500/30">
                {counts['CUIDADO']} CUIDADO
              </span>
            )}
            {counts['NÃO INICIAR'] > 0 && (
              <span className="px-2.5 py-1 rounded-full text-xs font-bold bg-red-500/15 text-red-400 border border-red-500/30">
                {counts['NÃO INICIAR']} NÃO INICIAR
              </span>
            )}
            {counts['N/A'] > 0 && (
              <span className="px-2.5 py-1 rounded-full text-xs font-bold bg-white/5 text-gray-400 border border-white/10">
                {counts['N/A']} N/A
              </span>
            )}
          </div>

          {results.multiAsset && <AssetConsistency rows={results.results} />}

          <div className="space-y-3">
            {results.results.map(r => {
              const rowSymbol = r.symbol || results.symbol
              const key = resultKey(r, rowSymbol)
              const disabledReason = selectionBlockReason(r, rowSymbol)
              return (
                <ResultCard
                  key={`${r.strategy_id}-${rowSymbol}`}
                  r={r}
                  symbol={rowSymbol}
                  selected={Boolean(selectedRows[key])}
                  disabled={Boolean(disabledReason)}
                  disabledReason={disabledReason}
                  onToggle={() => toggleSelection(r, rowSymbol)}
                />
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
