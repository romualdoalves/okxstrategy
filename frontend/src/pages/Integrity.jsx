import { useEffect, useState } from 'react'
import { ShieldCheck, ShieldAlert, ShieldQuestion, RefreshCw } from 'lucide-react'
import { getIntegrityCheck } from '../api'

const STATUS_STYLE = {
  ok:    { badge: 'bg-bull/15 text-bull border-bull/30',   dot: 'bg-bull' },
  warn:  { badge: 'bg-yellow-400/15 text-yellow-400 border-yellow-400/30', dot: 'bg-yellow-400' },
  fail:  { badge: 'bg-bear/15 text-bear border-bear/30',   dot: 'bg-bear' },
  error: { badge: 'bg-bear/15 text-bear border-bear/30',   dot: 'bg-bear' },
}

const OVERALL_LABEL = {
  ok:    'OK',
  warn:  'ATENÇÃO',
  fail:  'DIVERGENTE',
  error: 'ERRO',
}

export default function Integrity() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await getIntegrityCheck()
      setData(res)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchData() }, [])

  const bots = data?.bots ?? []
  const okCount   = bots.filter(b => b.overall === 'ok').length
  const failCount = bots.filter(b => b.overall === 'fail' || b.overall === 'error').length
  const warnCount = bots.filter(b => b.overall === 'warn').length

  const OverallIcon = ({ overall }) => {
    if (overall === 'ok')   return <ShieldCheck size={18} className="text-bull" />
    if (overall === 'warn') return <ShieldQuestion size={18} className="text-yellow-400" />
    return <ShieldAlert size={18} className="text-bear" />
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <ShieldCheck size={22} className="text-accent" />
            Verificação de Integridade
          </h1>
          <p className="text-sm text-muted mt-1">
            Compara, agora, o estado que o app acredita ter para cada bot contra o estado
            real na OKX — posição, tamanho e proteção (SL/Trailing) ativa.
          </p>
        </div>
        <button
          onClick={fetchData}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent/15 text-accent hover:bg-accent/25 transition-colors text-sm font-medium disabled:opacity-50"
        >
          <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
          {loading ? 'Verificando...' : 'Verificar Agora'}
        </button>
      </div>

      {error && (
        <div className="bg-bear/10 border border-bear/30 rounded-xl p-4 text-sm text-bear">
          Falha ao verificar: {error}
        </div>
      )}

      {data && (
        <>
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-black/20 rounded-xl p-4 border border-white/5">
              <p className="text-xs text-muted uppercase">Consistentes</p>
              <p className="text-2xl font-bold text-bull mt-1">{okCount}</p>
            </div>
            <div className="bg-black/20 rounded-xl p-4 border border-white/5">
              <p className="text-xs text-muted uppercase">Atenção</p>
              <p className="text-2xl font-bold text-yellow-400 mt-1">{warnCount}</p>
            </div>
            <div className="bg-black/20 rounded-xl p-4 border border-white/5">
              <p className="text-xs text-muted uppercase">Divergentes</p>
              <p className="text-2xl font-bold text-bear mt-1">{failCount}</p>
            </div>
          </div>

          <p className="text-xs text-muted">
            Última verificação: {data.checked_at ? new Date(data.checked_at).toLocaleString() : '—'}
          </p>

          <div className="space-y-3">
            {bots.map((bot) => (
              <div key={bot.bot_id} className="bg-black/20 rounded-xl border border-white/5 overflow-hidden">
                <div className="flex items-center justify-between px-4 py-3 border-b border-white/5">
                  <div className="flex items-center gap-3">
                    <OverallIcon overall={bot.overall} />
                    <div>
                      <p className="text-sm font-semibold text-white">{bot.name}</p>
                      <p className="text-xs text-muted">{bot.symbol}</p>
                    </div>
                  </div>
                  <span className={`text-xs font-bold uppercase px-2.5 py-1 rounded-full border ${STATUS_STYLE[bot.overall]?.badge ?? ''}`}>
                    {OVERALL_LABEL[bot.overall] ?? bot.overall}
                  </span>
                </div>
                <div className="divide-y divide-white/5">
                  {bot.checks.map((c, i) => (
                    <div key={i} className="flex items-start gap-3 px-4 py-2.5">
                      <span className={`mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 ${STATUS_STYLE[c.status]?.dot ?? 'bg-white/30'}`} />
                      <div className="min-w-0">
                        <p className="text-xs font-semibold text-white/80">{c.label}</p>
                        <p className="text-xs text-muted break-words">{c.detail}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
            {bots.length === 0 && (
              <p className="text-sm text-muted">Nenhum bot configurado.</p>
            )}
          </div>
        </>
      )}
    </div>
  )
}
