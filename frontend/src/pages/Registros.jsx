import React, { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  DollarSign, Target, TrendingUp, TrendingDown, AlertTriangle,
  CheckCircle, Activity, RefreshCcw, ChevronDown, ChevronRight, History,
} from 'lucide-react'
import { getTrades, getTradesSummary, getBots, syncTradeFees, getActivities } from '../api'
import { useLanguage } from '../i18n/LanguageContext'
import TradeTable from '../components/TradeTable'
import StatCard   from '../components/StatCard'

// ── Helpers compartilhados ────────────────────────────────────────────────────

function fmt(val, decimals = 2) {
  if (val === null || val === undefined) return '—'
  return Number(val).toLocaleString('pt-BR', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

function PnlChip({ value }) {
  if (value === null || value === undefined) return <span className="text-muted">—</span>
  const pos = value >= 0
  return (
    <span className={`font-semibold tabular-nums ${pos ? 'text-bull' : 'text-bear'}`}>
      {pos ? '+' : ''}${fmt(value)}
    </span>
  )
}

function SideBadge({ side }) {
  if (!side) return null
  const buy = side.toLowerCase() === 'buy'
  return (
    <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded
      ${buy ? 'bg-bull/15 text-bull' : 'bg-bear/15 text-bear'}`}>
      {buy ? 'BUY' : 'SELL'}
    </span>
  )
}

function BotTag({ bot }) {
  return (
    <span className="text-[10px] bg-accent/10 text-accent px-1.5 py-0.5 rounded font-medium">
      {bot.name}
    </span>
  )
}

// ── Aba Histórico ─────────────────────────────────────────────────────────────

function TabHistorico() {
  const { t } = useLanguage()
  const qc = useQueryClient()
  const [botFilter, setBotFilter] = useState('')
  const [syncing, setSyncing]     = useState(false)

  const { data: bots   = [] } = useQuery({ queryKey: ['bots'], queryFn: getBots })
  const { data: trades = [] } = useQuery({
    queryKey: ['trades', botFilter],
    queryFn:  () => getTrades(botFilter || undefined),
  })
  const { data: summary = {} } = useQuery({
    queryKey: ['summary', botFilter],
    queryFn:  () => getTradesSummary(botFilter || undefined),
  })

  const handleSyncFees = async () => {
    setSyncing(true)
    try {
      await syncTradeFees(90)
      await qc.invalidateQueries({ queryKey: ['summary'] })
      await qc.invalidateQueries({ queryKey: ['trades'] })
    } catch (e) {
      alert('Falha ao sincronizar taxas: ' + e.message)
    } finally {
      setSyncing(false)
    }
  }

  const gross   = summary.total_pnl  ?? 0
  const fees    = summary.total_fees ?? 0
  const net     = summary.net_pnl    ?? gross
  const hasFees = (summary.fees_synced ?? 0) > 0
  const pending = summary.fees_pending ?? 0

  return (
    <div className="space-y-5">
      {/* Filtro + Sync */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <label className="text-sm text-muted">{t('trades.filter_bot')}</label>
          <select className="input max-w-xs" value={botFilter} onChange={e => setBotFilter(e.target.value)}>
            <option value="">{t('c.all')}</option>
            {bots.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
          {pending > 0 && (
            <span className="text-[10px] text-yellow-400/80 bg-yellow-400/10 border border-yellow-400/20 px-2 py-0.5 rounded-full">
              {pending} trade{pending > 1 ? 's' : ''} sem taxa registrada
            </span>
          )}
        </div>
        <button
          onClick={handleSyncFees}
          disabled={syncing}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-accent/10 hover:bg-accent/20 text-accent border border-accent/20 transition-all disabled:opacity-50"
        >
          <RefreshCcw className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : ''}`} />
          {syncing ? 'Sincronizando taxas...' : 'Sincronizar Corretagem'}
        </button>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          label="P&L Líquido (após taxas)"
          value={`${net >= 0 ? '+' : ''}$${net.toFixed(2)}`}
          color={net >= 0 ? 'bull' : 'bear'}
          icon={DollarSign}
          rows={hasFees ? [
            { source: 'okx', label: 'Bruto', value: `${gross >= 0 ? '+' : ''}$${gross.toFixed(2)}`, color: gross >= 0 ? 'text-white/60' : 'text-bear/60' },
            { source: 'app', label: 'Taxas', value: fees > 0 ? `-$${fees.toFixed(4)}` : '$0', color: 'text-bear/70' },
          ] : undefined}
          sub={!hasFees ? `${gross >= 0 ? '+' : ''}$${gross.toFixed(2)} (taxas pendentes)` : undefined}
        />
        <StatCard
          label={t('c.win_rate')}
          value={`${(summary.win_rate_net ?? summary.win_rate ?? 0).toFixed(1)}%`}
          sub={`${summary.wins ?? 0}W · ${summary.losses ?? 0}L`}
          color={(summary.win_rate_net ?? summary.win_rate ?? 0) >= 50 ? 'bull' : 'bear'}
          icon={Target}
        />
        <StatCard
          label={t('trades.best')}
          value={summary.best_trade !== undefined ? `${summary.best_trade >= 0 ? '+' : ''}$${summary.best_trade.toFixed(2)}` : '—'}
          color={summary.best_trade >= 0 ? 'bull' : 'bear'}
          icon={TrendingUp}
        />
        <StatCard
          label={t('trades.worst')}
          value={summary.worst_trade !== undefined ? `${summary.worst_trade >= 0 ? '+' : ''}$${summary.worst_trade.toFixed(2)}` : '—'}
          color={summary.worst_trade >= 0 ? 'bull' : 'bear'}
          icon={TrendingDown}
        />
      </div>

      {hasFees && (
        <div className="flex items-center gap-4 px-3 py-2 bg-bear/5 border border-bear/10 rounded-lg text-[11px]">
          <span className="text-muted">Corretagem total:</span>
          <span className="text-bear font-mono font-semibold">-${fees.toFixed(4)}</span>
          <span className="text-muted">·</span>
          <span className="text-muted">Média/trade:</span>
          <span className="text-bear/70 font-mono">${(summary.avg_fee ?? 0).toFixed(4)}</span>
          <span className="text-muted">·</span>
          <span className="text-muted">{summary.fees_synced} de {summary.total_trades} trades com taxa registrada</span>
        </div>
      )}

      <div className="card">
        <TradeTable trades={trades} />
      </div>
    </div>
  )
}

// ── Aba Atividades OKX ────────────────────────────────────────────────────────

function FillsTable({ fills, locale }) {
  const [expanded, setExpanded] = useState(null)

  if (!fills.length) return (
    <div className="text-center text-muted py-10 text-sm">Nenhum fill registrado nesta data.</div>
  )

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-muted text-[11px] uppercase tracking-wide">
            <th className="text-left py-2 px-3 font-medium w-4" />
            <th className="text-left py-2 px-3 font-medium">Hora</th>
            <th className="text-left py-2 px-3 font-medium">Lado</th>
            <th className="text-left py-2 px-3 font-medium">Símbolo</th>
            <th className="text-right py-2 px-3 font-medium">Qtd</th>
            <th className="text-right py-2 px-3 font-medium">Preço</th>
            <th className="text-right py-2 px-3 font-medium">P&L Bruto</th>
            <th className="text-right py-2 px-3 font-medium">Fee</th>
            <th className="text-right py-2 px-3 font-medium">P&L Líquido</th>
            <th className="text-left py-2 px-3 font-medium">Bot Correlato</th>
            <th className="text-left py-2 px-3 font-medium">Trade App</th>
          </tr>
        </thead>
        <tbody>
          {fills.map((f, i) => {
            const ts = f.transaction_time
              ? new Date(f.transaction_time).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
              : '—'
            const isOpen  = expanded === i
            const hasMeta = f.matched_db_trade || (f.matched_bots && f.matched_bots.length > 0)

            return (
              <React.Fragment key={f.id || i}>
                <tr
                  className={`border-b border-border/40 hover:bg-panel/40 transition-colors ${hasMeta ? 'cursor-pointer' : ''}`}
                  onClick={() => hasMeta && setExpanded(isOpen ? null : i)}
                >
                  <td className="py-2.5 px-3 text-muted">
                    {hasMeta ? (isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />) : null}
                  </td>
                  <td className="py-2.5 px-3 text-muted text-xs font-mono whitespace-nowrap">{ts}</td>
                  <td className="py-2.5 px-3"><SideBadge side={f.side} /></td>
                  <td className="py-2.5 px-3 font-mono text-xs font-semibold">{f.symbol ?? '—'}</td>
                  <td className="py-2.5 px-3 text-right text-xs tabular-nums">{fmt(f.qty, 6)}</td>
                  <td className="py-2.5 px-3 text-right font-mono text-xs tabular-nums">${fmt(f.price, 4)}</td>
                  <td className="py-2.5 px-3 text-right">
                    {f.side === 'sell' ? <PnlChip value={f.fill_pnl} /> : <span className="text-muted text-xs">—</span>}
                  </td>
                  <td className="py-2.5 px-3 text-right">
                    {f.fee_usd > 0
                      ? <span className="text-bear text-xs tabular-nums">-${fmt(f.fee_usd)}</span>
                      : <span className="text-muted text-xs">—</span>}
                  </td>
                  <td className="py-2.5 px-3 text-right">
                    {f.side === 'sell' ? <PnlChip value={f.net_pnl} /> : <span className="text-muted text-xs">—</span>}
                  </td>
                  <td className="py-2.5 px-3">
                    <div className="flex flex-wrap gap-1">
                      {(f.matched_bots || []).map(b => <BotTag key={b.id} bot={b} />)}
                    </div>
                  </td>
                  <td className="py-2.5 px-3 text-xs">
                    {f.matched_db_trade
                      ? <span className="text-muted">#{f.matched_db_trade.id} · <PnlChip value={f.matched_db_trade.pnl} /></span>
                      : <span className="text-muted/40">—</span>}
                  </td>
                </tr>
                {isOpen && hasMeta && (
                  <tr className="bg-panel/20 border-b border-border/20">
                    <td colSpan={11} className="px-6 py-3 text-xs text-muted space-y-1">
                      <div><span className="font-medium text-white">Order ID:</span> {f.order_id ?? '—'}</div>
                      <div><span className="font-medium text-white">Status:</span> {f.order_status ?? '—'}</div>
                      {f.fee_usd > 0 && (
                        <div>
                          <span className="font-medium text-white">Fee (CFEE):</span>{' '}
                          <span className="text-bear">-${fmt(f.fee_usd)}</span>
                          {f.fill_pnl != null && (
                            <span className="text-muted ml-2">→ P&L líquido: <PnlChip value={f.net_pnl} /></span>
                          )}
                        </div>
                      )}
                      {f.matched_db_trade && (
                        <div>
                          <span className="font-medium text-white">Trade app:</span>{' '}
                          #{f.matched_db_trade.id} — tipo {f.matched_db_trade.type} —
                          P&L app: <PnlChip value={f.matched_db_trade.pnl} />
                          {f.fill_pnl !== null && f.fill_pnl !== undefined && f.matched_db_trade.pnl !== null && (
                            <span className="ml-2 text-amber-400">
                              Δ ${fmt(f.fill_pnl - (f.matched_db_trade.pnl ?? 0))}
                            </span>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                )}
              </React.Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function DbTradesTable({ trades, locale }) {
  if (!trades.length) return (
    <div className="text-center text-muted py-6 text-sm">Nenhum trade registrado no banco nesta data.</div>
  )
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-muted text-[11px] uppercase tracking-wide">
            <th className="text-left py-2 px-3 font-medium">Hora</th>
            <th className="text-left py-2 px-3 font-medium">Bot ID</th>
            <th className="text-left py-2 px-3 font-medium">Tipo</th>
            <th className="text-left py-2 px-3 font-medium">Dir</th>
            <th className="text-left py-2 px-3 font-medium">Símbolo</th>
            <th className="text-right py-2 px-3 font-medium">Entrada</th>
            <th className="text-right py-2 px-3 font-medium">Saída</th>
            <th className="text-right py-2 px-3 font-medium">P&L App</th>
            <th className="text-left py-2 px-3 font-medium">Evento</th>
          </tr>
        </thead>
        <tbody>
          {trades.map(t => (
            <tr key={t.id} className="border-b border-border/40 hover:bg-panel/40">
              <td className="py-2 px-3 text-muted text-xs font-mono">
                {t.timestamp ? new Date(t.timestamp).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' }) : '—'}
              </td>
              <td className="py-2 px-3 text-xs text-muted">#{t.bot_id}</td>
              <td className="py-2 px-3">
                <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded
                  ${t.type === 'entry' ? 'bg-accent/15 text-accent' : 'bg-bear/15 text-bear'}`}>
                  {t.type}
                </span>
              </td>
              <td className="py-2 px-3 text-xs">
                {t.direction === 'LONG'
                  ? <span className="text-bull font-medium">LONG</span>
                  : t.direction === 'SHORT'
                  ? <span className="text-bear font-medium">SHORT</span>
                  : '—'}
              </td>
              <td className="py-2 px-3 font-mono text-xs">{t.symbol}</td>
              <td className="py-2 px-3 text-right font-mono text-xs">{t.entry_price ? `$${fmt(t.entry_price, 4)}` : '—'}</td>
              <td className="py-2 px-3 text-right font-mono text-xs">{t.exit_price  ? `$${fmt(t.exit_price,  4)}` : '—'}</td>
              <td className="py-2 px-3 text-right"><PnlChip value={t.pnl} /></td>
              <td className="py-2 px-3 text-xs text-muted">{t.event ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function EquitySparkline({ points }) {
  if (!points || points.length < 2) return null
  const equities = points.map(p => p.equity).filter(Boolean)
  if (!equities.length) return null
  const min = Math.min(...equities), max = Math.max(...equities)
  const range = max - min || 1
  const W = 400, H = 60, pad = 4
  const coords = equities.map((v, i) => {
    const x = pad + (i / (equities.length - 1)) * (W - pad * 2)
    const y = pad + (1 - (v - min) / range) * (H - pad * 2)
    return `${x},${y}`
  }).join(' ')
  const color = equities[equities.length - 1] >= equities[0] ? '#22c55e' : '#ef4444'
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-14">
      <polyline points={coords} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  )
}

function TabAtividades() {
  const { lang } = useLanguage()
  const locale   = lang === 'en-US' ? 'en-US' : 'pt-BR'
  const today    = new Date().toLocaleDateString('en-CA')
  const [selectedDate, setSelectedDate] = useState(today)
  const [innerTab, setInnerTab]         = useState('fills')

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['activities', selectedDate],
    queryFn:  () => getActivities(selectedDate),
    staleTime: 60_000,
  })

  const account  = data?.account       ?? {}
  const recon    = data?.reconciliation ?? {}
  const fills    = data?.fills          ?? []
  const dbTrades = data?.db_trades      ?? []
  const history  = data?.history        ?? []
  const discrepancy    = recon.discrepancy ?? 0
  const hasDiscrepancy = Math.abs(discrepancy) >= 0.01

  return (
    <div className="space-y-5">
      {/* Controles */}
      <div className="flex items-center justify-end gap-3">
        <input
          type="date"
          value={selectedDate}
          max={today}
          onChange={e => setSelectedDate(e.target.value)}
          className="input text-sm"
        />
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="btn-secondary flex items-center gap-1.5 text-sm"
        >
          <RefreshCcw size={14} className={isFetching ? 'animate-spin' : ''} />
          Atualizar
        </button>
      </div>

      {isError && (
        <div className="card border border-bear/30 bg-bear/5 text-bear text-sm p-4 flex items-center gap-2">
          <AlertTriangle size={16} />
          Erro ao buscar dados da OKX: {error?.message ?? 'desconhecido'}
        </div>
      )}

      {isLoading ? (
        <div className="text-center text-muted py-20 text-sm">Carregando dados da OKX…</div>
      ) : data && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard label="Equity (OKX)"    value={`$${fmt(account.equity)}`}   sub={`Cash: $${fmt(account.cash)}`} color="accent" icon={DollarSign} />
            <StatCard
              label="P&L do Dia (OKX)"
              value={`${(account.day_pl ?? 0) >= 0 ? '+' : ''}$${fmt(account.day_pl)}`}
              sub="equity − equity_anterior"
              color={(account.day_pl ?? 0) >= 0 ? 'bull' : 'bear'}
              icon={(account.day_pl ?? 0) >= 0 ? TrendingUp : TrendingDown}
            />
            <StatCard
              label="P&L App (banco)"
              value={`${(recon.app_pnl_today ?? 0) >= 0 ? '+' : ''}$${fmt(recon.app_pnl_today)}`}
              sub={`${recon.db_trades_count ?? 0} trades no banco`}
              color={(recon.app_pnl_today ?? 0) >= 0 ? 'bull' : 'bear'}
              icon={DollarSign}
            />
            <StatCard
              label="P&L OKX (fills)"
              value={`${(recon.exchange_realized_pl ?? 0) >= 0 ? '+' : ''}$${fmt(recon.exchange_realized_pl)}`}
              sub={`${recon.fills_count ?? 0} fills hoje`}
              color={(recon.exchange_realized_pl ?? 0) >= 0 ? 'bull' : 'bear'}
              icon={DollarSign}
            />
          </div>

          <div className={`card p-4 flex items-start gap-3 border ${hasDiscrepancy ? 'border-amber-500/30 bg-amber-500/5' : 'border-bull/20 bg-bull/5'}`}>
            <div className="mt-0.5">
              {hasDiscrepancy ? <AlertTriangle size={18} className="text-amber-400" /> : <CheckCircle size={18} className="text-bull" />}
            </div>
            <div className="flex-1 space-y-1">
              <p className="font-semibold text-sm">
                {hasDiscrepancy ? 'Discrepância detectada entre App e OKX' : 'Valores reconciliados ✓'}
              </p>
              <div className="grid grid-cols-4 gap-4 text-xs text-muted mt-2">
                <div><span className="block text-white/60">P&L Bruto (fills)</span><PnlChip value={recon.exchange_realized_pl} /></div>
                <div>
                  <span className="block text-white/60">Fees (CFEE)</span>
                  <span className="font-semibold text-bear">{recon.total_fees > 0 ? `-$${fmt(recon.total_fees)}` : '—'}</span>
                </div>
                <div><span className="block text-white/60">P&L Líquido</span><PnlChip value={recon.net_pnl_after_fees} /></div>
                <div>
                  <span className="block text-white/60">P&L App (banco)</span>
                  <span className={`font-semibold ${hasDiscrepancy ? 'text-amber-400' : 'text-bull'}`}>
                    {(recon.app_pnl_today ?? 0) >= 0 ? '+' : ''}${fmt(recon.app_pnl_today)}
                    {hasDiscrepancy && <span className="text-amber-400 ml-1">(Δ {discrepancy >= 0 ? '+' : ''}${fmt(discrepancy)})</span>}
                  </span>
                </div>
              </div>
              {recon.cross_day_fifo && (
                <p className="text-xs text-accent/80 mt-2">
                  FIFO cross-day ativo: fills carregados desde {recon.fifo_since} para calcular P&L de posições abertas em dias anteriores.
                </p>
              )}
              {hasDiscrepancy && (
                <p className="text-xs text-amber-400/80 mt-2">
                  Uma discrepância pode indicar fills não capturados pelo bot, trades em aberto ainda não encerrados, ou slippage entre preço estimado e filled_avg_price.
                </p>
              )}
            </div>
          </div>

          {history.length > 1 && (
            <div className="card p-4">
              <p className="text-xs text-muted mb-2 font-medium uppercase tracking-wide">Equity intraday</p>
              <EquitySparkline points={history} />
              <div className="flex justify-between text-[10px] text-muted mt-1">
                <span>{history[0]?.ts ? new Date(history[0].ts * 1000).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' }) : ''}</span>
                <span className="font-semibold text-white">${fmt(history[history.length - 1]?.equity)}</span>
                <span>{history[history.length - 1]?.ts ? new Date(history[history.length - 1].ts * 1000).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' }) : ''}</span>
              </div>
            </div>
          )}

          <div className="card overflow-hidden">
            <div className="flex border-b border-border">
              {[
                { key: 'fills',  label: `Fills OKX (${fills.length})` },
                { key: 'trades', label: `Trades App (${dbTrades.length})` },
              ].map(tab => (
                <button
                  key={tab.key}
                  onClick={() => setInnerTab(tab.key)}
                  className={`px-5 py-3 text-sm font-medium transition-colors border-b-2 -mb-px
                    ${innerTab === tab.key ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-white'}`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <div className="p-0">
              {innerTab === 'fills'  && <FillsTable   fills={fills}     locale={locale} />}
              {innerTab === 'trades' && <DbTradesTable trades={dbTrades} locale={locale} />}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

// ── Página principal ──────────────────────────────────────────────────────────

const TABS = [
  { key: 'historico',  label: 'Histórico de Trades', icon: History   },
  { key: 'atividades', label: 'Atividades OKX',       icon: Activity  },
]

export default function Registros() {
  const [tab, setTab] = useState('historico')

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-xl font-bold">Registros</h1>
        <p className="text-muted text-sm mt-0.5">Histórico de trades da app e atividades oficiais da OKX</p>
      </div>

      {/* Tab selector */}
      <div className="flex gap-1 border-b border-border">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex items-center gap-2 px-5 py-3 text-sm font-medium transition-colors border-b-2 -mb-px
              ${tab === key ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-white'}`}
          >
            <Icon size={15} />
            {label}
          </button>
        ))}
      </div>

      {tab === 'historico'  && <TabHistorico />}
      {tab === 'atividades' && <TabAtividades />}
    </div>
  )
}
