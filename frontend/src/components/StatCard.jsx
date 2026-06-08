import React from 'react'

/**
 * rows?: Array<{ label: string, value: string, source?: 'okx'|'app', mismatch?: bool }>
 *
 * Quando `rows` é fornecido, substitui `sub` e exibe uma tabela compacta de reconciliação
 * com linha "OKX" (azul) e linha "App" (cinza). Ativa indicador ⚠ se mismatch=true.
 */
export default function StatCard({ label, value, sub, color = 'white', icon: Icon, rows }) {
  const colors = { white: 'text-white', bull: 'text-bull', bear: 'text-bear', accent: 'text-accent' }

  return (
    <div className="card flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="label">{label}</span>
        {Icon && <Icon size={15} className="text-muted" />}
      </div>

      <span className={`text-2xl font-bold leading-none ${colors[color] ?? 'text-white'}`}>
        {value}
      </span>

      {rows ? (
        <div className="mt-0.5 pt-1.5 border-t border-white/5 space-y-[3px]">
          {rows.map((row, i) => {
            const isExchange = row.source === 'okx'
            return (
              <div
                key={i}
                className={`flex items-center justify-between gap-1 px-1 rounded ${
                  row.mismatch ? 'bg-yellow-400/5' : ''
                }`}
              >
                <span className={`text-[9px] font-bold uppercase tracking-wider shrink-0 ${
                  isExchange ? 'text-blue-400/80' : 'text-white/30'
                }`}>
                  {row.label}
                </span>
                <div className="flex items-center gap-1 min-w-0">
                  <span className={`text-[10px] font-mono leading-none ${
                    row.color ?? (isExchange ? 'text-white/80' : 'text-white/45')
                  }`}>
                    {row.value ?? '—'}
                  </span>
                  {row.mismatch && (
                    <span
                      className="text-yellow-400 text-[9px] leading-none shrink-0"
                      title="Discrepância entre OKX e App"
                    >⚠</span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        sub && <div className="text-xs text-muted">{sub}</div>
      )}
    </div>
  )
}
