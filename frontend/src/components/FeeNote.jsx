import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Info } from 'lucide-react'
import { getFeeModel } from '../api'

const pct = (v) => (Number(v) * 100).toFixed(2).replace('.', ',') + '%'

// As duas taxas por lado, sempre lado a lado: a de REFERÊNCIA (conta real; avalia estudos, Scanner, backtests e Meta 1%)
// e a COBRADA pela conta conectada (demo cobra mais). Nada de número único que esconda a diferença.
export default function FeeNote({ className = '' }) {
  const { data } = useQuery({ queryKey: ['fee-model'], queryFn: getFeeModel, staleTime: 5 * 60 * 1000 })
  if (!data || data.fee_rate == null) return null
  const premium = (data.charged_rate ?? data.fee_rate) - data.fee_rate
  return (
    <p className={`flex items-start gap-1.5 text-[11px] text-muted ${className}`}>
      <Info size={12} className="mt-0.5 shrink-0" />
      <span>
        Taxa usada nos estudos, Scanner, backtests e Meta 1%: <strong className="text-white/80">{pct(data.fee_rate)} por lado</strong> (referência de conta real).
        {premium > 0.00005
          ? <> A conta {data.demo ? 'demo ' : ''}está cobrando <strong className="text-amber-300">{pct(data.charged_rate)} por lado</strong>, {(premium * 100).toFixed(2).replace('.', ',')} p.p. a mais:
              o P&amp;L que ela realiza fica abaixo do que o backtest mostra.</>
          : ' A conta está cobrando a mesma taxa.'}
      </span>
    </p>
  )
}
