import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  DollarSign, TrendingUp, TrendingDown, AlertTriangle,
  CheckCircle, Activity, RefreshCcw, ChevronDown, ChevronRight,
} from 'lucide-react'
import { getActivities } from '../api'
import { useLanguage } from '../i18n/LanguageContext'
import StatCard from '../components/StatCard'

// ── helpers ──────────────────────────────────────────────────────────────────

function fmt(val, decimals = 2) {
  if (val === null || val === undefined) return '—'
  return Number(val).toLocaleString('pt-BR', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

function PnlChip({ value, suffix = '' }) {
  if (value === null || value === undefined) return <span className="text-muted">—</span>
  const pos = value >= 0
  return (
    <span className={`font-semibold tabular-nums ${pos ? 'text-bull' : 'text-bear'}`}>
      {pos ? '+' : ''}${fmt(value)}{suffix}
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

function DiscrepancyBadge({ value }) {
  if (value === null || value === undefined) return null
  const abs = Math.abs(value)
  if (abs < 0.01) return <span className="text-xs text-bull flex items-center gap-1"><CheckCircle size={12} /> OK</span>
  return (
    <span className="text-xs text-amber-400 flex items-center gap-1">
      <AlertTriangle size={12} /> ${fmt(abs)} desvio
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

// ── Fills table ───────────────────────────────────────────────────────────────

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
            const isOpen = expanded === i
            const hasMeta = f.matched_db_trade || (f.matched_bots && f.matched_bots.length > 0)

            return (
              <React.Fragment key={f.id || i}>
                <tr
                  className={`border-b border-border/40 hover:bg-panel/40 transition-colors
                    ${hasMeta ? 'cursor-pointer' : ''}`}
                  onClick={() => hasMeta && setExpanded(isOpen ? null : i)}
                >
                  <td className="py-2.5 px-3 text-muted">
                    {hasMeta
                      ? (isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />)
                      : null}
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

// ── DB Trades table (compacta) ────────────────────────────────────────────────

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
              <td className="py-2 px-3 text-right font-mono text-xs">
                {t.entry_price ? `$${fmt(t.entry_price, 4)}` : '—'}
              </td>
              <td className="py-2 px-3 text-right font-mono text-xs">
                {t.exit_price ? `$${fmt(t.exit_price, 4)}` : '—'}
              </td>
              <td className="py-2 px-3 text-right"><PnlChip value={t.pnl} /></td>
              <td className="py-2 px-3 text-xs text-muted">{t.event ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Equity mini-chart ─────────────────────────────────────────────────────────

function EquitySparkline({ points }) {
  if (!points || points.length < 2) return null
  const equities = points.map(p => p.equity).filter(Boolean)
  if (!equities.length) return null

  const min = Math.min(...equities)
  const max = Math.max(...equities)
  const range = max - min || 1
  const W = 400, H = 60, pad = 4

  const coords = equities.map((v, i) => {
    const x = pad + (i / (equities.length - 1)) * (W - pad * 2)
    const y = pad + (1 - (v - min) / range) * (H - pad * 2)
    return `${x},${y}`
  }).join(' ')

  const last = equities[equities.length - 1]
  const first = equities[0]
  const color = last >= first ? '#22c55e' : '#ef4444'

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-14">
      <polyline points={coords} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Activities() {
  const { lang } = useLanguage()
  const locale = lang === 'en-US' ? 'en-US' : 'pt-BR'

  const today = new Date().toLocaleDateString('en-CA')  // YYYY-MM-DD no fuso local
  const [selectedDate, setSelectedDate] = useState(today)
  const [activeTab, setActiveTab] = useState('fills')

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['activities', selectedDate],
    queryFn:  () => getActivities(selectedDate),
    staleTime: 60_000,
  })

  const account = data?.account ?? {}
  const recon   = data?.reconciliation ?? {}
  const fills   = data?.fills ?? []
  const dbTrades = data?.db_trades ?? []
  const history  = data?.history ?? []

  const discrepancy = recon.discrepancy ?? 0
  const hasDiscrepancy = Math.abs(discrepancy) >= 0.01

  return (
    <div className="p-6 space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2">
            <Activity size={20} className="text-accent" />
            Activities — Dados Oficiais OKX
          </h1>
          <p className="text-muted text-sm mt-0.5">
            Fills reais da corretora correlacionados com os bots do sistema
          </p>
        </div>
        <div className="flex items-center gap-3">
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
          {/* Stat cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard
              label="Equity (OKX)"
              value={`$${fmt(account.equity)}`}
              sub={`Cash: $${fmt(account.cash)}`}
              color="accent"
              icon={DollarSign}
            />
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

          {/* Reconciliação banner */}
          <div className={`card p-4 flex items-start gap-3 border
            ${hasDiscrepancy
              ? 'border-amber-500/30 bg-amber-500/5'
              : 'border-bull/20 bg-bull/5'}`}>
            <div className="mt-0.5">
              {hasDiscrepancy
                ? <AlertTriangle size={18} className="text-amber-400" />
                : <CheckCircle size={18} className="text-bull" />}
            </div>
            <div className="flex-1 space-y-1">
              <p className="font-semibold text-sm">
                {hasDiscrepancy ? 'Discrepância detectada entre App e OKX' : 'Valores reconciliados ✓'}
              </p>
              <div className="grid grid-cols-4 gap-4 text-xs text-muted mt-2">
                <div>
                  <span className="block text-white/60">P&L Bruto (fills)</span>
                  <PnlChip value={recon.exchange_realized_pl} />
                </div>
                <div>
                  <span className="block text-white/60">Fees (CFEE)</span>
                  <span className="font-semibold text-bear">
                    {recon.total_fees > 0 ? `-$${fmt(recon.total_fees)}` : '—'}
                  </span>
                </div>
                <div>
                  <span className="block text-white/60">P&L Líquido</span>
                  <PnlChip value={recon.net_pnl_after_fees} />
                </div>
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
                  FIFO cross-day ativo: fills carregados desde {recon.fifo_since} para calcular
                  P&L de posições abertas em dias anteriores.
                </p>
              )}
              {hasDiscrepancy && (
                <p className="text-xs text-amber-400/80 mt-2">
                  Uma discrepância pode indicar fills não capturados pelo bot, trades em aberto
                  ainda não encerrados, ou slippage entre preço estimado e filled_avg_price.
                </p>
              )}
            </div>
          </div>

          {/* Equity sparkline */}
          {history.length > 1 && (
            <div className="card p-4">
              <p className="text-xs text-muted mb-2 font-medium uppercase tracking-wide">Equity intraday</p>
              <EquitySparkline points={history} />
              <div className="flex justify-between text-[10px] text-muted mt-1">
                <span>
                  {history[0]?.ts
                    ? new Date(history[0].ts * 1000).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
                    : ''}
                </span>
                <span className="font-semibold text-white">${fmt(history[history.length - 1]?.equity)}</span>
                <span>
                  {history[history.length - 1]?.ts
                    ? new Date(history[history.length - 1].ts * 1000).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
                    : ''}
                </span>
              </div>
            </div>
          )}

          {/* Tabs */}
          <div className="card overflow-hidden">
            <div className="flex border-b border-border">
              {[
                { key: 'fills',  label: `Fills OKX (${fills.length})` },
                { key: 'trades', label: `Trades App (${dbTrades.length})` },
              ].map(tab => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={`px-5 py-3 text-sm font-medium transition-colors border-b-2 -mb-px
                    ${activeTab === tab.key
                      ? 'border-accent text-accent'
                      : 'border-transparent text-muted hover:text-white'}`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            <div className="p-0">
              {activeTab === 'fills'  && <FillsTable   fills={fills}    locale={locale} />}
              {activeTab === 'trades' && <DbTradesTable trades={dbTrades} locale={locale} />}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
