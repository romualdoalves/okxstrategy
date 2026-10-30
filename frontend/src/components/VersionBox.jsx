import React from 'react'
import { GitCommit, CheckCircle2, AlertTriangle } from 'lucide-react'
import { formatStamp, formatStampUtc } from '../versionInfo'

// Quadro de versão: o hash que ESTA aba carregou, se bate com o servidor, e quando o servidor foi publicado
// (momento do deploy, lido do .git da imagem) em AAAA-MM-DD HH:MM:SS + timestamp Unix.
export default function VersionBox({ loadedVersion, serverVersion, timestamp, stale }) {
  const shown = loadedVersion ?? serverVersion
  if (!shown) return null
  const known = Boolean(loadedVersion && serverVersion)
  const stamp = formatStamp(timestamp)
  const utc = formatStampUtc(timestamp)

  const tone = stale
    ? 'border-yellow-400/50 bg-yellow-400/10'
    : 'border-accent/30 bg-accent/5'

  return (
    <div
      className={`mx-3 mt-3 rounded-lg border px-3 py-2.5 ${tone}`}
      title={stale
        ? `Esta aba roda v${loadedVersion}; o servidor já está em v${serverVersion}. Atualize a página.`
        : (utc ? `Publicado em ${utc}` : 'Versão em execução')}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1 text-[9px] font-bold uppercase tracking-wider text-muted">
          <GitCommit size={10} /> Versão
        </span>
        {known && (
          stale
            ? <span className="flex items-center gap-1 text-[9px] font-bold text-yellow-300"><AlertTriangle size={10} /> DESATUALIZADA</span>
            : <span className="flex items-center gap-1 text-[9px] font-bold text-bull"><CheckCircle2 size={10} /> ATUAL</span>
        )}
      </div>
      <p className="mt-1 font-mono text-base font-bold leading-none text-white">{shown}</p>
      {stale && (
        <p className="mt-1 font-mono text-[10px] text-yellow-300">servidor: {serverVersion}</p>
      )}
      <p className="mt-1.5 text-[9px] uppercase tracking-wider text-muted/70">{stale ? 'Servidor publicado em' : 'Publicado em'}</p>
      <p className="font-mono text-[11px] leading-tight text-white/90">{stamp ?? 'data indisponível'}</p>
      {timestamp != null && (
        <p className="font-mono text-[10px] leading-tight text-muted/70">ts {timestamp}</p>
      )}
    </div>
  )
}
