import React from 'react'
import { TrendingUp, TrendingDown } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { getTicker } from '../api'
import { useLanguage } from '../i18n/LanguageContext'

const fmtPrice = (p) => {
  const n = Number(p)
  if (!isFinite(n)) return '—'
  if (n >= 100) return `$${n.toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  if (n >= 1)   return `$${n.toFixed(4)}`
  return `$${n.toFixed(6)}`
}

export default function AssetPriceCard({ symbol, livePrice = null, className = '' }) {
  const { t } = useLanguage()

  const { data: ticker } = useQuery({
    queryKey:        ['ticker', symbol],
    queryFn:         () => getTicker(symbol),
    refetchInterval: 60_000,
    staleTime:       30_000,
  })

  const td        = ticker?.data?.[0]
  const open24h   = td ? parseFloat(td.open24h)  : null
  const high24h   = td ? parseFloat(td.high24h)  : null
  const low24h    = td ? parseFloat(td.low24h)   : null
  const basePrice = livePrice ?? (td ? parseFloat(td.last) : null)

  const change24h = (basePrice != null && open24h)
    ? (basePrice - open24h) / open24h * 100
    : null
  const rising = change24h != null ? change24h >= 0 : null

  const symbolLabel = symbol.replace('-SWAP', '').replace(/-(\w+)$/, '/$1')

  return (
    <div className={`rounded-lg bg-white/4 border border-border/40 px-3 py-2.5 ${className}`}>
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-1.5">
          {rising === true  && <TrendingUp  size={12} className="text-bull" />}
          {rising === false && <TrendingDown size={12} className="text-bear" />}
          {rising === null  && <TrendingUp  size={12} className="text-muted" />}
          <span className="text-xs text-muted font-medium">{symbolLabel}</span>
        </div>
        {change24h != null && (
          <span className={`text-xs font-semibold ${rising ? 'text-bull' : 'text-bear'}`}>
            {rising ? '+' : ''}{t('c.change_24h', { v: change24h.toFixed(2) })}
          </span>
        )}
      </div>

      <p className={`text-lg font-bold leading-tight ${
        basePrice != null
          ? rising === true  ? 'text-bull'
          : rising === false ? 'text-bear'
          : 'text-white'
        : 'text-muted'
      }`}>
        {basePrice != null ? fmtPrice(basePrice) : '—'}
      </p>

      {(high24h != null || low24h != null) && (
        <div className="flex gap-3 mt-1">
          {high24h != null && (
            <span className="text-xs text-muted">
              {t('c.high_short')}: <span className="text-white/70">{fmtPrice(high24h)}</span>
            </span>
          )}
          {low24h != null && (
            <span className="text-xs text-muted">
              {t('c.low_short')}: <span className="text-white/70">{fmtPrice(low24h)}</span>
            </span>
          )}
        </div>
      )}
    </div>
  )
}
