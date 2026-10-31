import React from 'react'

const p2 = (v) => Number(v).toFixed(2).replace('.', ',')
const sg = (v) => `${v > 0 ? '+' : ''}${p2(v)}%`
const tone = (v) => (v > 0 ? 'text-bull' : v < 0 ? 'text-bear' : 'text-white/80')

// Taxa REAL (tabela da OKX para o nível da conta: a que avalia estratégias e a Meta) e taxa DEMO (a que a conta conectada cobra de fato),
// com o cálculo das duas. Posição aberta: bruto − taxas = líquido, sobre o mesmo preço ao vivo. Sem posição: custo de ida e volta.
export function netFor(direction, entry, last, rate) {
  const gross = direction * ((last - entry) / entry) * 100
  const fees = ((entry + last) * rate / entry) * 100          // taxa de entrada + taxa de saída, em % do valor de entrada
  return { gross, fees, net: gross - fees }
}

export default function FeeCompare({ direction, entry, last, demoRate, realRate }) {
  if (demoRate == null || realRate == null) return null
  const same = Math.abs(demoRate - realRate) < 1e-9
  const positioned = Math.abs(direction) > 0 && entry > 0 && last > 0
  const rows = [
    { key: 'real', label: 'Taxa Real', rate: realRate, hint: 'Tabela da OKX para o seu nível (taker): avalia estudos, Scanner e Meta 1%' },
    { key: 'demo', label: 'Taxa Demo', rate: demoRate, hint: 'Cobrada pela conta conectada, medida nos fills (forma o P&L realizado)' },
  ]
  return (
    <div className="mb-3 rounded-lg border border-white/8 bg-white/4 px-2.5 py-2 text-[11px]" data-testid="fee-compare">
      <p className="mb-1 text-[10px] font-bold uppercase tracking-wider text-muted">
        Taxas por lado · Real {p2(realRate * 100)}% · Demo {p2(demoRate * 100)}%{same ? ' (iguais)' : ''}
      </p>
      {rows.map(r => {
        const c = positioned ? netFor(direction, entry, last, r.rate) : null
        return (
          <div key={r.key} className="flex flex-wrap items-baseline gap-x-1.5 leading-snug" title={r.hint}>
            <span className="w-[74px] shrink-0 font-semibold text-white/80">{r.label} {p2(r.rate * 100)}%</span>
            {c ? (
              <span className="text-muted">
                bruto <span className={tone(c.gross)}>{sg(c.gross)}</span> − taxas (entrada + saída) <span className="text-bear/80">{p2(c.fees)}%</span>
                {' '}= líquido <strong className={tone(c.net)}>{sg(c.net)}</strong>
              </span>
            ) : (
              <span className="text-muted">
                ida e volta custa <strong className="text-bear/80">{p2(r.rate * 200)}%</strong> (2 × {p2(r.rate * 100)}%): o preço precisa subir isso só para empatar
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}
