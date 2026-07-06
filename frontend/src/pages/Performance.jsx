import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Trophy, TrendingUp, ArrowRight, Activity, Percent, ExternalLink } from 'lucide-react'
import { getPerformanceRanking, getActivePerformanceRanking, syncTrailingStops } from '../api'
import { useLanguage } from '../i18n/LanguageContext'
import { useState } from 'react'

export default function Performance() {
  const { t } = useLanguage()
  const [syncing, setSyncing] = useState(false)

  // Ranking Histórico (Realizado)
  const { data: historical = [], refetch: refetchHist } = useQuery({ 
    queryKey: ['performance-ranking-hist'], 
    queryFn: getPerformanceRanking,
    refetchInterval: 60000 
  })

  // Ranking Ativo (PnL Aberto)
  const { data: active = [], refetch: refetchActive } = useQuery({ 
    queryKey: ['performance-ranking-active'], 
    queryFn: getActivePerformanceRanking,
    refetchInterval: 15000 // Aumentado para 15s conforme solicitado (sync agora é manual/candle)
  })

  const handleSync = async () => {
    setSyncing(true)
    try {
      await syncTrailingStops()
      await Promise.all([refetchHist(), refetchActive()])
    } catch (err) {
      console.error("Sync failed", err)
    } finally {
      setSyncing(false)
    }
  }

  return (
    <div className="p-6 space-y-10">
      <div>
        <div className="flex items-center gap-3 mb-1">
          <Trophy className="text-accent w-6 h-6" />
          <h1 className="text-2xl font-bold">{t('perf.title')}</h1>
        </div>
        <p className="text-muted text-sm">{t('perf.subtitle')}</p>
      </div>

      {/* SEÇÃO: BOTS EM EXECUÇÃO */}
      <section className="space-y-4">
        <div className="flex items-center justify-between pb-2 border-b border-white/5">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <Activity className="w-5 h-5 text-bull" />
              <h2 className="text-lg font-semibold">{t('perf.active_bots')}</h2>
            </div>
            
            <button 
              onClick={handleSync}
              disabled={syncing}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-[11px] font-bold transition-all ${
                syncing 
                ? 'bg-accent/10 text-accent/50 cursor-wait' 
                : 'bg-accent/10 text-accent hover:bg-accent/20 border border-accent/20'
              }`}
            >
              <Activity className={`w-3 h-3 ${syncing ? 'animate-spin' : ''}`} />
              {syncing ? 'Sincronizando...' : 'Buscar Lucro Garantido na API'}
            </button>
          </div>
          <span className="flex items-center gap-1.5 bg-bull/10 text-bull text-[10px] font-bold px-2.5 py-1 rounded-full border border-bull/20">
            <span className="w-1.5 h-1.5 bg-bull rounded-full animate-pulse" />
            LIVE
          </span>
        </div>

        {active.length === 0 ? (
          <div className="bg-white/5 border border-white/5 rounded-xl p-8 text-center text-muted text-sm italic">
            Nenhum robô com operação aberta no momento.
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4">
            {active.map((item, index) => (
              <div key={item.id} className="bg-white/[0.03] border border-white/5 rounded-xl p-4 flex items-center justify-between hover:bg-white/[0.05] transition-all">
                <div className="flex items-center gap-4">
                  <div className="w-10 h-10 rounded-full bg-bull/10 flex items-center justify-center text-bull font-bold text-sm">
                    {index + 1}
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-mono text-muted bg-white/5 px-1.5 rounded">{item.strategy_id}</span>
                      <h3 className="font-bold text-white">{item.name}</h3>
                    </div>
                    <div className="flex items-center gap-2 mt-1">
                      <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-white/5 text-muted">{item.symbol}</span>
                      <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${item.direction === 'LONG' ? 'bg-bull/20 text-bull' : 'bg-bear/20 text-bear'}`}>
                        {item.direction}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-12">
                  <div className="text-right">
                    <p className="text-[10px] text-muted uppercase font-bold mb-1 opacity-60">Entrada / Atual</p>
                    <div className="flex items-center gap-2 font-mono text-sm">
                      <span className="text-muted">${item.entry_price}</span>
                      <ArrowRight className="w-3 h-3 text-muted" />
                      <span className="text-white font-bold">${item.last_price}</span>
                    </div>
                  </div>

                  <div className="text-right min-w-[120px]">
                    <p className="text-[10px] text-muted uppercase font-bold mb-1 opacity-60">{t('perf.open_pnl')}</p>
                    <div className={`text-xl font-bold leading-none ${item.pnl_usd >= 0 ? 'text-bull' : 'text-bear'}`}>
                      {item.pnl_usd >= 0 ? '+' : ''}${item.pnl_usd.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </div>
                    <div className={`text-[11px] font-bold mt-0.5 ${item.pnl_pct >= 0 ? 'text-bull' : 'text-bear'}`}>
                      {item.pnl_pct >= 0 ? '+' : ''}{item.pnl_pct}%
                    </div>
                  </div>

                  <div className="text-right min-w-[120px]">
                    <p className="text-[10px] text-muted uppercase font-bold mb-1 opacity-60 flex items-center justify-end gap-1">
                      <Percent className="w-2 h-2" />
                      {t('perf.guaranteed')}
                    </p>
                    <div className={`text-lg font-bold leading-none ${item.guaranteed_pnl_usd > 0 ? 'text-bull' : 'text-muted'}`}>
                      {item.guaranteed_pnl_usd > 0 ? '+' : ''}${item.guaranteed_pnl_usd.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </div>
                    <div className="flex flex-col items-end gap-0.5 mt-1">
                      <p className="text-[9px] text-muted font-mono">
                        Stop: <span className="text-white">${item.sl_price?.toFixed(4)}</span>
                      </p>
                      <p className="text-[9px] text-muted font-mono">
                        Gatilho: <span className="text-accent">{item.sts_trigger}%</span>
                      </p>
                    </div>
                  </div>

                  <a href={`/bots/${item.id}`} className="p-2.5 bg-white/5 hover:bg-white/10 text-white rounded-lg border border-white/5 transition-all">
                    <ExternalLink className="w-4 h-4" />
                  </a>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* SEÇÃO: RANKING HISTÓRICO */}
      <section className="space-y-4">
        <div className="flex items-center gap-2 pb-2 border-b border-white/5">
          <TrendingUp className="w-5 h-5 text-accent" />
          <h2 className="text-lg font-semibold">{t('perf.historical')}</h2>
        </div>

        {historical.length === 0 ? (
          <div className="bg-white/5 border border-white/5 rounded-xl p-8 text-center text-muted text-sm italic">
            Nenhum trade finalizado registrado ainda.
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3">
            {historical.map((item, index) => (
              <div key={item.id} className="bg-white/[0.03] border border-white/5 rounded-xl p-4 flex items-center justify-between group hover:border-accent/30 transition-all">
                <div className="flex items-center gap-4">
                  <div className={`w-10 h-10 rounded-full flex items-center justify-center font-bold text-lg ${index === 0 ? 'bg-accent/20 text-accent border border-accent/30 shadow-[0_0_15px_rgba(234,179,8,0.1)]' : 'bg-white/5 text-muted'}`}>
                    {index === 0 ? <Trophy className="w-5 h-5" /> : index + 1}
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-[10px] font-mono text-muted bg-white/5 px-1.5 rounded">{item.strategy_id}</span>
                      <h3 className="font-bold text-white group-hover:text-accent transition-colors">{item.name}</h3>
                    </div>
                    <p className="text-xs text-muted mt-0.5">{item.symbol}</p>
                  </div>
                </div>

                <div className="flex items-center gap-10">
                  <div className="text-right">
                    <p className="text-[10px] text-muted uppercase font-bold mb-1 opacity-60">{t('perf.pnl')} (líq.)</p>
                    <div className={`text-lg font-bold ${item.pnl >= 0 ? 'text-bull' : 'text-bear'}`}>
                      {item.pnl >= 0 ? '+' : ''}${item.pnl.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                    </div>
                    {item.fees > 0 && (
                      <p className="text-[10px] text-muted mt-0.5">
                        bruto {item.pnl_gross >= 0 ? '+' : ''}${item.pnl_gross.toFixed(2)} · taxas -${item.fees.toFixed(4)}
                      </p>
                    )}
                  </div>

                  <div className="text-right">
                    <p className="text-[10px] text-muted uppercase font-bold mb-1 opacity-60">{t('perf.trades')}</p>
                    <div className="text-lg font-bold text-white">{item.trades}</div>
                  </div>

                  <div className="text-right w-32">
                    <p className="text-[10px] text-muted uppercase font-bold mb-1 opacity-60">{t('perf.win_rate')}</p>
                    <div className="flex items-center gap-2">
                      <div className="flex-1 h-1.5 bg-white/5 rounded-full overflow-hidden">
                        <div className="h-full bg-accent transition-all duration-1000" style={{ width: `${item.win_rate}%` }} />
                      </div>
                      <span className="text-sm font-bold text-accent min-w-[45px]">{item.win_rate}%</span>
                    </div>
                  </div>

                  <a href={`/bots/${item.id}/report`} className="btn bg-white/5 hover:bg-white/10 text-white text-xs px-4 py-2 rounded-lg border border-white/5 transition-all">
                    Detalhes
                  </a>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
