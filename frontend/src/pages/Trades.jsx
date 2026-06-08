import React, { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getTrades, getTradesSummary, getBots, syncTradeFees } from '../api'
import { useLanguage } from '../i18n/LanguageContext'
import TradeTable from '../components/TradeTable'
import StatCard   from '../components/StatCard'
import { DollarSign, Target, TrendingUp, TrendingDown, RefreshCcw } from 'lucide-react'

export default function Trades() {
  const { t } = useLanguage()
  const qc = useQueryClient()
  const [botFilter, setBotFilter] = useState('')
  const [syncing, setSyncing]     = useState(false)

  const { data: bots   = [] } = useQuery({ queryKey: ['bots'],     queryFn: getBots })
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

  const gross     = summary.total_pnl  ?? 0
  const fees      = summary.total_fees ?? 0
  const net       = summary.net_pnl    ?? gross    // fallback gracioso antes do primeiro sync
  const hasFees   = (summary.fees_synced ?? 0) > 0
  const pending   = summary.fees_pending ?? 0

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold">{t('trades.title')}</h1>
          <p className="text-muted text-sm mt-0.5">{t('trades.subtitle')}</p>
        </div>
        <button
          onClick={handleSyncFees}
          disabled={syncing}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-accent/10 hover:bg-accent/20 text-accent border border-accent/20 transition-all disabled:opacity-50"
          title="Busca taxas na OKX e corrige saldos"
        >
          <RefreshCcw className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : ''}`} />
          {syncing ? 'Sincronizando taxas...' : 'Sincronizar Corretagem'}
        </button>
      </div>

      <div className="flex items-center gap-3">
        <label className="text-sm text-muted">{t('trades.filter_bot')}</label>
        <select className="input max-w-xs" value={botFilter}
          onChange={e => setBotFilter(e.target.value)}>
          <option value="">{t('c.all')}</option>
          {bots.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
        {pending > 0 && (
          <span className="text-[10px] text-yellow-400/80 bg-yellow-400/10 border border-yellow-400/20 px-2 py-0.5 rounded-full">
            {pending} trade{pending > 1 ? 's' : ''} sem taxa registrada
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {/* P&L Líquido (principal) */}
        <StatCard
          label="P&L Líquido (após taxas)"
          value={`${net >= 0 ? '+' : ''}$${net.toFixed(2)}`}
          color={net >= 0 ? 'bull' : 'bear'}
          icon={DollarSign}
          rows={hasFees ? [
            {
              source: 'okx',
              label: 'Bruto',
              value: `${gross >= 0 ? '+' : ''}$${gross.toFixed(2)}`,
              color: gross >= 0 ? 'text-white/60' : 'text-bear/60',
            },
            {
              source: 'app',
              label: 'Taxas',
              value: fees > 0 ? `-$${fees.toFixed(4)}` : '$0',
              color: 'text-bear/70',
            },
          ] : undefined}
          sub={!hasFees ? `${gross >= 0 ? '+' : ''}$${gross.toFixed(2)} (taxas pendentes)` : undefined}
        />

        {/* Win Rate */}
        <StatCard
          label={t('c.win_rate')}
          value={`${(summary.win_rate_net ?? summary.win_rate ?? 0).toFixed(1)}%`}
          sub={`${summary.wins ?? 0}W · ${summary.losses ?? 0}L`}
          color={(summary.win_rate_net ?? summary.win_rate ?? 0) >= 50 ? 'bull' : 'bear'}
          icon={Target}
        />

        {/* Melhor trade */}
        <StatCard
          label={t('trades.best')}
          value={summary.best_trade !== undefined ? `${summary.best_trade >= 0 ? '+' : ''}$${summary.best_trade.toFixed(2)}` : '—'}
          color={summary.best_trade >= 0 ? 'bull' : 'bear'}
          icon={TrendingUp}
        />

        {/* Pior trade */}
        <StatCard
          label={t('trades.worst')}
          value={summary.worst_trade !== undefined ? `${summary.worst_trade >= 0 ? '+' : ''}$${summary.worst_trade.toFixed(2)}` : '—'}
          color={summary.worst_trade >= 0 ? 'bull' : 'bear'}
          icon={TrendingDown}
        />
      </div>

      {/* Resumo de taxas se houver dados */}
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
