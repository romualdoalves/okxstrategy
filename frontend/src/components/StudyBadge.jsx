import React, { useState } from 'react'
import { Info } from 'lucide-react'
import StudyModal from './StudyModal'

const BADGE_STYLE = {
  candidate: 'bg-bull/15 text-bull',
  watch:     'bg-yellow-400/15 text-yellow-300',
  retired:   'bg-bear/15 text-bear',
  insufficient: 'bg-border text-muted',
  none:      'bg-border text-muted',
}

// Etiqueta de veredito do estudo + botão (i) que abre o quadro completo.
export default function StudyBadge({ strategyId, verdict = 'none', label = 'Sem estudo', stale = false }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <span className="inline-flex items-center gap-1">
        <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${BADGE_STYLE[verdict] ?? BADGE_STYLE.none}`}>
          {label}{stale ? ' (desatualizado)' : ''}
        </span>
        <button
          type="button"
          title="Resultados do estudo desta estratégia"
          aria-label="Resultados do estudo desta estratégia"
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen(true) }}
          className="text-muted hover:text-accent transition-colors"
        >
          <Info size={14} />
        </button>
      </span>
      {open && <StudyModal strategyId={strategyId} onClose={() => setOpen(false)} />}
    </>
  )
}
