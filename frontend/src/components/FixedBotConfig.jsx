import React from 'react'
import { Lock, AlertTriangle } from 'lucide-react'

// Configuração do bot que o usuário NÃO edita: o servidor ignora qualquer valor enviado. Mostrar o que será usado é
// transparência; deixar editar prejudicaria a execução (timeframe fora do validado, parâmetros ruins, stop sem critério).
export default function FixedBotConfig({ timeframe, validatedTimeframe }) {
  const differs = Boolean(timeframe && validatedTimeframe && timeframe !== validatedTimeframe)
  const items = [
    ['Timeframe', timeframe || '—', 'o validado pelo estudo da estratégia'],
    ['Parâmetros', 'Padrão', 'os padrões validados da estratégia'],
    ['Stake por operação', 'US$ 100', 'fixo'],
    ['Stop diário', 'US$ −50', 'limite de perda do dia, fixo'],
    ['Alavancagem', '1x (spot)', 'o app só opera spot'],
  ]
  return (
    <div className="card space-y-3">
      <h2 className="flex items-center gap-2 text-sm font-semibold"><Lock size={14} className="text-muted" /> Configuração fixa</h2>
      <p className="text-xs leading-relaxed text-muted">
        Timeframe, parâmetros e stop não são configuráveis: foram validados pelo estudo da estratégia, e alterá-los
        poderia prejudicar a execução do bot. O servidor ignora qualquer outro valor.
      </p>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
        {items.map(([label, value, hint]) => (
          <div key={label} className="flex items-baseline justify-between gap-3 border-b border-white/5 pb-1.5">
            <dt className="text-xs text-muted">{label} <span className="text-[10px] text-muted/60">· {hint}</span></dt>
            <dd className="font-mono text-sm font-semibold text-white">{value}</dd>
          </div>
        ))}
      </dl>
      {differs && (
        <p className="flex items-start gap-2 rounded-lg border border-yellow-500/30 bg-yellow-500/10 p-2.5 text-xs text-yellow-200">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          Este bot usa o timeframe {timeframe}, mas o validado para esta estratégia é {validatedTimeframe}. Fora do validado,
          o resultado não tem respaldo do estudo.
        </p>
      )}
    </div>
  )
}
