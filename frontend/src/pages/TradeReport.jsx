import React, { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, TrendingUp, TrendingDown, Clock, Target, ShieldAlert, Bot, ChevronDown } from 'lucide-react'
import Chart from '../components/Chart'

async function fetchReport(botId, tradeId) {
  const url = tradeId
    ? `/api/bots/${botId}/trade-report?trade_id=${tradeId}`
    : `/api/bots/${botId}/trade-report`
  const r = await fetch(url)
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

function Stat({ label, value, sub, color }) {
  return (
    <div className="bg-white/5 rounded-xl p-4 flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-widest text-muted font-bold">{label}</span>
      <span className={`text-xl font-bold font-mono ${color ?? 'text-white'}`}>{value}</span>
      {sub && <span className="text-[11px] text-muted">{sub}</span>}
    </div>
  )
}

export default function TradeReport() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [selectedTradeId, setSelectedTradeId] = useState(null)

  const { data, isLoading, error } = useQuery({
    queryKey: ['trade-report', id, selectedTradeId],
    queryFn: () => fetchReport(id, selectedTradeId),
    staleTime: 60_000,
  })

  if (isLoading) return (
    <div className="flex items-center justify-center h-64 text-muted text-sm">Carregando relatório…</div>
  )
  if (error) return (
    <div className="flex items-center justify-center h-64 text-bear text-sm">
      Nenhum trade finalizado para este robô ainda.
    </div>
  )

  const { bot, trade, all_trades, candles, markers, ai_analysis } = data
  const isLong  = trade.direction === 'LONG'
  const pnlPos  = trade.pnl >= 0

  // Convert marker timestamps to trade objects that Chart.jsx understands
  const chartTrades = markers.map(m => ({
    ...m,
    timestamp: m.timestamp,
  }))

  return (
    <div className="max-w-5xl mx-auto px-4 py-8 space-y-6">

      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={() => navigate(-1)}
          className="p-2 rounded-lg bg-white/5 hover:bg-white/10 transition-colors"
        >
          <ArrowLeft size={18} />
        </button>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <Bot size={20} className="text-accent" />
            <h1 className="text-xl font-bold">{bot.name}</h1>
            <span className="text-xs bg-white/10 px-2 py-0.5 rounded font-mono">{bot.strategy_id}</span>
            <span className={`text-xs px-2 py-0.5 rounded font-bold ${isLong ? 'bg-bull/20 text-bull' : 'bg-bear/20 text-bear'}`}>
              {trade.direction}
            </span>
          </div>
          <p className="text-sm text-muted mt-0.5">{bot.symbol} · {bot.timeframe} · Relatório Evolutivo</p>
        </div>

        {/* Trade selector */}
        {all_trades.length > 1 && (
          <div className="relative">
            <select
              value={selectedTradeId ?? trade.id}
              onChange={e => setSelectedTradeId(Number(e.target.value))}
              className="appearance-none bg-white/5 border border-white/10 rounded-lg px-3 py-2 pr-8 text-sm text-white cursor-pointer"
            >
              {all_trades.map((t, i) => (
                <option key={t.id} value={t.id}>
                  Trade #{i + 1} — {t.entry_price ? `$${Number(t.entry_price).toFixed(2)}` : '?'} → {t.pnl >= 0 ? '+' : ''}${Number(t.pnl).toFixed(2)}
                </option>
              ))}
            </select>
            <ChevronDown size={14} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted pointer-events-none" />
          </div>
        )}
      </div>

      {/* Stats grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat
          label="Entrada"
          value={`$${Number(trade.entry_price).toFixed(4)}`}
          sub={trade.entry_time ? new Date(trade.entry_time).toLocaleString() : '—'}
        />
        <Stat
          label="Saída"
          value={`$${Number(trade.exit_price).toFixed(4)}`}
          sub={trade.closed_at ? new Date(trade.closed_at).toLocaleString() : '—'}
        />
        <Stat
          label="PnL Realizado"
          value={`${pnlPos ? '+' : ''}$${Number(trade.pnl).toFixed(2)}`}
          sub={`${trade.pnl_pct >= 0 ? '+' : ''}${Number(trade.pnl_pct).toFixed(2)}%`}
          color={pnlPos ? 'text-bull' : 'text-bear'}
        />
        <Stat
          label="Duração"
          value={trade.duration}
          sub={`${trade.n_candles} candles`}
        />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat
          label="Stop Loss Inicial"
          value={trade.sl_price ? `$${Number(trade.sl_price).toFixed(4)}` : '—'}
          color="text-bear"
        />
        <Stat
          label="Take Profit 1"
          value={trade.tp1_price ? `$${Number(trade.tp1_price).toFixed(4)}` : '—'}
          color="text-accent"
        />
        <Stat
          label="Pico na Operação"
          value={trade.peak_price ? `$${Number(trade.peak_price).toFixed(4)}` : '—'}
          color={isLong ? 'text-bull' : 'text-bear'}
        />
        <Stat
          label="Resultado"
          value={pnlPos ? 'GANHO' : 'PERDA'}
          color={pnlPos ? 'text-bull' : 'text-bear'}
        />
      </div>

      {/* Chart */}
      <div className="rounded-xl overflow-hidden border border-white/5">
        <div className="bg-white/5 px-4 py-2 text-xs font-bold uppercase tracking-widest text-muted">
          Gráfico da Operação
        </div>
        {candles.length > 0 ? (
          <Chart
            candles={candles}
            indicators={{}}
            strategyId={bot.strategy_id}
            trades={chartTrades}
            height={420}
          />
        ) : (
          <div className="flex items-center justify-center h-48 text-muted text-sm">
            Dados de candle não disponíveis para este trade.
          </div>
        )}
      </div>

      {/* AI Analysis */}
      <div className="rounded-xl border border-accent/20 bg-accent/5 p-5 space-y-3">
        <div className="flex items-center gap-2">
          <TrendingUp size={16} className="text-accent" />
          <span className="text-sm font-bold uppercase tracking-widest text-accent">Análise do Especialista (DeepSeek)</span>
        </div>
        <p className="text-sm text-muted leading-relaxed whitespace-pre-line">
          {ai_analysis ?? 'Análise não disponível.'}
        </p>
      </div>

      {/* Timeline */}
      <div className="rounded-xl border border-white/5 overflow-hidden">
        <div className="bg-white/5 px-4 py-2 text-xs font-bold uppercase tracking-widest text-muted">
          Linha do Tempo
        </div>
        <div className="divide-y divide-white/5">
          {markers.map((m, i) => (
            <div key={i} className="flex items-center gap-4 px-4 py-3">
              <div className={`w-2 h-2 rounded-full flex-shrink-0 ${
                m.type === 'entry' ? (isLong ? 'bg-bull' : 'bg-bear') : 'bg-amber-400'
              }`} />
              <div className="flex-1">
                <span className="text-sm font-bold capitalize">
                  {m.type === 'entry' ? `Abertura ${trade.direction}` : 'Fechamento'}
                </span>
                <span className="text-xs text-muted ml-2">
                  {m.timestamp ? new Date(m.timestamp).toLocaleString() : '—'}
                </span>
              </div>
              <span className="text-sm font-mono">
                {m.type === 'entry'
                  ? `$${Number(trade.entry_price).toFixed(4)}`
                  : `$${Number(trade.exit_price).toFixed(4)} · ${m.pnl >= 0 ? '+' : ''}$${Number(m.pnl).toFixed(2)}`}
              </span>
            </div>
          ))}
        </div>
      </div>

    </div>
  )
}
