import React, { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Bot, DollarSign, ShieldAlert, Target, TrendingUp, RefreshCcw, Bell, BellOff, Send, AlertTriangle, X, BarChart3, LockKeyhole, Waves, Ban, CheckCircle2 } from 'lucide-react'
import { getBots, getTradesSummary, getBalance, getAccountSnapshot, resetAccount, getTelegramStatus, testTelegram, getOpsDiagnostics } from '../api'
import { useWebSocket } from '../hooks/useWebSocket'
import { useLanguage } from '../i18n/LanguageContext'
import StatCard from '../components/StatCard'
import BotCard  from '../components/BotCard'
import { getCriteriaCount } from '../components/StrategyChecklist'

const FIXED_STAKE_USD = 100

const RESET_WORD = 'RESETAR'

// ── Modal Reset Geral ──────────────────────────────────────────────────────────
function ResetModal({ onClose, onConfirm }) {
  const [loading, setLoading]   = useState(false)
  const [error,   setError]     = useState('')
  const [typed,   setTyped]     = useState('')
  const confirmed = typed.trim().toUpperCase() === RESET_WORD

  async function handleSubmit(e) {
    e.preventDefault()
    if (!confirmed) return
    setError('')
    setLoading(true)
    try {
      await onConfirm()
    } catch (err) {
      setError(err.message)
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-md bg-[#0f1117] border border-red-500/30 rounded-2xl shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-border">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-red-500/15 flex items-center justify-center">
              <AlertTriangle size={18} className="text-red-400" />
            </div>
            <div>
              <p className="font-bold text-white">Reset Geral</p>
              <p className="text-xs text-muted">Limpar dados da plataforma</p>
            </div>
          </div>
          <button onClick={onClose} className="text-muted hover:text-white p-1 rounded transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* Warning */}
        <div className="mx-5 mt-5 flex gap-3 p-3 rounded-xl bg-red-500/8 border border-red-500/25">
          <AlertTriangle size={16} className="text-red-400 shrink-0 mt-0.5" />
          <div className="text-xs text-red-300/80 space-y-1">
            <p className="font-semibold text-red-400">Esta ação é irreversível</p>
            <ul className="space-y-0.5 text-red-300/70 list-disc list-inside">
              <li>Todos os bots serão excluídos</li>
              <li>Histórico de trades, sinais e snapshots apagados</li>
              <li>Credenciais OKX preservadas</li>
              <li>Plataforma inicializada do zero</li>
            </ul>
          </div>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          <div>
            <label className="block text-xs text-muted mb-1.5">
              Digite <span className="font-bold text-red-400 font-mono">{RESET_WORD}</span> para confirmar:
            </label>
            <input
              autoFocus
              value={typed}
              onChange={e => setTyped(e.target.value)}
              placeholder={RESET_WORD}
              className={`w-full px-3 py-2 rounded-lg text-sm font-mono border bg-white/5 text-white placeholder-muted/40 focus:outline-none transition-colors ${
                typed.length === 0
                  ? 'border-white/10'
                  : confirmed
                  ? 'border-red-500/60 bg-red-500/5'
                  : 'border-white/20'
              }`}
            />
          </div>

          {error && (
            <div className="flex items-start gap-2 text-red-400 text-xs bg-red-500/10 border border-red-500/20 rounded-lg p-2.5">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              {error}
            </div>
          )}

          <div className="flex gap-3 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="btn btn-ghost flex-1"
              disabled={loading}
            >
              Cancelar
            </button>
            <button
              type="submit"
              disabled={loading || !confirmed}
              className={`btn flex-1 font-semibold transition-all ${
                loading
                  ? 'btn-ghost opacity-50 cursor-not-allowed'
                  : confirmed
                  ? 'bg-red-500 hover:bg-red-600 text-white border-red-500'
                  : 'bg-white/5 border-white/10 text-muted/40 cursor-not-allowed'
              }`}
            >
              {loading ? (
                <span className="flex items-center gap-2 justify-center">
                  <RefreshCcw size={13} className="animate-spin" /> Resetando...
                </span>
              ) : (
                '⚠ Confirmar Reset Geral'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

const DIAG_STYLE = {
  market: {
    icon: Waves,
    cls: 'border-blue-500/25 bg-blue-500/8 text-blue-300',
    dot: 'bg-blue-400',
  },
  blocked: {
    icon: LockKeyhole,
    cls: 'border-yellow-500/25 bg-yellow-500/8 text-yellow-300',
    dot: 'bg-yellow-400',
  },
  rejection: {
    icon: Ban,
    cls: 'border-red-500/25 bg-red-500/8 text-red-300',
    dot: 'bg-red-400',
  },
  operational: {
    icon: CheckCircle2,
    cls: 'border-green-500/25 bg-green-500/8 text-green-300',
    dot: 'bg-green-400',
  },
  no_data: {
    icon: BarChart3,
    cls: 'border-white/10 bg-white/5 text-muted',
    dot: 'bg-white/30',
  },
}

function OpsDiagnosticsPanel({ data, loading, days, setDays }) {
  const bots = data?.bots ?? []
  const totals = data?.totals ?? {}
  const priority = { rejection: 0, blocked: 1, no_data: 2, market: 3, operational: 4 }
  const visibleBots = [...bots]
    .sort((a, b) => (priority[a.verdict] ?? 9) - (priority[b.verdict] ?? 9) || b.signals.total - a.signals.total)
    .slice(0, 6)

  return (
    <div className="card space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <BarChart3 size={16} className="text-accent" />
          <h2 className="font-semibold text-sm">Diagnóstico operacional</h2>
          <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/20">
            {days}D
          </span>
        </div>
        <select
          value={days}
          onChange={e => setDays(Number(e.target.value))}
          className="input h-8 py-1 text-xs w-28"
        >
          <option value={1}>1 dia</option>
          <option value={3}>3 dias</option>
          <option value={7}>7 dias</option>
          <option value={14}>14 dias</option>
          <option value={30}>30 dias</option>
        </select>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <DiagMetric label="Sinais" value={totals.signals ?? 0} />
        <DiagMetric label="BUY/SELL" value={totals.executable_signals ?? 0} />
        <DiagMetric label="Trades" value={totals.trades ?? 0} />
        <DiagMetric label="Vinculados" value={totals.linked_signals ?? 0} />
        <DiagMetric label="Rejeições" value={totals.rejections ?? 0} tone={(totals.rejections ?? 0) > 0 ? 'bear' : 'white'} />
      </div>

      {loading ? (
        <div className="text-xs text-muted py-4">Carregando diagnóstico...</div>
      ) : visibleBots.length === 0 ? (
        <div className="text-xs text-muted py-4">Sem bots para diagnosticar.</div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
          {visibleBots.map(bot => {
            const style = DIAG_STYLE[bot.verdict] ?? DIAG_STYLE.no_data
            const Icon = style.icon
            const topHold = bot.top_hold_reasons?.[0]?.reason
            const topRejection = bot.rejections?.top_reasons?.[0]?.reason
            const reason = topRejection || topHold || bot.detail
            return (
              <div key={bot.bot_id} className={`rounded-lg border p-3 ${style.cls}`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 min-w-0">
                      <Icon size={14} className="shrink-0" />
                      <p className="text-sm font-semibold text-white truncate">{bot.name}</p>
                      <span className="text-[10px] font-mono text-white/45">{bot.strategy_id}</span>
                    </div>
                    <p className="text-[11px] text-white/45 mt-0.5">
                      {bot.symbol} · {bot.timeframe} · {bot.runtime_status}
                    </p>
                  </div>
                  <span className="text-[10px] font-bold uppercase shrink-0">{bot.verdict_label}</span>
                </div>
                <div className="grid grid-cols-4 gap-2 mt-3 text-[11px]">
                  <TinyStat label="HOLD" value={`${bot.signals.hold_rate.toFixed(0)}%`} />
                  <TinyStat label="BUY" value={bot.signals.buy} />
                  <TinyStat label="SELL" value={bot.signals.sell} />
                  <TinyStat label="Conv." value={`${bot.signals.trade_conversion_rate.toFixed(0)}%`} />
                </div>
                <p className="mt-3 text-[11px] text-white/70 line-clamp-2">{reason}</p>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function DiagMetric({ label, value, tone = 'white' }) {
  const color = tone === 'bear' ? 'text-bear' : tone === 'bull' ? 'text-bull' : 'text-white'
  return (
    <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-muted">{label}</p>
      <p className={`text-lg font-bold tabular-nums ${color}`}>{value}</p>
    </div>
  )
}

function TinyStat({ label, value }) {
  return (
    <div>
      <p className="text-white/35 uppercase tracking-wider">{label}</p>
      <p className="font-mono text-white/90">{value}</p>
    </div>
  )
}


export default function Dashboard() {
  const { t } = useLanguage()
  const qc = useQueryClient()
  const { data: bots    = [] } = useQuery({ queryKey: ['bots'],    queryFn: getBots })
  const { data: summary = {} } = useQuery({ queryKey: ['summary'], queryFn: () => getTradesSummary() })
  const { data: telegram = {} } = useQuery({ queryKey: ['telegram-status'], queryFn: getTelegramStatus, refetchInterval: 60_000 })
  const [liveStatuses, setLiveStatuses] = useState({})
  const [sortBy, setSortBy] = useState('criteria')
  const [diagDays, setDiagDays] = useState(7)
  const [resetting, setResetting] = useState(false)
  const [showResetModal, setShowResetModal] = useState(false)
  const [balanceMode, setBalanceMode] = useState('demo')
  const [tgTesting, setTgTesting] = useState(false)
  const [tgTestResult, setTgTestResult] = useState(null)

  const { data: balance } = useQuery({
    queryKey: ['balance', balanceMode],
    queryFn:  () => getBalance('', balanceMode === 'demo'),
    refetchInterval: 15_000,
  })

  const { data: snap } = useQuery({
    queryKey: ['account-snapshot', balanceMode],
    queryFn:  () => getAccountSnapshot(balanceMode === 'demo'),
    refetchInterval: 30_000,
  })

  const { data: opsDiagnostics, isLoading: opsDiagnosticsLoading } = useQuery({
    queryKey: ['ops-diagnostics', diagDays],
    queryFn: () => getOpsDiagnostics(diagDays),
    refetchInterval: 60_000,
  })

  const handleTelegramTest = async () => {
    setTgTesting(true)
    setTgTestResult(null)
    try {
      await testTelegram()
      setTgTestResult({ ok: true, msg: 'Mensagem de teste enviada! Verifique seu Telegram.' })
    } catch (e) {
      setTgTestResult({ ok: false, msg: e.message })
    } finally {
      setTgTesting(false)
      qc.invalidateQueries({ queryKey: ['telegram-status'] })
    }
  }

  const handleReset = () => setShowResetModal(true)
  const wsMsg = useWebSocket()

  useEffect(() => {
    if (!wsMsg) return
    if (wsMsg.type === 'price') {
      setLiveStatuses(prev => ({ ...prev, [wsMsg.bot_id]: { ...prev[wsMsg.bot_id], last_price: wsMsg.price } }))
    }
    if (wsMsg.type === 'indicators') {
      setLiveStatuses(prev => ({
        ...prev,
        [wsMsg.bot_id]: {
          ...prev[wsMsg.bot_id],
          last_indicators: wsMsg.indicators,
          hold_reason: wsMsg.hold_reason,
          order_criteria: wsMsg.order_criteria,
          execution_cycle: wsMsg.execution_cycle,
        },
      }))
    }
    if (wsMsg.type === 'trade') {
      setLiveStatuses(prev => ({
        ...prev,
        [wsMsg.bot_id]: { ...prev[wsMsg.bot_id], daily_pnl: (prev[wsMsg.bot_id]?.daily_pnl ?? 0) + (wsMsg.pnl ?? 0) },
      }))
    }
    if (wsMsg.type === 'order_error') {
      setLiveStatuses(prev => ({
        ...prev,
        [wsMsg.bot_id]: { ...prev[wsMsg.bot_id], last_order_error: wsMsg.error },
      }))
    }
  }, [wsMsg])

  const botsWithRuntime = bots.map(bot => ({
    ...bot,
    runtime: { ...(bot.runtime ?? {}), ...(liveStatuses[bot.id] ?? {}) },
  }))

  const runningBots = botsWithRuntime.filter(b => b.runtime.status === 'running' || b.active).length
  const openPositions = botsWithRuntime.filter(b => Math.abs(b.runtime.direction ?? 0) > 0)
  const openExposure = openPositions.reduce((sum, bot) => {
    const size = Math.abs(Number(bot.runtime.size ?? 0))
    const price = Number(bot.runtime.last_price ?? bot.runtime.entry_price ?? 0)
    const notional = size > 0 && price > 0 ? size * price : FIXED_STAKE_USD
    return sum + notional
  }, 0)
  const attentionBots = botsWithRuntime.filter(b =>
    b.runtime.status === 'error' ||
    b.runtime.status === 'maintenance' ||
    b.runtime.last_order_error ||
    b.runtime.halted
  ).length
  const sortedBots = [...botsWithRuntime].sort((a, b) => {
    if (sortBy === 'criteria') {
      return criteriaRate(b) - criteriaRate(a) || a.name.localeCompare(b.name)
    }
    if (sortBy === 'asset') {
      return a.symbol.localeCompare(b.symbol) || a.name.localeCompare(b.name)
    }
    if (sortBy === 'open') {
      const aOpen = Math.abs(a.runtime.direction ?? 0) > 0 ? 1 : 0
      const bOpen = Math.abs(b.runtime.direction ?? 0) > 0 ? 1 : 0
      return bOpen - aOpen || a.name.localeCompare(b.name)
    }
    return a.name.localeCompare(b.name)
  })
  const totalPnl    = summary.total_pnl    ?? 0
  const netPnl      = summary.net_pnl     ?? totalPnl      // fallback para bruto antes do 1º sync
  const totalFees   = summary.total_fees  ?? 0
  const winRate     = summary.win_rate_net ?? summary.win_rate ?? 0
  const totalTrades = summary.total_trades ?? 0



  const [isBalanceExpanded, setIsBalanceExpanded] = useState(false)

  // ── Reconciliação Exchange vs App ──────────────────────────────────────────
  const fmt = (n, decimals = 2) => n != null ? `$${Number(n).toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}` : '—'

  const exchPositions  = snap?.positions_count ?? null
  const exchExposure   = snap?.long_market_value ?? null
  const exchUnrealPnl  = snap?.unrealized_pl ?? null
  const exchEquity     = snap?.equity ?? null
  const exchCash       = snap?.cash ?? null

  const posMismatch      = exchPositions != null && exchPositions !== openPositions.length
  const exposureMismatch = exchExposure  != null && (
    (exchExposure === 0) !== (openExposure === 0)
  )
  // Não há "mismatch" entre exchUnrealPnl (não-realizado, posições abertas agora) e
  // totalPnl (realizado acumulado, trades já fechados) — são grandezas diferentes,
  // não duas fontes para o mesmo número. Não comparar como divergência.
  const valueColor = (n) => Number(n) > 0 ? 'bull' : Number(n) < 0 ? 'bear' : 'white'
  const textValueColor = (n, opacity = '') => {
    const suffix = opacity ? `/${opacity}` : ''
    return Number(n) > 0 ? `text-bull${suffix}` : Number(n) < 0 ? `text-bear${suffix}` : `text-white${suffix}`
  }
  const signedMoney = (n, decimals = 2) => {
    const value = Number(n ?? 0)
    return `${value > 0 ? '+' : ''}$${value.toFixed(decimals)}`
  }

  return (
    <div className="p-6 space-y-6">
      {/* ── Modal Reset Geral ──────────────────────────────────────────────── */}
      {showResetModal && (
        <ResetModal
          onClose={() => setShowResetModal(false)}
          onConfirm={async () => {
            setResetting(true)
            setShowResetModal(false)
            try {
              await resetAccount()
              window.location.reload()
            } catch (e) {
              alert('Erro no Reset Geral: ' + e.message)
              setResetting(false)
            }
          }}
        />
      )}

      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-xl font-bold">{t('dash.title')}</h1>
          <div className="flex items-center gap-2 mt-0.5">
            <p className="text-muted text-sm">{t('dash.subtitle')}</p>
          </div>
        </div>
        <button
          onClick={handleReset}
          disabled={resetting}
          className="flex items-center gap-2 px-3 py-1.5 bg-red-500/10 hover:bg-red-500/20 text-red-500 rounded-lg border border-red-500/20 transition-all text-xs font-medium"
          title="Para todos os bots e fecha posições na OKX"
        >
          <RefreshCcw className={`w-3.5 h-3.5 ${resetting ? 'animate-spin' : ''}`} />
          {resetting ? 'Resetando...' : 'Reset Geral'}
        </button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-7 gap-4">
        <StatCard
          label={
            <div className="flex items-center justify-between w-full pr-1">
              <span>{t('bd.balance')}</span>
              <div className="flex bg-white/5 p-0.5 rounded-md border border-white/10">
                <button
                  onClick={(e) => { e.stopPropagation(); setBalanceMode('live') }}
                  className={`px-1.5 py-0.5 rounded text-[9px] font-bold transition-all ${balanceMode === 'live' ? 'bg-bull text-white' : 'text-muted hover:text-white'}`}
                >
                  LIVE
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); setBalanceMode('demo') }}
                  className={`px-1.5 py-0.5 rounded text-[9px] font-bold transition-all ${balanceMode === 'demo' ? 'bg-accent text-white' : 'text-muted hover:text-white'}`}
                >
                  DEMO
                </button>
              </div>
            </div>
          }
          value={exchEquity != null ? fmt(exchEquity) : (balance?.available != null ? fmt(balance.available) : '—')}
          sub={
            <div className="flex flex-col gap-1 mt-1">
              {snap && (
                <div className="flex justify-between items-center text-[9px] mb-1 px-0.5 border-t border-white/5 pt-1">
                  <span className="text-white/30 font-bold uppercase tracking-wider">Cash</span>
                  <span className="font-mono text-white/45">{fmt(snap.cash)}</span>
                </div>
              )}
              <button
                onClick={() => setIsBalanceExpanded(!isBalanceExpanded)}
                className="text-[10px] text-accent hover:text-accent/80 flex items-center justify-between py-1 transition-all"
              >
                <span>{balanceMode === 'demo' ? 'Distribuição Demo (OKX)' : 'Distribuição Real (OKX)'}</span>
                <span className="text-[8px] opacity-60 bg-white/5 px-1 rounded border border-white/5">
                  {isBalanceExpanded ? 'RECOLHER ▲' : 'VER ATIVOS ▼'}
                </span>
              </button>
              
              {isBalanceExpanded && (
                <div className="space-y-1 animate-in fade-in slide-in-from-top-1 duration-200">
                  {balance?.assets?.map(a => {
                    const total = Number(a.total || 0)
                    return (
                      <div key={a.ccy} className="flex justify-between items-center bg-white/5 px-1.5 py-0.5 rounded">
                        <span className="text-[10px] font-bold text-accent">{a.ccy}</span>
                        <span className="text-[10px] font-mono text-white/90">
                          {total < 0.01 ? total.toFixed(8) : total.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                        </span>
                      </div>
                    )
                  })}
                  {(!balance?.assets || balance.assets.length === 0) && (
                    <span className="text-[9px] text-muted/50 italic">Nenhum ativo em carteira</span>
                  )}
                </div>
              )}
            </div>
          }
          color={valueColor(exchEquity ?? balance?.available ?? 0)}
          icon={DollarSign}
        />
        <StatCard
          label={t('dash.active_bots')}
          value={`${runningBots} / ${bots.length}`}
          sub={t('dash.stopped', { n: bots.length - runningBots })}
          color={valueColor(runningBots)}
          icon={Bot}
        />
        <StatCard
          label={t('dash.open_positions')}
          value={openPositions.length}
          color={valueColor(openPositions.length)}
          icon={Activity}
          rows={[
            {
              source: 'okx',
              label: 'OKX',
              value: exchPositions != null
                ? `${exchPositions} pos  ${exchPositions > 0 ? `· ${openPositions.filter(b => b.runtime.direction === 1).length}L ${openPositions.filter(b => b.runtime.direction === -1).length}S` : ''}`
                : '—',
              mismatch: posMismatch,
            },
            {
              source: 'app',
              label: 'App',
              value: `${openPositions.length} pos  · ${openPositions.filter(b => b.runtime.direction === 1).length}L ${openPositions.filter(b => b.runtime.direction === -1).length}S`,
              mismatch: posMismatch,
            },
          ]}
        />
        <StatCard
          label={t('dash.open_exposure')}
          value={exchExposure != null ? fmt(exchExposure, 0) : `$${openExposure.toFixed(0)}`}
          color={valueColor(exchExposure ?? openExposure)}
          icon={TrendingUp}
          rows={[
            {
              source: 'okx',
              label: 'OKX',
              value: exchExposure != null ? fmt(exchExposure, 0) : '—',
              mismatch: exposureMismatch,
            },
            {
              source: 'app',
              label: 'App',
              value: `$${openExposure.toFixed(0)} nocional`,
              mismatch: exposureMismatch,
            },
          ]}
        />
        <StatCard
          label={t('c.total_pnl')}
          value={signedMoney(netPnl)}
          color={valueColor(netPnl)}
          icon={DollarSign}
          rows={[
            {
              source: 'okx',
              label: 'OKX',
              value: exchUnrealPnl != null
                ? `${exchUnrealPnl > 0 ? '+' : ''}${fmt(exchUnrealPnl)} não-real. (aberto)`
                : '—',
              color: exchUnrealPnl != null
                ? textValueColor(exchUnrealPnl, '80')
                : undefined,
            },
            {
              source: 'app',
              label: 'App',
              value: totalFees > 0
                ? `${fmt(netPnl)} líq · -${fmt(totalFees, 4)} tx (realizado)`
                : `${totalPnl > 0 ? '+' : ''}${fmt(totalPnl)} · ${totalTrades}t (realizado)`,
              color: textValueColor(netPnl, '50'),
            },
          ]}
        />
        <StatCard
          label={t('c.win_rate')}
          value={`${winRate.toFixed(1)}%`}
          sub={`${summary.wins ?? 0}W · ${summary.losses ?? 0}L`}
          color={valueColor(winRate)}
          icon={Target}
        />
        <StatCard
          label={t('dash.attention')}
          value={attentionBots}
          sub={attentionBots ? t('dash.check_bots') : t('dash.all_clear')}
          color={valueColor(attentionBots)}
          icon={ShieldAlert}
        />
      </div>

      <OpsDiagnosticsPanel
        data={opsDiagnostics}
        loading={opsDiagnosticsLoading}
        days={diagDays}
        setDays={setDiagDays}
      />

      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="font-semibold">{t('nav.bots')}</h2>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-2 text-xs text-muted">
              <span>{t('dash.sort_by')}</span>
              <select
                className="input h-8 py-1 text-xs w-44"
                value={sortBy}
                onChange={e => setSortBy(e.target.value)}
              >
                <option value="name">{t('dash.sort_name')}</option>
                <option value="criteria">{t('dash.sort_criteria')}</option>
                <option value="asset">{t('dash.sort_asset')}</option>
                <option value="open">Operações Abertas</option>
              </select>
            </label>
            <a href="/bots/new" className="btn-primary btn text-xs">{t('dash.new_bot')}</a>
          </div>
        </div>

        {bots.length === 0 ? (
          <div className="card text-center text-muted py-12">
            <Bot size={32} className="mx-auto mb-3 opacity-30" />
            <p>{t('dash.no_bots')}</p>
            <a href="/bots/new" className="btn-primary btn text-sm mt-4 inline-block">
              {t('dash.create_first')}
            </a>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {sortedBots.map(bot => (
              <BotCard key={bot.id} bot={bot} liveStatus={liveStatuses[bot.id]} />
            ))}
          </div>
        )}
      </div>

      {/* Telegram Notifications */}
      <div className="card space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {telegram.enabled
              ? <Bell size={16} className="text-bull" />
              : <BellOff size={16} className="text-muted" />}
            <h2 className="font-semibold text-sm">Notificações Telegram</h2>
            {telegram.enabled
              ? <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-bull/15 text-bull">ATIVO</span>
              : <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-border text-muted">INATIVO</span>}
          </div>
          {telegram.enabled && (
            <button
              onClick={handleTelegramTest}
              disabled={tgTesting}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-accent/10 hover:bg-accent/20 text-accent border border-accent/20 transition-all"
            >
              <Send size={12} className={tgTesting ? 'animate-pulse' : ''} />
              {tgTesting ? t('dash.telegram_sending') : t('dash.telegram_send_balances')}
            </button>
          )}
        </div>

        {telegram.enabled ? (
          <div className="text-xs text-muted space-y-0.5">
            <p>Token: <span className="font-mono text-white/70">{telegram.token_preview}</span></p>
            <p>Chat ID: <span className="font-mono text-white/70">{telegram.chat_id_preview}</span></p>
            <p className="text-white/50 mt-1">Eventos notificados: início, entrada, TP1, stop atingido, saída, circuit breaker.</p>
          </div>
        ) : (
          <div className="text-xs text-muted space-y-1">
            <p>Configure no arquivo <span className="font-mono text-white/60">.env</span> do servidor:</p>
            <pre className="bg-black/30 rounded p-2 text-white/60 font-mono text-[11px] select-all">
{`TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=6753071411`}
            </pre>
            <p>Obtenha o token via <span className="text-accent">@BotFather</span> e o chat_id com <span className="text-accent">@userinfobot</span> no Telegram.</p>
          </div>
        )}

        {tgTestResult && (
          <p className={`text-xs font-medium ${tgTestResult.ok ? 'text-bull' : 'text-bear'}`}>
            {tgTestResult.ok ? '✓' : '✗'} {tgTestResult.msg}
          </p>
        )}
      </div>
    </div>
  )
}

function criteriaRate(bot) {
  const indicators = bot.runtime?.last_indicators
  if (!indicators || Object.keys(indicators).length === 0) return 0
  const criteria = getCriteriaCount(bot, indicators)
  if (!criteria || criteria.total === 0) return 0
  return criteria.ok / criteria.total
}
