// Formatação do timestamp de versão (epoch em segundos, como vem de /api/health).
const pad = n => String(n).padStart(2, '0')

function toDate(epochSeconds) {
  if (epochSeconds == null || epochSeconds === '') return null
  const d = new Date(Number(epochSeconds) * 1000)
  return Number.isNaN(d.getTime()) ? null : d
}

/** Hora LOCAL do navegador: AAAA-MM-DD HH:MM:SS (ou null se indisponível). */
export function formatStamp(epochSeconds) {
  const d = toDate(epochSeconds)
  if (!d) return null
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/** Mesmo instante em UTC: AAAA-MM-DD HH:MM:SS UTC (ou null). */
export function formatStampUtc(epochSeconds) {
  const d = toDate(epochSeconds)
  return d ? `${d.toISOString().replace('T', ' ').slice(0, 19)} UTC` : null
}
