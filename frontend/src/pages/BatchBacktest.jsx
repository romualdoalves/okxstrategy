import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ScanSearch, Play, HelpCircle, ChevronDown, ChevronUp, PlusCircle, CheckSquare, Square, ShieldCheck, AlertTriangle, Clock, Zap } from 'lucide-react'
import BacktestLikert from '../components/BacktestLikert'
import { getStrategies, getBots, createBot, getAutoScanStatus, toggleAutoScan, getAutoScanRuns } from '../api'

const CATEGORIES = [
  { id: 'TF', label: 'Trend Following',  description: 'Estratégias que seguem a tendência (EMA, MACD, SuperTrend…)' },
  { id: 'MR', label: 'Mean Reversion',   description: 'Reversão à média (Bollinger, RSI extremos…)' },
  { id: 'PA', label: 'Price Action',     description: 'Ação do preço (Pivot, ABCD, OTR, CTE…)' },
  { id: 'SC', label: 'Scalping',         description: 'Execução rápida em movimentos curtos' },
  { id: 'RG', label: 'Regime',           description: 'Detectam o estado do mercado (Markov, Graph…)' },
  { id: 'IF', label: 'Information',      description: 'Usam dados externos (On-chain, DEX, eventos…)' },
  { id: 'NW', label: 'Network',          description: 'Relações entre ativos (Influencers & Followers)' },
]

const SYMBOLS = ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'BNB-USDT', 'XRP-USDT', 'DOGE-USDT', 'ADA-USDT', 'AVAX-USDT']

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

function resultKey(r, fallbackSymbol = '') {
  return `${r.strategy_id}:${r.symbol || fallbackSymbol}`
}

function buildBotPayload(r, symbol) {
  return {
    name: `${r.strategy_id} - ${r.strategy_name || 'Scanner BT'} (${symbol})`,
    strategy_id: r.strategy_id,
    symbol,
    timeframe: r.timeframe || '15m',
    demo: true,
    stake_usd: 100,
    leverage: 1,
    stop_loss_usd: -50,
    strategy_params: {},
  }
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
      const res = await fetch('/api/backtest/category', {
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
  const canSelect = !isNA && !disabled
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

  const { data: strategies = [] } = useQuery({ queryKey: ['strategies'], queryFn: getStrategies })
  const { data: bots = [] }       = useQuery({ queryKey: ['bots'],       queryFn: getBots       })

  const { data: autoScan, isLoading: autoScanLoading } = useQuery({
    queryKey: ['auto-scan-status'],
    queryFn: getAutoScanStatus,
    refetchInterval: 30_000,
  })
  const [autoScanFilters, setAutoScanFilters] = useState({ symbol: '', category: '', strategyId: '', dateFrom: '', dateTo: '' })
  const [autoScanFiltersApplied, setAutoScanFiltersApplied] = useState({})
  const hasAutoScanFilters = Object.values(autoScanFiltersApplied).some(Boolean)
  const { data: autoScanRuns = [] } = useQuery({
    queryKey: ['auto-scan-runs', autoScanFiltersApplied],
    queryFn: () => getAutoScanRuns({ limit: hasAutoScanFilters ? 100 : 5, ...autoScanFiltersApplied }),
    refetchInterval: 30_000,
  })
  const [autoScanToggling, setAutoScanToggling] = useState(false)

  async function handleAutoScanToggle() {
    if (!autoScan) return
    setAutoScanToggling(true)
    try {
      await toggleAutoScan(!autoScan.enabled)
      await qc.invalidateQueries({ queryKey: ['auto-scan-status'] })
    } catch (err) {
      setError(err.message)
    } finally {
      setAutoScanToggling(false)
    }
  }

  const usedSymbols = new Set(bots.map(b => b.symbol))
  const usedStrategies = Array.from(new Set(bots.map(b => b.strategy_id)))
  const selectedList = Object.values(selectedRows)

  const strategyCounts = strategies.reduce((acc, s) => {
    if (usedStrategies.includes(s.id)) return acc
    const prefix = (s.id || '').match(/^([A-Z]+)/i)?.[1]?.toUpperCase()
    if (prefix) acc[prefix] = (acc[prefix] || 0) + 1
    return acc
  }, {})

  function setCategoryProgress(catId, status, extra = {}) {
    setScanProgress(prev => {
      if (!prev) return prev
      const items = prev.items.map(item =>
        item.id === catId ? { ...item, status, ...extra } : item
      )
      const completed = items.filter(item => item.status === 'done' || item.status === 'failed').length
      const current = items.find(item => item.status === 'running')?.id || null
      return { ...prev, items, completed, current }
    })
  }

  const run = async () => {
    setLoading(true)
    setScanProgress(null)
    setResults(null)
    setSelectedRows({})
    setBulkMessage(null)
    setError(null)
    try {
      const categoriesToScan = category === 'ALL'
        ? CATEGORIES.map(c => c.id).filter(cat => (strategyCounts[cat] || 0) > 0)
        : [category]
      if (!categoriesToScan.length) {
        throw new Error('Nenhuma estratégia elegível para escanear.')
      }
      setScanProgress({
        total: categoriesToScan.length,
        completed: 0,
        current: null,
        items: categoriesToScan.map(cat => ({ id: cat, status: 'pending', resultCount: 0, error: '' })),
      })
      const merged = []
      const failedCategories = []

      for (const cat of categoriesToScan) {
        setCategoryProgress(cat, 'running', { error: '' })
        const scan = await requestCategoryScan(
          { category: cat, symbol, exclude_strategies: usedStrategies, strict: true },
          3,
        )
        if (!scan.ok) {
          const message = scan.message
          setCategoryProgress(cat, 'failed', { error: message })
          if (category === 'ALL') {
            failedCategories.push(`${cat}: ${message}`)
            continue
          }
          throw new Error(message)
        }
        const data = scan.data
        setCategoryProgress(cat, 'done', { resultCount: (data.results || []).length })
        merged.push(...(data.results || []).map(r => ({ ...r, category: cat, symbol })))
      }
      if (!merged.length && failedCategories.length) {
        throw new Error(`Nenhuma categoria retornou resultado. ${failedCategories.join(' | ')}`)
      }
      setResults({
        category,
        symbol,
        failedCategories,
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
            return { ...item, status: 'failed', error: item.error || e.message }
          }
          return item
        })
        const completed = items.filter(item => item.status === 'done' || item.status === 'failed').length
        return { ...prev, items, completed, current: null }
      })
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  function selectionBlockReason(row, rowSymbol) {
    if (usedSymbols.has(rowSymbol)) return 'Já existe um bot usando este ativo'
    if (usedStrategies.includes(row.strategy_id)) return 'Já existe um bot usando esta estratégia'
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
      if (!rowSymbol || rec.verdict === 'N/A') continue
      if (usedSymbols.has(rowSymbol) || usedStrategies.includes(row.strategy_id) || symbols.has(rowSymbol)) continue
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

      {/* Auto-Scan horário */}
      <div className="card p-5 mb-6">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3">
            <Zap size={20} className={autoScan?.enabled ? 'text-accent' : 'text-muted'} />
            <div>
              <p className="font-bold text-sm">Auto-Scan Horário</p>
              <p className="text-xs text-muted">
                Todas as categorias, um ativo habilitado por vez a cada hora cheia — cria e
                inicia o bot automaticamente se o score for ≥ {autoScan?.min_score ?? 9}.
                Sempre em modo demo.
              </p>
            </div>
          </div>
          <button
            onClick={handleAutoScanToggle}
            disabled={autoScanLoading || autoScanToggling}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 ${
              autoScan?.enabled
                ? 'bg-bear/15 text-bear hover:bg-bear/25'
                : 'bg-accent/15 text-accent hover:bg-accent/25'
            }`}
          >
            {autoScanToggling ? '...' : autoScan?.enabled ? 'Desativar' : 'Ativar'}
          </button>
        </div>

        {autoScan && (
          <div className="mt-3 pt-3 border-t border-white/5 flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted">
            <span className="flex items-center gap-1.5">
              <Clock size={12} />
              Próximo ciclo: {new Date(autoScan.next_run_at).toLocaleString()}
            </span>
            {autoScan.cursor_symbol && <span>Último ativo: {autoScan.cursor_symbol}</span>}
          </div>
        )}

        <div className="mt-3 pt-3 border-t border-white/5">
          <div className="flex flex-wrap items-end gap-2">
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-muted uppercase">Ativo</label>
              <input
                type="text" placeholder="ex: BTC-USDT" value={autoScanFilters.symbol}
                onChange={e => setAutoScanFilters(f => ({ ...f, symbol: e.target.value }))}
                className="bg-black/20 border border-white/10 rounded px-2 py-1 text-xs w-28"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-muted uppercase">Categoria</label>
              <select
                value={autoScanFilters.category}
                onChange={e => setAutoScanFilters(f => ({ ...f, category: e.target.value }))}
                className="bg-black/20 border border-white/10 rounded px-2 py-1 text-xs"
              >
                <option value="">Todas</option>
                {CATEGORIES.map(c => <option key={c.id} value={c.id}>{c.id}</option>)}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-muted uppercase">Estratégia</label>
              <input
                type="text" placeholder="ex: TF010" value={autoScanFilters.strategyId}
                onChange={e => setAutoScanFilters(f => ({ ...f, strategyId: e.target.value }))}
                className="bg-black/20 border border-white/10 rounded px-2 py-1 text-xs w-24"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-muted uppercase">De</label>
              <input
                type="date" value={autoScanFilters.dateFrom}
                onChange={e => setAutoScanFilters(f => ({ ...f, dateFrom: e.target.value }))}
                className="bg-black/20 border border-white/10 rounded px-2 py-1 text-xs"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-muted uppercase">Até</label>
              <input
                type="date" value={autoScanFilters.dateTo}
                onChange={e => setAutoScanFilters(f => ({ ...f, dateTo: e.target.value }))}
                className="bg-black/20 border border-white/10 rounded px-2 py-1 text-xs"
              />
            </div>
            <button
              onClick={() => setAutoScanFiltersApplied({ ...autoScanFilters })}
              className="px-3 py-1.5 rounded bg-accent/15 text-accent text-xs font-medium hover:bg-accent/25"
            >
              Buscar
            </button>
            {hasAutoScanFilters && (
              <button
                onClick={() => {
                  setAutoScanFilters({ symbol: '', category: '', strategyId: '', dateFrom: '', dateTo: '' })
                  setAutoScanFiltersApplied({})
                }}
                className="px-3 py-1.5 rounded bg-white/5 text-muted text-xs font-medium hover:bg-white/10"
              >
                Limpar
              </button>
            )}
          </div>
        </div>

        {autoScanRuns.length > 0 ? (
          <div className="mt-3 pt-3 border-t border-white/5 space-y-1.5 max-h-96 overflow-y-auto">
            {autoScanRuns.map(run => (
              <div key={run.id} className="text-xs flex items-start gap-2">
                <span className={`mt-1 w-1.5 h-1.5 rounded-full shrink-0 ${run.bot_created ? 'bg-bull' : 'bg-white/20'}`} />
                <div>
                  <span className="text-muted">{new Date(run.created_at).toLocaleString()}</span>
                  {run.symbol && <span className="text-muted"> · {run.symbol}</span>}
                  {run.category && <span className="text-muted"> · {run.category}</span>}
                  {run.best_score != null && <span className="text-muted"> · score {run.best_score.toFixed(2)}</span>}
                  <span className="text-white/80"> — {run.note || '—'}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-3 pt-3 border-t border-white/5 text-xs text-muted">
            {hasAutoScanFilters ? 'Nenhum ciclo encontrado para esses filtros.' : 'Nenhum ciclo registrado ainda.'}
          </p>
        )}
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
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2">
            <button
              onClick={() => setCategory('ALL')}
              title="Executa todas as categorias disponíveis para o ativo selecionado"
              className={`px-3 py-2 rounded-lg text-sm font-bold border transition-all ${
                category === 'ALL'
                  ? 'bg-accent/20 border-accent/50 text-accent'
                  : 'bg-white/5 border-white/10 text-muted hover:text-white hover:border-white/20'
              }`}
            >
              Todas
              <div className="text-[9px] font-normal opacity-70 truncate">Todas as categorias</div>
            </button>
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
            <p className="text-xs text-muted mt-2">
              {category === 'ALL'
                ? 'Executa todas as categorias disponíveis para o ativo selecionado.'
                : CATEGORIES.find(c => c.id === category)?.description}
            </p>
          )}
        </div>

        {/* Symbol */}
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[160px]">
            <label className="text-xs text-muted uppercase tracking-wider mb-2 block">Ativo</label>
            <div className="flex gap-2 flex-wrap">
              {SYMBOLS.map(s => {
                const inUse = usedSymbols.has(s)
                return (
                  <button
                    key={s}
                    onClick={() => !inUse && setSymbol(s)}
                    disabled={inUse}
                    title={inUse ? 'Já existe um bot usando este ativo' : undefined}
                    className={`px-2.5 py-1 rounded text-xs font-mono border transition-all ${
                      inUse
                        ? 'opacity-35 cursor-not-allowed bg-white/3 border-white/8 text-muted line-through'
                        : symbol === s
                        ? 'bg-accent/20 border-accent/50 text-accent'
                        : 'bg-white/5 border-white/10 text-muted hover:text-white'
                    }`}
                  >
                    {s}
                  </button>
                )
              })}
              <input
                value={SYMBOLS.includes(symbol) ? '' : symbol}
                onChange={e => setSymbol(e.target.value.toUpperCase())}
                placeholder="Outro (ex: LINK-USDT)"
                className="px-2.5 py-1 rounded text-xs font-mono bg-white/5 border border-white/10 text-white placeholder-muted focus:outline-none focus:border-accent/50 w-36"
              />
            </div>
          </div>

          <button
            onClick={run}
            disabled={loading || !symbol}
            className="btn flex items-center gap-2 bg-accent text-black font-bold hover:bg-accent/90 shadow-[0_0_20px_rgba(var(--accent-rgb),0.3)] disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading
              ? <div className="w-4 h-4 border-2 border-black border-t-transparent rounded-full animate-spin" />
              : <Play size={15} />
            }
            {loading ? 'Executando…' : category === 'ALL' ? 'Executar Todas as Categorias' : 'Executar Backtest da Categoria'}
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
            <p className="text-sm">Executando backtest para {category === 'ALL' ? 'todas as categorias' : `todas as estratégias ${category}`}…</p>
          </div>
          <p className="text-xs text-muted">Isso pode levar alguns segundos</p>

          {scanProgress && (
            <>
              <div>
                <div className="flex items-center justify-between text-xs text-muted mb-1.5">
                  <span>Progresso geral</span>
                  <span>{scanProgress.completed}/{scanProgress.total}</span>
                </div>
                <div className="h-2 rounded-full bg-white/10 overflow-hidden">
                  <div
                    className="h-2 bg-accent transition-all"
                    style={{ width: `${Math.round((scanProgress.completed / Math.max(1, scanProgress.total)) * 100)}%` }}
                  />
                </div>
              </div>

              <div className="space-y-2">
                {scanProgress.items.map(item => {
                  const meta = CATEGORIES.find(c => c.id === item.id)
                  const isRunning = item.status === 'running'
                  const isDone = item.status === 'done'
                  const isFailed = item.status === 'failed'
                  return (
                    <div key={item.id} className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
                      <div className="flex items-center justify-between gap-2 mb-1">
                        <div className="text-xs font-bold text-white">{item.id} {meta ? `- ${meta.label}` : ''}</div>
                        <div className={`text-[10px] font-bold ${isDone ? 'text-green-400' : isFailed ? 'text-red-400' : isRunning ? 'text-accent' : 'text-muted'}`}>
                          {isDone ? `Concluida (${item.resultCount})` : isFailed ? 'Falhou' : isRunning ? 'Executando' : 'Pendente'}
                        </div>
                      </div>
                      <div className="h-1.5 rounded-full bg-white/10 overflow-hidden">
                        <div
                          className={`h-1.5 transition-all ${isDone ? 'bg-green-400' : isFailed ? 'bg-red-400' : isRunning ? 'bg-accent animate-pulse' : 'bg-transparent'}`}
                          style={{ width: isDone || isFailed ? '100%' : isRunning ? '55%' : '0%' }}
                        />
                      </div>
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
          {results.failedCategories?.length > 0 && (
            <div className="mb-4 rounded-lg border border-yellow-500/30 bg-yellow-500/10 p-3 text-sm text-yellow-300">
              Algumas categorias falharam e foram ignoradas: {results.failedCategories.join(' | ')}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3 mb-4">
            <span className="text-sm text-muted">{results.total} estratégias · {results.symbol}</span>
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
