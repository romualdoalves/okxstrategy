const ZONE_CFG = {
  'NÃO INICIAR': { text: 'text-red-400',    badge: 'bg-red-500/20 text-red-300 border border-red-500/30' },
  CUIDADO:       { text: 'text-yellow-400', badge: 'bg-yellow-500/20 text-yellow-300 border border-yellow-500/30' },
  INICIAR:       { text: 'text-green-400',  badge: 'bg-green-500/20 text-green-300 border border-green-500/30' },
}

export default function BacktestLikert({ score }) {
  const s = typeof score === 'number' ? score : 0
  const pct = Math.max(0, Math.min(100, (s / 10) * 100))
  const zone = s >= 6.67 ? 'INICIAR' : s >= 3.33 ? 'CUIDADO' : 'NÃO INICIAR'
  const zc = ZONE_CFG[zone]

  return (
    <div className="my-2 select-none">
      {/* Header: label + score + badge */}
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] text-muted uppercase tracking-wider">Escala de recomendação</span>
        <div className="flex items-center gap-1.5">
          <span className={`text-base font-bold font-mono leading-none ${zc.text}`}>
            {s.toFixed(2)}
          </span>
          <span className="text-xs text-muted">/10</span>
          <span className={`ml-1 px-2 py-0.5 rounded-full text-xs font-bold ${zc.badge}`}>
            {zone}
          </span>
        </div>
      </div>

      {/* Track */}
      <div className="relative h-3.5 rounded-full overflow-visible">
        {/* Zone backgrounds */}
        <div className="absolute inset-0 flex rounded-full overflow-hidden">
          <div className="flex-[33.33] bg-red-500/25" />
          <div className="flex-[33.33] bg-yellow-500/25" />
          <div className="flex-[33.34] bg-green-500/25" />
        </div>
        {/* Zone dividers */}
        <div className="absolute top-0 bottom-0 w-px bg-white/15" style={{ left: '33.33%' }} />
        <div className="absolute top-0 bottom-0 w-px bg-white/15" style={{ left: '66.67%' }} />
        {/* Marker */}
        <div
          className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-4 h-4 rounded-full bg-white border-2 border-white/80 shadow-[0_0_8px_rgba(255,255,255,0.6)] z-10"
          style={{ left: `${pct}%` }}
        />
      </div>

      {/* Zone labels */}
      <div className="flex mt-1.5">
        <div className="flex-[33.33] text-[9px] text-center text-red-400/55 font-medium">NÃO INICIAR</div>
        <div className="flex-[33.33] text-[9px] text-center text-yellow-400/55 font-medium">CUIDADO</div>
        <div className="flex-[33.34] text-[9px] text-center text-green-400/55 font-medium">INICIAR</div>
      </div>
      <div className="flex justify-between mt-0.5 px-0.5">
        <span className="text-[8px] text-muted/50 font-mono">0</span>
        <span className="text-[8px] text-muted/50 font-mono">10</span>
      </div>
    </div>
  )
}
