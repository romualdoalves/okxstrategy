import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { X, AlertTriangle } from 'lucide-react'
import { getStrategyStudy } from '../api'

const VERDICT_STYLE = {
  candidate: 'border-bull/40 bg-bull/10 text-bull',
  watch:     'border-yellow-400/40 bg-yellow-400/10 text-yellow-300',
  retired:   'border-bear/40 bg-bear/10 text-bear',
  insufficient: 'border-border bg-white/5 text-muted',
  none:      'border-border bg-white/5 text-muted',
}

const money = (v) => `${v > 0 ? '+' : ''}${Number(v).toFixed(0)}`
const tone = (v) => (v > 0 ? 'text-bull' : v < 0 ? 'text-bear' : 'text-muted')
const short = (sym) => sym.replace('-USDT', '')

function Section({ title, children }) {
  return (
    <div className="mt-5">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-white/50 mb-2">{title}</p>
      {children}
    </div>
  )
}

function WalkForward({ data }) {
  const rows = Object.entries(data || {})
  if (rows.length === 0) return <p className="text-xs text-muted">Sem walk-forward para esta estratégia.</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-muted text-left">
            <th className="py-1 pr-3 font-normal">Ativo</th>
            <th className="pr-3 font-normal">PF fora da amostra</th>
            <th className="pr-3 font-normal">PF dos parâmetros atuais</th>
            <th className="pr-3 font-normal">Trades</th>
            <th className="pr-3 font-normal">Dobras +</th>
            <th className="font-normal">Planalto</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([sym, r]) => (
            <tr key={sym} className="border-t border-border/50">
              <td className="py-1 pr-3 font-medium">{short(sym)}</td>
              <td className="pr-3">{r.oos_pf.toFixed(2)}</td>
              <td className="pr-3">{r.current_pf.toFixed(2)}</td>
              <td className="pr-3">{r.oos_trades}</td>
              <td className="pr-3">{r.folds_positive}/{r.folds_total}</td>
              <td>{r.plateau ? 'sim' : 'não'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-[10px] text-muted">
        PF = fator de lucro (acima de 1 ganha). "Fora da amostra" = janelas que a busca de parâmetros não viu.
      </p>
    </div>
  )
}

function Baseline({ data }) {
  const rows = Object.entries(data || {})
  if (rows.length === 0) return null
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-muted text-left">
            <th className="py-1 pr-3 font-normal">Ativo</th>
            <th className="pr-3 font-normal">Trades/ano</th>
            <th className="pr-3 font-normal">PF</th>
            <th className="pr-3 font-normal">PF fora da amostra</th>
            <th className="pr-3 font-normal">Lucro (US$)</th>
            <th className="font-normal">Ativo parado (%)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([sym, r]) => (
            <tr key={sym} className="border-t border-border/50">
              <td className="py-1 pr-3 font-medium">{short(sym)}</td>
              <td className="pr-3">{r.trades_per_year}</td>
              <td className="pr-3">{r.pf.toFixed(2)}</td>
              <td className="pr-3">{r.oos_pf.toFixed(2)} <span className="text-muted">({r.oos_trades})</span></td>
              <td className={`pr-3 ${tone(r.profit_usd)}`}>{money(r.profit_usd)}</td>
              <td className={tone(r.buy_hold_pct)}>{r.buy_hold_pct > 0 ? '+' : ''}{r.buy_hold_pct}%</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-[10px] text-muted">
        Baseline com os parâmetros padrão. PF = fator de lucro (acima de 1 ganha). Entre parênteses: trades fora da amostra.
      </p>
    </div>
  )
}

function Yearly({ yearly, buyHold }) {
  const syms = Object.keys(yearly || {})
  if (syms.length === 0) return <p className="text-xs text-muted">Sem detalhamento por ano.</p>
  const years = [...new Set(syms.flatMap(s => Object.keys(yearly[s])))].sort()
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-muted text-left">
            <th className="py-1 pr-3 font-normal">Ano</th>
            {syms.map(s => <th key={s} className="pr-3 font-normal">{short(s)} estratégia (trades)</th>)}
            {syms.map(s => <th key={`b${s}`} className="pr-3 font-normal">{short(s)} ativo</th>)}
          </tr>
        </thead>
        <tbody>
          {years.map(y => (
            <tr key={y} className={`border-t border-border/50 ${y === '2022' ? 'bg-white/5' : ''}`}>
              <td className="py-1 pr-3 font-medium">{y}</td>
              {syms.map(s => {
                const cell = yearly[s][y]
                return cell
                  ? <td key={s} className={`pr-3 ${tone(cell[0])}`}>{money(cell[0])} <span className="text-muted">({cell[1]})</span></td>
                  : <td key={s} className="pr-3 text-muted">–</td>
              })}
              {syms.map(s => {
                const p = buyHold?.[s]?.[y]
                return p === undefined
                  ? <td key={`b${s}`} className="pr-3 text-muted">–</td>
                  : <td key={`b${s}`} className={`pr-3 ${tone(p)}`}>{p > 0 ? '+' : ''}{p}%</td>
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-[10px] text-muted">
        Lucro em US$ do backtest por ano da entrada. 2022 (linha destacada) foi o mercado de baixa.
      </p>
    </div>
  )
}

export default function StudyModal({ strategyId, onClose }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['strategy-study', strategyId],
    queryFn: () => getStrategyStudy(strategyId),
    staleTime: 5 * 60 * 1000,
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
      <div
        className="card w-full max-w-3xl max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="font-bold text-white/90">Estudo da estratégia {strategyId}</h3>
            {data?.generated_at && (
              <p className="text-[11px] text-muted">Timeframe {data.timeframe} · gerado em {data.generated_at}</p>
            )}
          </div>
          <button type="button" onClick={onClose} className="text-muted hover:text-white" aria-label="Fechar">
            <X size={18} />
          </button>
        </div>

        {isLoading && <p className="mt-4 text-xs text-muted">Carregando...</p>}
        {error && <p className="mt-4 text-xs text-bear">Não foi possível carregar o estudo.</p>}

        {data && (
          <>
            <div className={`mt-4 rounded-lg border p-3 ${VERDICT_STYLE[data.verdict] ?? VERDICT_STYLE.none}`}>
              <p className="text-xs font-bold uppercase">{data.verdict_label}</p>
              <p className="mt-1 text-[12px] text-white/80">{data.reason}</p>
            </div>

            {data.stale && (
              <div className="mt-3 flex items-start gap-2 rounded-lg border border-yellow-400/30 bg-yellow-400/10 p-3">
                <AlertTriangle size={14} className="mt-0.5 shrink-0 text-yellow-400" />
                <p className="text-[11px] text-yellow-100/85">
                  Resultados desatualizados: o código da estratégia mudou depois do estudo. {data.note}
                </p>
              </div>
            )}

            {data.verdict !== 'none' && (
              <>
                {data.verdict !== 'insufficient' && Object.keys(data.baseline || {}).length > 0 && (
                  <Section title="Baseline no histórico completo">
                    <Baseline data={data.baseline} />
                  </Section>
                )}
                {data.verdict !== 'insufficient' && Object.keys(data.baseline || {}).length === 0 && (
                  <>
                    <Section title="Walk-forward (fora da amostra)">
                      <WalkForward data={data.walkforward} />
                    </Section>
                    <Section title="Resultado por ano contra o ativo">
                      <Yearly yearly={data.yearly} buyHold={data.buy_hold} />
                    </Section>
                  </>
                )}
                {data.applied_params && Object.keys(data.applied_params).length > 0 && (
                  <Section title="Parâmetros aplicados após o estudo">
                    <p className="text-[12px] font-mono">
                      {Object.entries(data.applied_params).map(([k, v]) => `${k}=${v}`).join('  ')}
                    </p>
                  </Section>
                )}
                <Section title="Ressalvas">
                  <ul className="list-disc pl-4 space-y-1 text-[11px] text-white/60">
                    {data.caveats?.map((c, i) => <li key={i}>{c}</li>)}
                  </ul>
                </Section>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}
