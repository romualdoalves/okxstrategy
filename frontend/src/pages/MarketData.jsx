import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Database, RefreshCw, PlusCircle, Trash2, DownloadCloud } from 'lucide-react'
import {
  bootstrapMarketData,
  forceSyncAllMarketData,
  forceSyncMarketData,
  getMarketDataSyncJobs,
  getTrackedMarketData,
  removeTrackedMarketData,
  trackMarketDataSymbol,
} from '../api'

const DEFAULT_TIMEFRAMES = ['15m', '1h', '4h']

function fmtDate(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '-'
  return d.toLocaleString()
}

export default function MarketData() {
  const qc = useQueryClient()
  const [symbol, setSymbol] = useState('')
  const [timeframes, setTimeframes] = useState(DEFAULT_TIMEFRAMES.join(','))
  const [bulkTimeframe, setBulkTimeframe] = useState('')
  const [busyId, setBusyId] = useState('')
  const [activeBatchId, setActiveBatchId] = useState('')

  const { data = [], isLoading, error } = useQuery({
    queryKey: ['market-data-tracked'],
    queryFn: getTrackedMarketData,
    refetchInterval: 10_000,
  })

  const { data: syncJobs = [] } = useQuery({
    queryKey: ['market-data-sync-jobs'],
    queryFn: () => getMarketDataSyncJobs(120),
    refetchInterval: 5000,
  })

  const summary = useMemo(() => {
    const bySymbol = {}
    for (const row of data) {
      bySymbol[row.symbol] = bySymbol[row.symbol] || { symbol: row.symbol, tfs: 0, candles: 0 }
      bySymbol[row.symbol].tfs += 1
      bySymbol[row.symbol].candles += Number(row.candle_count || 0)
    }
    return {
      symbols: Object.keys(bySymbol).length,
      timeframes: data.length,
      candles: data.reduce((acc, r) => acc + Number(r.candle_count || 0), 0),
    }
  }, [data])

  const readiness = useMemo(() => {
    const total = data.length
    if (!total) return { ready: 0, total: 0, percent: 0 }
    const now = Date.now()
    const ready = data.filter(row => {
      const hasCandles = Number(row.candle_count || 0) > 0
      const recentSync = row.last_sync ? (now - new Date(row.last_sync).getTime()) < 45 * 60 * 1000 : false
      return hasCandles && recentSync
    }).length
    return { ready, total, percent: Math.round((ready / total) * 100) }
  }, [data])

  const activeBatchJobs = useMemo(() => {
    if (!activeBatchId) return []
    return syncJobs.filter(j => j.batch_id === activeBatchId)
  }, [syncJobs, activeBatchId])

  const activeBatchProgress = useMemo(() => {
    if (!activeBatchJobs.length) {
      return { total: 0, done: 0, running: 0, failed: 0, percent: 0, finished: false }
    }
    const total = activeBatchJobs.length
    const done = activeBatchJobs.filter(j => j.status === 'success').length
    const running = activeBatchJobs.filter(j => j.status === 'running' || j.status === 'queued').length
    const failed = activeBatchJobs.filter(j => j.status === 'failed').length
    const finished = done + failed >= total
    const percent = Math.round(((done + failed) / total) * 100)
    return { total, done, running, failed, percent, finished }
  }, [activeBatchJobs])

  const invalidate = () => qc.invalidateQueries({ queryKey: ['market-data-tracked'] })

  const bootstrapMut = useMutation({
    mutationFn: () => bootstrapMarketData([], DEFAULT_TIMEFRAMES),
    onSuccess: (resp) => {
      if (resp?.batch_id) setActiveBatchId(resp.batch_id)
      invalidate()
    },
  })

  const addMut = useMutation({
    mutationFn: ({ sym, tfs }) => trackMarketDataSymbol(sym, tfs),
    onSuccess: () => {
      setSymbol('')
      invalidate()
    },
  })

  const syncMut = useMutation({
    mutationFn: ({ sym, tf }) => forceSyncMarketData(sym, tf || null),
    onSuccess: invalidate,
    onSettled: () => setBusyId(''),
  })

  const bulkSyncMut = useMutation({
    mutationFn: () => forceSyncAllMarketData(bulkTimeframe || null),
    onSuccess: (resp) => {
      if (resp?.batch_id) setActiveBatchId(resp.batch_id)
      invalidate()
    },
  })

  const delMut = useMutation({
    mutationFn: ({ sym, tf }) => removeTrackedMarketData(sym, tf || null),
    onSuccess: invalidate,
    onSettled: () => setBusyId(''),
  })

  function parseTimeframes(input) {
    return (input || '')
      .split(',')
      .map(v => v.trim())
      .filter(Boolean)
  }

  function onAdd() {
    const sym = symbol.trim().toUpperCase().replace('/', '-')
    const tfs = parseTimeframes(timeframes)
    if (!sym || !tfs.length) return
    addMut.mutate({ sym, tfs })
  }

  return (
    <section className="p-6 space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Database size={22} className="text-accent" />
            MarketData Cache (OKX)
          </h1>
          <p className="text-sm text-muted mt-1">Gerencie o cache local de candles para acelerar scanner e backtests.</p>
        </div>
        <div className="flex items-center gap-2">
          <input
            value={bulkTimeframe}
            onChange={e => setBulkTimeframe(e.target.value)}
            placeholder="TF opcional (ex: 1h)"
            className="px-3 py-2 rounded-lg bg-white/5 border border-white/10 outline-none focus:border-accent/50 text-sm"
          />
          <button
            onClick={() => bulkSyncMut.mutate()}
            disabled={bulkSyncMut.isPending}
            className="px-3 py-2 rounded-lg bg-blue-500/15 text-blue-300 border border-blue-500/30 hover:bg-blue-500/25 disabled:opacity-60 inline-flex items-center gap-2"
          >
            <RefreshCw size={14} />
            Sync em lote
          </button>
          <button
            onClick={() => bootstrapMut.mutate()}
            disabled={bootstrapMut.isPending}
            className="px-3 py-2 rounded-lg bg-accent/15 text-accent border border-accent/30 hover:bg-accent/25 disabled:opacity-60 inline-flex items-center gap-2"
          >
            <DownloadCloud size={14} />
            Bootstrap padrão
          </button>
        </div>
      </header>

      <div className="rounded-xl bg-panel border border-border p-4 space-y-2">
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted">Prontidão do cache</span>
          <span className="font-mono">{readiness.ready}/{readiness.total} ({readiness.percent}%)</span>
        </div>
        <div className="h-2 rounded-full bg-white/10 overflow-hidden">
          <div
            className="h-full bg-green-400/80 transition-all duration-500"
            style={{ width: `${readiness.percent}%` }}
          />
        </div>
        <div className="text-xs text-muted">
          Considera pronto quando tem candles e sync recente (ult. 45 min).
        </div>
      </div>

      {activeBatchId && (
        <div className="rounded-xl bg-panel border border-border p-4 space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span className="text-muted">Lote ativo</span>
            <span className="font-mono">
              {activeBatchProgress.done}/{activeBatchProgress.total} concluídos
              {activeBatchProgress.failed > 0 ? ` • ${activeBatchProgress.failed} falhas` : ''}
            </span>
          </div>
          <div className="h-2 rounded-full bg-white/10 overflow-hidden">
            <div
              className="h-full bg-blue-400/80 transition-all duration-300"
              style={{ width: `${activeBatchProgress.percent}%` }}
            />
          </div>
          <div className="text-xs text-muted">
            {activeBatchProgress.finished
              ? 'Lote finalizado.'
              : `${activeBatchProgress.running} jobs ainda em execução/fila.`}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="rounded-xl bg-panel border border-border p-4">
          <div className="text-xs text-muted uppercase tracking-wider">Símbolos</div>
          <div className="text-2xl font-bold mt-1">{summary.symbols}</div>
        </div>
        <div className="rounded-xl bg-panel border border-border p-4">
          <div className="text-xs text-muted uppercase tracking-wider">Símbolo/TF</div>
          <div className="text-2xl font-bold mt-1">{summary.timeframes}</div>
        </div>
        <div className="rounded-xl bg-panel border border-border p-4">
          <div className="text-xs text-muted uppercase tracking-wider">Candles</div>
          <div className="text-2xl font-bold mt-1">{summary.candles.toLocaleString()}</div>
        </div>
      </div>

      <div className="rounded-xl bg-panel border border-border p-4 space-y-3">
        <h2 className="text-sm font-bold uppercase tracking-wider text-muted">Adicionar rastreamento</h2>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-2">
          <input
            value={symbol}
            onChange={e => setSymbol(e.target.value)}
            placeholder="BTC-USDT"
            className="px-3 py-2 rounded-lg bg-white/5 border border-white/10 outline-none focus:border-accent/50"
          />
          <input
            value={timeframes}
            onChange={e => setTimeframes(e.target.value)}
            placeholder="15m,1h,4h"
            className="px-3 py-2 rounded-lg bg-white/5 border border-white/10 outline-none focus:border-accent/50"
          />
          <button
            onClick={onAdd}
            disabled={addMut.isPending}
            className="px-3 py-2 rounded-lg bg-green-500/15 text-green-300 border border-green-500/30 hover:bg-green-500/25 disabled:opacity-60 inline-flex items-center justify-center gap-2"
          >
            <PlusCircle size={14} />
            Adicionar
          </button>
        </div>
      </div>

      <div className="rounded-xl bg-panel border border-border overflow-hidden">
        <div className="px-4 py-3 border-b border-border text-sm font-semibold">Itens rastreados</div>
        {isLoading && <div className="p-4 text-sm text-muted">Carregando...</div>}
        {error && <div className="p-4 text-sm text-red-400">{error.message}</div>}
        {!isLoading && !error && data.length === 0 && (
          <div className="p-4 text-sm text-muted">Nenhum símbolo rastreado.</div>
        )}
        {!isLoading && !error && data.length > 0 && (
          <div className="overflow-auto">
            <table className="w-full text-sm">
              <thead className="bg-white/5 text-muted">
                <tr>
                  <th className="text-left px-4 py-2">Símbolo</th>
                  <th className="text-left px-4 py-2">TF</th>
                  <th className="text-right px-4 py-2">Candles</th>
                  <th className="text-left px-4 py-2">Últ. Sync</th>
                  <th className="text-left px-4 py-2">Ações</th>
                </tr>
              </thead>
              <tbody>
                {data.map(row => {
                  const rowBusy = busyId === row.id
                  return (
                    <tr key={row.id} className="border-t border-border">
                      <td className="px-4 py-2 font-mono">{row.symbol}</td>
                      <td className="px-4 py-2">{row.timeframe}</td>
                      <td className="px-4 py-2 text-right">{Number(row.candle_count || 0).toLocaleString()}</td>
                      <td className="px-4 py-2 text-muted">{fmtDate(row.last_sync)}</td>
                      <td className="px-4 py-2">
                        <div className="flex items-center gap-2">
                          <button
                            disabled={rowBusy || syncMut.isPending}
                            onClick={() => {
                              setBusyId(row.id)
                              syncMut.mutate({ sym: row.symbol, tf: row.timeframe })
                            }}
                            className="px-2 py-1 rounded bg-accent/10 text-accent border border-accent/25 hover:bg-accent/20 disabled:opacity-50 inline-flex items-center gap-1"
                          >
                            <RefreshCw size={12} />
                            Sync
                          </button>
                          <button
                            disabled={rowBusy || delMut.isPending}
                            onClick={() => {
                              setBusyId(row.id)
                              delMut.mutate({ sym: row.symbol, tf: row.timeframe })
                            }}
                            className="px-2 py-1 rounded bg-red-500/10 text-red-300 border border-red-500/25 hover:bg-red-500/20 disabled:opacity-50 inline-flex items-center gap-1"
                          >
                            <Trash2 size={12} />
                            Remover
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="rounded-xl bg-panel border border-border overflow-hidden">
        <div className="px-4 py-3 border-b border-border text-sm font-semibold">Histórico de sync</div>
        {syncJobs.length === 0 && <div className="p-4 text-sm text-muted">Sem jobs recentes.</div>}
        {syncJobs.length > 0 && (
          <div className="overflow-auto">
            <table className="w-full text-sm">
              <thead className="bg-white/5 text-muted">
                <tr>
                  <th className="text-left px-4 py-2">Status</th>
                  <th className="text-left px-4 py-2">Símbolo</th>
                  <th className="text-left px-4 py-2">TF</th>
                  <th className="text-right px-4 py-2">Candles</th>
                  <th className="text-left px-4 py-2">Trigger</th>
                  <th className="text-left px-4 py-2">Início</th>
                  <th className="text-left px-4 py-2">Fim</th>
                </tr>
              </thead>
              <tbody>
                {syncJobs.slice(0, 40).map(job => (
                  <tr key={job.id} className="border-t border-border">
                    <td className="px-4 py-2">
                      <span className={`px-2 py-0.5 rounded text-xs font-bold border ${
                        job.status === 'success'
                          ? 'text-green-300 bg-green-500/10 border-green-500/30'
                          : job.status === 'failed'
                          ? 'text-red-300 bg-red-500/10 border-red-500/30'
                          : job.status === 'running'
                          ? 'text-blue-300 bg-blue-500/10 border-blue-500/30'
                          : 'text-yellow-300 bg-yellow-500/10 border-yellow-500/30'
                      }`}>
                        {job.status}
                      </span>
                    </td>
                    <td className="px-4 py-2 font-mono">{job.symbol}</td>
                    <td className="px-4 py-2">{job.timeframe || '-'}</td>
                    <td className="px-4 py-2 text-right">{job.candles_synced ?? '-'}</td>
                    <td className="px-4 py-2">{job.trigger}</td>
                    <td className="px-4 py-2 text-muted">{fmtDate(job.started_at || job.created_at)}</td>
                    <td className="px-4 py-2 text-muted">{fmtDate(job.finished_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  )
}
