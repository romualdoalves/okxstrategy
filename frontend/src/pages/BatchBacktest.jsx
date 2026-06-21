import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ScanSearch, Play, HelpCircle, ChevronDown, ChevronUp, PlusCircle, CheckSquare, Square } from 'lucide-react'
import BacktestLikert from '../components/BacktestLikert'
import { getStrategies, getBots, createBot } from '../api'

const API = import.meta.env.VITE_API_URL || ''

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

function MetricPill({ label, value, positive }) {
  const color = positive === true ? 'text-green-400' : positive === false ? 'text-red-400' : 'text-white'
  return (
    <div className="flex flex-col items-center px-3 py-1.5 rounded-lg bg-white/5 min-w-[72px]">
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

function ResultCard({ r, symbol, selected, disabled, disabledReason, onToggle }) {
  const [open, setOpen] = useState(false)
  const nav = useNavigate()
  const rec = r.recommendation
  const cfg = VERDICT_CFG[rec.verdict] || VERDICT_CFG['N/A']
  const hasMetrics = r.trades_count !== undefined
  const isNA = rec.verdict === 'N/A'
  const canSelect = !isNA && !disabled

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

      {/* Metrics row */}
      {hasMetrics && (
        <div className="flex flex-wrap gap-2 mt-2 mb-2">
          <MetricPill label="PnL" value={`${r.total_profit >= 0 ? '+' : ''}$${r.total_profit?.toFixed(2)}`} positive={r.total_profit > 0} />
          <MetricPill label="Trades" value={r.trades_count} />
          <MetricPill label="Win Rate" value={`${r.win_rate?.toFixed(1)}%`} positive={r.win_rate >= 50} />
          <MetricPill label="Prof. Factor" value={r.profit_factor?.toFixed(2)} positive={r.profit_factor >= 1.2} />
          <MetricPill label="Drawdown" value={`$${r.max_drawdown?.toFixed(2)}`} positive={false} />
          <MetricPill label="Saldo Final" value={`$${r.final_balance?.toFixed(2)}`} />
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
        <ul className="mt-2 space-y-1">
          {rec.reasons.map((reason, i) => (
            <li key={i} className="text-xs text-white/70 flex gap-2">
              <span className={`shrink-0 ${cfg.color}`}>·</span>
              <span>{reason}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function BatchBacktest() {
  const nav = useNavigate()
  const qc = useQueryClient()
  const [category, setCategory] = useState('TF')
  const [symbol, setSymbol]     = useState('')
  const [loading, setLoading]   = useState(false)
  const [results, setResults]   = useState(null)
  const [error, setError]       = useState(null)
  const [selectedRows, setSelectedRows] = useState({})
  const [bulkCreating, setBulkCreating] = useState(false)
  const [bulkMessage, setBulkMessage] = useState(null)

  const [autoScanLoading, setAutoScanLoading] = useState(false)
  const [autoScanProgress, setAutoScanProgress] = useState({ current: 0, total: 0 })
  const [categoryProgress, setCategoryProgress] = useState({})
  const [autoScanResults, setAutoScanResults] = useState(null)

  const { data: strategies = [] } = useQuery({ queryKey: ['strategies'], queryFn: getStrategies })
  const { data: bots = [] }       = useQuery({ queryKey: ['bots'],       queryFn: getBots       })
  const { data: scanHistory = [], refetch: refetchHistory } = useQuery({ 
    queryKey: ['autoScanHistory'], 
    queryFn: async () => {
      const res = await fetch(`${API}/api/backtest/auto-scan-history`)
      if (!res.ok) return []
      return await res.json()
    }
  })

  const deleteHistory = async (id) => {
    await fetch(`${API}/api/backtest/auto-scan-history/${id}`, { method: 'DELETE' })
    refetchHistory()
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

  const runAutoScan = async () => {
    setAutoScanLoading(true)
    setAutoScanResults(null)
    setSelectedRows({})
    setBulkMessage(null)
    setError(null)

    const combinations = []
    const initialProgress = {}

    for (const cat of CATEGORIES) {
      if (!strategyCounts[cat.id]) continue;
      
      let catTotal = 0
      for (const sym of SYMBOLS) {
        if (usedSymbols.has(sym)) continue;
        combinations.push({ cat: cat.id, sym })
        catTotal++
      }

      if (catTotal > 0) {
        initialProgress[cat.id] = {
          current: 0,
          total: catTotal,
          bestScore: 0,
          bestSymbol: '',
          bestStrategy: '',
          counts: { 'INICIAR': 0, 'CUIDADO': 0, 'NÃO INICIAR': 0 }
        }
      }
    }

    setCategoryProgress(initialProgress)
    setAutoScanProgress({ current: 0, total: combinations.length })

    let allResults = []
    let currentTotal = 0
    let trackingProgress = { ...initialProgress }

    for (const { cat, sym } of combinations) {
      let maxCatScore = trackingProgress[cat].bestScore || 0;
      let bestSym = trackingProgress[cat].bestSymbol || '';
      let bestStrat = trackingProgress[cat].bestStrategy || '';
      let catCounts = { ...(trackingProgress[cat].counts || { 'INICIAR': 0, 'CUIDADO': 0, 'NÃO INICIAR': 0 }) };

      try {
        const res = await fetch(`${API}/api/backtest/category`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ category: cat, symbol: sym, exclude_strategies: usedStrategies }),
        })
        if (res.ok) {
          const data = await res.json()
          
          for (const r of data.results || []) {
             const score = r.recommendation?.score || 0
             const verdict = r.recommendation?.verdict

             if (verdict && catCounts[verdict] !== undefined) {
                 catCounts[verdict] += 1
             }

             if (score >= 7.0 && verdict === 'INICIAR') {
                 allResults.push({ ...r, symbol: sym, category: cat })
             }
             if (score > maxCatScore) {
                 maxCatScore = score
                 bestSym = sym
                 bestStrat = r.strategy_id
             }
          }
        }

        trackingProgress[cat] = {
          ...trackingProgress[cat],
          current: trackingProgress[cat].current + 1,
          bestScore: maxCatScore,
          bestSymbol: bestSym,
          bestStrategy: bestStrat,
          counts: catCounts
        }
        
        setCategoryProgress({ ...trackingProgress })
      } catch (err) {
        console.error(err)
        trackingProgress[cat] = {
            ...trackingProgress[cat],
            current: trackingProgress[cat].current + 1
        }
        setCategoryProgress({ ...trackingProgress })
      }
      currentTotal++
      setAutoScanProgress({ current: currentTotal, total: combinations.length })

      // Small delay to prevent rate limiting from OKX
      await new Promise(resolve => setTimeout(resolve, 150))
    }

    allResults.sort((a, b) => (b.recommendation?.score || 0) - (a.recommendation?.score || 0))

    const finalRanking = []
    const seenStrats = new Set()
    const seenSyms = new Set()

    for (const r of allResults) {
       if (!seenStrats.has(r.strategy_id) && !seenSyms.has(r.symbol)) {
           finalRanking.push(r)
           seenStrats.add(r.strategy_id)
           seenSyms.add(r.symbol)
       }
    }

    setAutoScanResults(finalRanking)
    setAutoScanLoading(false)

    if (finalRanking.length > 0) {
        try {
            await fetch(`${API}/api/backtest/auto-scan-history`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ results: finalRanking })
            })
            refetchHistory()
        } catch(e) {
            console.error("Falha ao salvar histórico", e)
        }
    }
  }

  const run = async () => {
    setLoading(true)
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
      const merged = []

      for (const cat of categoriesToScan) {
        const res = await fetch(`${API}/api/backtest/category`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ category: cat, symbol, exclude_strategies: usedStrategies }),
        })
        if (!res.ok) {
          const err = await res.json().catch(() => ({}))
          throw new Error(err.detail || `HTTP ${res.status}`)
        }
        const data = await res.json()
        merged.push(...(data.results || []).map(r => ({ ...r, category: cat, symbol })))
      }
      setResults({
        category,
        symbol,
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

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <ScanSearch size={24} className="text-accent" />
        <div>
          <h1 className="text-2xl font-bold">Scanner de Backtest</h1>
          <p className="text-sm text-muted">Testa todas as estratégias de uma categoria em um ativo e ordena do melhor ao pior</p>
        </div>
      </div>

      {/* Auto-Scan Section */}
      <div className="card p-5 mb-6 space-y-4 border-accent/30">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold text-accent">Ranking Auto-Scan (INICIAR)</h2>
            <p className="text-sm text-muted">Testa todas as categorias e ativos disponíveis e ranqueia as melhores combinações</p>
          </div>
          <button
            onClick={runAutoScan}
            disabled={autoScanLoading}
            className="btn flex items-center gap-2 bg-accent text-black font-bold hover:bg-accent/90 shadow-[0_0_20px_rgba(var(--accent-rgb),0.3)] disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {autoScanLoading
              ? <div className="w-4 h-4 border-2 border-black border-t-transparent rounded-full animate-spin" />
              : <Play size={15} />
            }
            {autoScanLoading ? 'Escaneando…' : 'Auto-Scan'}
          </button>
        </div>

        {(autoScanLoading || (autoScanResults && Object.keys(categoryProgress).length > 0)) && (
          <div className="flex flex-col py-4 gap-6 bg-white/[0.02] p-4 rounded-xl border border-white/5">
            {autoScanLoading && (
              <div className="flex flex-col items-center justify-center gap-2 text-muted">
                <div className="flex items-center gap-3">
                  <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
                  <span className="text-sm font-bold text-white">Analisando estratégias e ativos…</span>
                </div>
                <p className="text-xs font-mono bg-white/5 px-3 py-1 rounded-full text-accent mt-1">
                  Progresso Global: {autoScanProgress.current} / {autoScanProgress.total}
                </p>
              </div>
            )}
            
            {/* Barras de progresso por categoria */}
            <div className="w-full grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4">
              {Object.entries(categoryProgress).map(([catId, prog]) => {
                const catInfo = CATEGORIES.find(c => c.id === catId)
                const pct = prog.total > 0 ? (prog.current / prog.total) * 100 : 0
                return (
                  <div key={catId} className={`flex flex-col gap-1.5 transition-opacity duration-500 ${!autoScanLoading && pct === 100 ? 'opacity-70' : 'opacity-100'}`}>
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-bold text-white">
                        {catId} <span className="text-muted font-normal ml-1">({catInfo?.label})</span>
                      </span>
                      <span className="text-muted flex items-center gap-2">
                        {prog.counts && (
                          <div className="flex items-center gap-1.5 mr-1">
                            {prog.counts['INICIAR'] > 0 && (
                              <span className="text-[10px] font-bold text-green-400 bg-green-500/10 border border-green-500/20 px-1.5 py-0.5 rounded shadow-sm">
                                {prog.counts['INICIAR']} Iniciar
                              </span>
                            )}
                            {prog.counts['CUIDADO'] > 0 && (
                              <span className="text-[10px] font-bold text-yellow-400 bg-yellow-500/10 border border-yellow-500/20 px-1.5 py-0.5 rounded shadow-sm">
                                {prog.counts['CUIDADO']} Cuidado
                              </span>
                            )}
                            {prog.counts['NÃO INICIAR'] > 0 && (
                              <span className="text-[10px] font-bold text-red-400 bg-red-500/10 border border-red-500/20 px-1.5 py-0.5 rounded shadow-sm">
                                {prog.counts['NÃO INICIAR']} Não Iniciar
                              </span>
                            )}
                          </div>
                        )}
                        {prog.bestScore > 0 && (
                          <span className="text-accent font-bold px-1.5 py-0.5 bg-accent/10 border border-accent/20 rounded shadow-[0_0_8px_rgba(var(--accent-rgb),0.15)] flex items-center gap-1">
                            {prog.bestScore.toFixed(1)} <span className="font-mono text-[9px] opacity-80">({prog.bestStrategy}/{prog.bestSymbol.split('-')[0]})</span>
                          </span>
                        )}
                        <span className="font-mono w-9 text-right bg-white/5 px-1.5 py-0.5 rounded">{prog.current}/{prog.total}</span>
                      </span>
                    </div>
                    <div className="h-1.5 w-full bg-white/5 rounded-full overflow-hidden shadow-inner">
                      <div 
                        className="h-full bg-accent transition-all duration-300 relative"
                        style={{ width: `${pct}%` }}
                      >
                        {autoScanLoading && pct < 100 && (
                          <div className="absolute inset-0 bg-white/20 animate-pulse" />
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {autoScanResults && !autoScanLoading && (
          autoScanResults.length === 0 ? (
            <div className="text-center py-8 text-muted border border-white/5 rounded-lg bg-white/5">
              Nenhuma configuração obteve veredito <strong>INICIAR</strong> (Score ≥ 7.0)
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-xs text-muted">
                  <button
                    onClick={() => selectEligible(autoScanResults, '')}
                    className="px-2.5 py-1 rounded border border-white/10 bg-white/5 hover:text-white"
                  >
                    Marcar elegíveis
                  </button>
                  <button
                    onClick={() => setSelectedRows({})}
                    className="px-2.5 py-1 rounded border border-white/10 bg-white/5 hover:text-white"
                  >
                    Limpar seleção
                  </button>
                  <span>{selectedList.length} selecionada(s)</span>
                </div>
                <button
                  onClick={createSelectedBots}
                  disabled={!selectedList.length || bulkCreating}
                  className="btn flex items-center gap-2 bg-accent text-black font-bold hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {bulkCreating ? <div className="w-4 h-4 border-2 border-black border-t-transparent rounded-full animate-spin" /> : <PlusCircle size={15} />}
                  {bulkCreating ? 'Criando…' : `Criar ${selectedList.length || ''} bot(s)`}
                </button>
              </div>
              <div className="overflow-x-auto">
              <table className="w-full text-left text-sm whitespace-nowrap">
                <thead>
                  <tr className="text-muted border-b border-white/10 text-xs uppercase tracking-wider">
                    <th className="py-3 px-4">Sel.</th>
                    <th className="py-3 px-4">#</th>
                    <th className="py-3 px-4">Estratégia</th>
                    <th className="py-3 px-4">Ativo</th>
                    <th className="py-3 px-4 text-right">Score</th>
                    <th className="py-3 px-4 text-right">WR</th>
                    <th className="py-3 px-4 text-right">PF</th>
                    <th className="py-3 px-4 text-right">DD</th>
                    <th className="py-3 px-4 text-right">P&L</th>
                    <th className="py-3 px-4 text-center">Ação</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {autoScanResults.map((r, i) => {
                    const key = resultKey(r, r.symbol)
                    const disabledReason = selectionBlockReason(r, r.symbol)
                    const isSelected = Boolean(selectedRows[key])
                    return (
                    <tr key={`${r.strategy_id}-${r.symbol}`} className="hover:bg-white/5 transition-colors">
                      <td className="py-3 px-4">
                        <button
                          onClick={() => toggleSelection(r, r.symbol)}
                          disabled={!isSelected && Boolean(disabledReason)}
                          title={disabledReason}
                          className={`inline-flex ${isSelected ? 'text-green-400' : disabledReason ? 'text-muted/40 cursor-not-allowed' : 'text-muted hover:text-white'}`}
                        >
                          {isSelected ? <CheckSquare size={16} /> : <Square size={16} />}
                        </button>
                      </td>
                      <td className="py-3 px-4 font-mono text-muted">{i + 1}</td>
                      <td className="py-3 px-4 font-bold text-white">{r.strategy_id}</td>
                      <td className="py-3 px-4 font-mono text-white">{r.symbol}</td>
                      <td className="py-3 px-4 text-right font-mono text-green-400 font-bold">{r.recommendation?.score?.toFixed(1)}</td>
                      <td className="py-3 px-4 text-right font-mono">{r.win_rate?.toFixed(1)}%</td>
                      <td className="py-3 px-4 text-right font-mono">{r.profit_factor?.toFixed(2)}</td>
                      <td className="py-3 px-4 text-right font-mono text-red-400">${r.max_drawdown?.toFixed(2)}</td>
                      <td className={`py-3 px-4 text-right font-mono font-bold ${r.total_profit > 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {r.total_profit > 0 ? '+' : ''}${r.total_profit?.toFixed(2)}
                      </td>
                      <td className="py-3 px-4 text-center">
                        <button
                          onClick={() => nav(`/bots/new?strategy=${r.strategy_id}&symbol=${encodeURIComponent(r.symbol)}`)}
                          className="inline-flex items-center gap-1 px-3 py-1 rounded-lg text-xs font-bold bg-accent/15 text-accent border border-accent/30 hover:bg-accent/25 transition-colors"
                        >
                          <PlusCircle size={13} />
                          Criar Bot
                        </button>
                      </td>
                    </tr>
                    )
                  })}
                </tbody>
              </table>
              </div>
            </div>
          )
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

      {/* Auto-Scan History Section */}
      {scanHistory.length > 0 && (
        <div className="card p-5 mb-6 space-y-4 border-white/10">
          <h2 className="text-xl font-bold text-white">Históricos Salvos</h2>
          <div className="space-y-3">
            {scanHistory.map(hist => (
              <details key={hist.id} className="bg-white/5 border border-white/10 rounded-lg group">
                <summary className="p-4 cursor-pointer flex justify-between items-center hover:bg-white/5 transition-colors">
                  <div className="flex items-center gap-3">
                    <span className="font-bold text-accent">{new Date(hist.created_at).toLocaleString()}</span>
                    <span className="text-xs text-muted font-mono">{hist.results.length} Recomendações INICIAR</span>
                  </div>
                  <button 
                    onClick={(e) => { e.preventDefault(); deleteHistory(hist.id); }}
                    className="text-xs text-red-400 hover:text-red-300 px-2 py-1 rounded border border-red-500/20 bg-red-500/10"
                  >
                    Excluir
                  </button>
                </summary>
                <div className="p-4 pt-0 border-t border-white/10">
                  <div className="overflow-x-auto mt-3">
                    <table className="w-full text-left text-sm whitespace-nowrap">
                      <thead>
                        <tr className="text-muted border-b border-white/10 text-xs uppercase tracking-wider">
                          <th className="py-2 px-4">#</th>
                          <th className="py-2 px-4">Estratégia</th>
                          <th className="py-2 px-4">Ativo</th>
                          <th className="py-2 px-4 text-right">Score</th>
                          <th className="py-2 px-4 text-center">Ação</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-white/5">
                        {hist.results.map((r, i) => (
                          <tr key={`${r.strategy_id}-${r.symbol}-${i}`} className="hover:bg-white/5 transition-colors">
                            <td className="py-2 px-4 font-mono text-muted">{i + 1}</td>
                            <td className="py-2 px-4 font-bold text-white">{r.strategy_id}</td>
                            <td className="py-2 px-4 font-mono text-white">{r.symbol}</td>
                            <td className="py-2 px-4 text-right font-mono text-green-400 font-bold">{r.recommendation?.score?.toFixed(1)}</td>
                            <td className="py-2 px-4 text-center">
                              <button
                                onClick={() => nav(`/bots/new?strategy=${r.strategy_id}&symbol=${encodeURIComponent(r.symbol)}`)}
                                className="inline-flex items-center gap-1 px-3 py-1 rounded-lg text-xs font-bold bg-accent/15 text-accent border border-accent/30 hover:bg-accent/25 transition-colors"
                              >
                                <PlusCircle size={13} />
                                Criar Bot
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </details>
            ))}
          </div>
        </div>
      )}

      {/* Controls */}
      <div className="card p-5 mb-6 space-y-4">
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
        <div className="flex flex-col items-center justify-center py-16 gap-3 text-muted">
          <div className="w-8 h-8 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm">Executando backtest para {category === 'ALL' ? 'todas as categorias' : `todas as estratégias ${category}`}…</p>
          <p className="text-xs">Isso pode levar alguns segundos</p>
        </div>
      )}

      {/* Summary badges */}
      {results && counts && (
        <>
          <div className="flex flex-wrap items-center gap-3 mb-4">
            <span className="text-sm text-muted">{results.total} estratégias · {results.symbol}</span>
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
