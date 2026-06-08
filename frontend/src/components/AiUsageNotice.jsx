import React from 'react'
import { AlertTriangle, CheckCircle2 } from 'lucide-react'

export default function AiUsageNotice({ consumesTokens, text, className = '' }) {
  const tone = consumesTokens
    ? 'bg-amber-500/10 border-amber-500/30 text-amber-300'
    : 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'

  return (
    <div className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs ${tone} ${className}`.trim()}>
      {consumesTokens ? <AlertTriangle size={13} className="shrink-0" /> : <CheckCircle2 size={13} className="shrink-0" />}
      <span>{text}</span>
    </div>
  )
}
