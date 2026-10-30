import React, { useState, useEffect } from 'react'

function fmtLeft(ms) {
  const totalMin = Math.max(0, Math.floor(ms / 60000))
  return `${Math.floor(totalMin / 60)} h ${String(totalMin % 60).padStart(2, '0')} min`
}

const signed = n => `${n > 0 ? '+' : ''}${Number(n).toFixed(2)}%`
const tone = n => (n > 0 ? 'text-bull' : n < 0 ? 'text-bear' : 'text-white')

// Modo "Meta 1% Bruto (24 h)": a régua é o ganho BRUTO da sessão; o LÍQUIDO (depois das taxas) aparece sempre ao lado.
// Ao terminar mostra o resultado final dos dois e avisa se a meta bruta foi batida mas o líquido ficou <= 0 (ilusão).
export default function GoalLine({ goal }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(id)
  }, [])
  const deadline = goal.deadline ? Date.parse(goal.deadline) : null
  const left = deadline ? deadline - now : null
  const ended = goal.done
  const gross = ended && goal.result_gross_pct != null ? goal.result_gross_pct : goal.session_gross_pct
  const net = ended && goal.result_net_pct != null ? goal.result_net_pct : goal.session_net_pct
  const progress = gross == null ? 0 : Math.max(0, Math.min(100, (gross / goal.target_pct) * 100))
  const reached = goal.outcome === 'reached'

  let status
  if (ended) status = goal.outcome_label || 'Meta encerrada'
  else if (!goal.started_at) status = `Janela de ${goal.window_hours} h começa ao clicar em Iniciar`
  else if (left != null && left <= 0) status = goal.in_position
    ? 'Prazo esgotado · posição aberta: só sai por meta ou stop loss'
    : 'Prazo esgotado'
  else status = `restam ${fmtLeft(left)} de ${goal.window_hours} h`

  return (
    <div className={`mb-3 rounded-lg border p-2.5 text-[11px] ${
      goal.illusory ? 'border-yellow-500/40 bg-yellow-500/10'
        : reached ? 'border-green-500/40 bg-green-500/10' : ended ? 'border-white/10 bg-white/5' : 'border-accent/25 bg-accent/5'}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="font-bold uppercase tracking-wider text-[10px] text-muted">Meta {goal.target_pct}% bruto</span>
        <span className={`font-mono font-bold ${gross == null ? 'text-white' : tone(gross)}`}>
          {gross == null ? '—' : signed(gross)} / {goal.target_pct}%
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/10">
        <div className={`h-full rounded-full ${goal.illusory ? 'bg-yellow-400' : reached ? 'bg-green-400' : 'bg-accent'}`} style={{ width: `${progress}%` }} />
      </div>
      <p className="mt-1.5 flex items-center justify-between gap-2">
        <span className={reached && !goal.illusory ? 'font-bold text-green-300' : 'text-muted'}>{status}</span>
        <span className="font-mono text-muted">
          líquido <span className={net == null ? '' : tone(net)}>{net == null ? '—' : signed(net)}</span>
        </span>
      </p>
      {!ended && goal.in_position && goal.open_gross_pct != null && (
        <p className="text-muted/70">
          Posição aberta: {signed(goal.open_gross_pct)} bruto · {signed(goal.open_net_pct)} líquido agora.
        </p>
      )}
      {!ended && goal.net_equivalent_pct != null && (
        <p className="text-muted/60">Bater +{goal.target_pct}% bruto numa operação deixa ≈ {signed(goal.net_equivalent_pct)} líquido.</p>
      )}
      {goal.illusory && (
        <p className="mt-1 font-semibold text-yellow-300">
          Ilusão: a meta bruta foi batida, mas as taxas consumiram o ganho (líquido {signed(net)}).
        </p>
      )}
    </div>
  )
}
