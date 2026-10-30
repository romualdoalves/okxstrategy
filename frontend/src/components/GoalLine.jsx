import React, { useState, useEffect } from 'react'

function fmtLeft(ms) {
  const totalMin = Math.max(0, Math.floor(ms / 60000))
  return `${Math.floor(totalMin / 60)} h ${String(totalMin % 60).padStart(2, '0')} min`
}

// Modo "Meta 1% Hoje!": alvo líquido da sessão, progresso, tempo restante e como terminou.
export default function GoalLine({ goal }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(id)
  }, [])
  const deadline = goal.deadline ? Date.parse(goal.deadline) : null
  const left = deadline ? deadline - now : null
  const session = goal.session_net_pct
  const progress = session == null ? 0 : Math.max(0, Math.min(100, (session / goal.target_pct) * 100))
  const reached = goal.outcome === 'reached'
  const ended = goal.done

  let status
  if (ended) status = goal.outcome_label || 'Meta encerrada'
  else if (!goal.started_at) status = `Janela de ${goal.window_hours} h começa ao clicar em Iniciar`
  else if (left != null && left <= 0) status = goal.in_position
    ? 'Prazo esgotado · posição aberta: só sai por meta ou stop loss'
    : 'Prazo esgotado'
  else status = `restam ${fmtLeft(left)} de ${goal.window_hours} h`

  return (
    <div className={`mb-3 rounded-lg border p-2.5 text-[11px] ${
      reached ? 'border-green-500/40 bg-green-500/10' : ended ? 'border-white/10 bg-white/5' : 'border-accent/25 bg-accent/5'}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="font-bold uppercase tracking-wider text-[10px] text-muted">Meta {goal.target_pct}% líquido</span>
        <span className={`font-mono font-bold ${session > 0 ? 'text-bull' : session < 0 ? 'text-bear' : 'text-white'}`}>
          {session == null ? '—' : `${session > 0 ? '+' : ''}${session.toFixed(2)}%`} / {goal.target_pct}%
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/10">
        <div className={`h-full rounded-full ${reached ? 'bg-green-400' : 'bg-accent'}`} style={{ width: `${progress}%` }} />
      </div>
      <p className={`mt-1.5 ${reached ? 'font-bold text-green-300' : 'text-muted'}`}>{status}</p>
      {!ended && goal.in_position && goal.open_net_pct != null && (
        <p className="text-muted/70">Posição aberta: {goal.open_net_pct > 0 ? '+' : ''}{goal.open_net_pct.toFixed(2)}% líquido agora.</p>
      )}
    </div>
  )
}
