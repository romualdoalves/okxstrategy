import { useEffect, useState } from 'react'
import { Activity, TrendingUp, TrendingDown, Minus, AlertTriangle, Clock } from 'lucide-react'

const API = import.meta.env.VITE_API_URL || ''

export default function Monitor() {
  const [bots, setBots] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [lastUpdate, setLastUpdate] = useState(null)

  const fetchData = async () => {
    try {
      const res = await fetch(`${API}/api/monitor`)
      if (!res.ok) throw new Error('Falha ao carregar')
      const data = await res.json()
      setBots(data.bots || [])
      setLastUpdate(data.timestamp)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 5000) // atualiza a cada 5s
    return () => clearInterval(interval)
  }, [])

  const totalPnl = bots.reduce((sum, b) => sum + (b.pnl_usd || 0), 0)
  const openPositions = bots.filter(b => b.direction !== 'FLAT')
  const haltedBots = bots.filter(b => b.halted)
  const syncAlerts = bots.filter(b => b.sync_status === 'divergent' || b.sync_status === 'error')

  const directionColor = (dir) => {
    if (dir === 'LONG') return 'text-green-400'
    if (dir === 'SHORT') return 'text-red-400'
    return 'text-gray-400'
  }

  const directionIcon = (dir) => {
    if (dir === 'LONG') return <TrendingUp size={14} className="text-green-400" />
    if (dir === 'SHORT') return <TrendingDown size={14} className="text-red-400" />
    return <Minus size={14} className="text-gray-400" />
  }

  const pnlColor = (pnl) => {
    if (pnl > 0) return 'text-green-400'
    if (pnl < 0) return 'text-red-400'
    return 'text-gray-400'
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="animate-spin mr-2"><Activity size={20} /></div>
        Carregando monitoramento...
      </div>
    )
  }

  return (
    <div className="p-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Activity size={24} className="text-blue-400" />
          Monitoramento em Tempo Real
        </h1>
        <div className="flex items-center gap-4 text-sm text-muted">
          <span className="flex items-center gap-1">
            <Clock size={14} />
            {lastUpdate ? new Date(lastUpdate).toLocaleTimeString('pt-BR') : '—'}
          </span>
          <span className="px-2 py-1 rounded bg-blue-500/20 text-blue-400">
            {bots.length} bots
          </span>
          <span className="px-2 py-1 rounded bg-green-500/20 text-green-400">
            {openPositions.length} posições
          </span>
          {haltedBots.length > 0 && (
            <span className="px-2 py-1 rounded bg-red-500/20 text-red-400 flex items-center gap-1">
              <AlertTriangle size={12} />
              {haltedBots.length} haltados
            </span>
          )}
          {syncAlerts.length > 0 && (
            <span className="px-2 py-1 rounded bg-red-500/20 text-red-400 flex items-center gap-1">
              <AlertTriangle size={12} />
              {syncAlerts.length} divergência OKX
            </span>
          )}
        </div>
      </div>

      {syncAlerts.length > 0 && (
        <div className="mb-4 rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
          <div className="font-semibold flex items-center gap-2 mb-2">
            <AlertTriangle size={16} />
            Divergência App x OKX detectada
          </div>
          <div className="space-y-1">
            {syncAlerts.map(bot => (
              <div key={bot.bot_id}>
                <span className="font-semibold text-white">{bot.name}</span>: {bot.sync_detail}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Resumo */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <div className="card p-4">
          <p className="text-xs text-muted uppercase">PnL Total em Aberto</p>
          <p className={`text-2xl font-bold ${pnlColor(totalPnl)}`}>
            {totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(2)}
          </p>
        </div>
        <div className="card p-4">
          <p className="text-xs text-muted uppercase">Posições Long</p>
          <p className="text-2xl font-bold text-green-400">
            {bots.filter(b => b.direction === 'LONG').length}
          </p>
        </div>
        <div className="card p-4">
          <p className="text-xs text-muted uppercase">Posições Short</p>
          <p className="text-2xl font-bold text-red-400">
            {bots.filter(b => b.direction === 'SHORT').length}
          </p>
        </div>
        <div className="card p-4">
          <p className="text-xs text-muted uppercase">Aguardando Sinal</p>
          <p className="text-2xl font-bold text-gray-400">
            {bots.filter(b => b.direction === 'FLAT').length}
          </p>
        </div>
      </div>

      {/* Tabela de Bots */}
      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/10 text-left text-muted text-xs uppercase">
              <th className="p-3">Bot</th>
              <th className="p-3">Par</th>
              <th className="p-3">Estratégia</th>
              <th className="p-3">Direção</th>
              <th className="p-3">Entrada</th>
              <th className="p-3">Atual</th>
              <th className="p-3">Stop</th>
              <th className="p-3">TP1</th>
              <th className="p-3">PnL %</th>
              <th className="p-3">PnL $</th>
              <th className="p-3">App/OKX</th>
              <th className="p-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {bots.map((bot) => (
              <tr key={bot.bot_id} className="border-b border-white/5 hover:bg-white/5 transition-colors">
                <td className="p-3">
                  <div className="font-medium">{bot.name}</div>
                  <div className="text-xs text-muted">ID: {bot.bot_id}</div>
                </td>
                <td className="p-3">
                  <span className="px-2 py-1 rounded bg-white/10 text-xs">{bot.symbol}</span>
                  <div className="text-xs text-muted mt-1">{bot.timeframe}</div>
                </td>
                <td className="p-3">
                  <span className="px-2 py-1 rounded bg-blue-500/20 text-blue-400 text-xs">
                    {bot.strategy_id}
                  </span>
                </td>
                <td className="p-3">
                  <div className={`flex items-center gap-1 font-medium ${directionColor(bot.direction)}`}>
                    {directionIcon(bot.direction)}
                    {bot.direction}
                  </div>
                </td>
                <td className="p-3 font-mono text-xs">
                  {bot.entry_price ? `$${bot.entry_price}` : '—'}
                </td>
                <td className="p-3 font-mono text-xs">
                  {bot.last_price ? `$${bot.last_price}` : '—'}
                </td>
                <td className="p-3 font-mono text-xs text-red-400">
                  {bot.sl_price ? `$${bot.sl_price}` : '—'}
                </td>
                <td className="p-3 font-mono text-xs text-green-400">
                  {bot.tp1_price ? `$${bot.tp1_price}` : '—'}
                  {bot.tp1_done && <span className="ml-1 text-[10px]">✓</span>}
                </td>
                <td className={`p-3 font-mono font-bold ${pnlColor(bot.pnl_pct)}`}>
                  {bot.pnl_pct ? `${bot.pnl_pct >= 0 ? '+' : ''}${bot.pnl_pct.toFixed(2)}%` : '—'}
                </td>
                <td className={`p-3 font-mono font-bold ${pnlColor(bot.pnl_usd)}`}>
                  {bot.pnl_usd ? `${bot.pnl_usd >= 0 ? '+' : ''}$${bot.pnl_usd.toFixed(2)}` : '—'}
                </td>
                <td className="p-3">
                  {bot.sync_status === 'ok' ? (
                    <span title={bot.sync_detail} className="px-2 py-1 rounded bg-green-500/20 text-green-400 text-xs">
                      Sincronizado
                    </span>
                  ) : bot.sync_status === 'divergent' ? (
                    <span title={bot.sync_detail} className="px-2 py-1 rounded bg-red-500/20 text-red-400 text-xs flex items-center gap-1 w-fit">
                      <AlertTriangle size={10} /> Divergente
                    </span>
                  ) : bot.sync_status === 'error' ? (
                    <span title={bot.sync_detail} className="px-2 py-1 rounded bg-yellow-500/20 text-yellow-400 text-xs">
                      Erro OKX
                    </span>
                  ) : (
                    <span className="px-2 py-1 rounded bg-white/10 text-muted text-xs">Verificando</span>
                  )}
                  <div className="text-[10px] text-muted mt-1 font-mono">
                    OKX {bot.okx_direction || '—'} {bot.okx_size ? bot.okx_size : ''}
                  </div>
                </td>
                <td className="p-3">
                  {bot.halted ? (
                    <span className="px-2 py-1 rounded bg-red-500/20 text-red-400 text-xs flex items-center gap-1">
                      <AlertTriangle size={10} /> HALT
                    </span>
                  ) : bot.direction !== 'FLAT' ? (
                    <span className="px-2 py-1 rounded bg-green-500/20 text-green-400 text-xs">
                      Ativo
                    </span>
                  ) : (
                    <span className="px-2 py-1 rounded bg-yellow-500/20 text-yellow-400 text-xs">
                      {bot.hold_reason || 'Aguardando'}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {error && (
        <div className="mt-4 p-4 rounded bg-red-500/20 text-red-400 text-sm">
          Erro: {error}
        </div>
      )}
    </div>
  )
}
