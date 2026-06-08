import React from 'react'
import { useLanguage } from '../i18n/LanguageContext'

const EVENT_LABELS = {
  SL:                     'Stop Loss',
  TRAILING:               'Trailing Stop',
  TP1_50pct:              'TP1 50%',
  MANUAL:                 'Manual',
  WS_EXECUTION:           'Exchange',
  RESET_FORCE_CLOSE:      'Reset',
  DYNAMIC_SHADOW_TRIGGER: 'Shadow TP',
  MANUAL_FAILED:          'Manual (falhou)',
}

function TypeBadge({ type }) {
  const cls = type === 'entry'
    ? 'bg-accent/15 text-accent'
    : type === 'tp1'
    ? 'bg-bull/15 text-bull'
    : 'bg-bear/15 text-bear'
  return <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded ${cls}`}>{type}</span>
}

function PnlCell({ pnl, net, fee }) {
  if (pnl === null || pnl === undefined) return <span className="text-muted">—</span>
  const hasFee = fee != null && fee > 0
  return (
    <div className="flex flex-col items-end gap-0.5">
      {/* P&L líquido em destaque */}
      <span className={`font-semibold tabular-nums text-sm leading-none ${(net ?? pnl) >= 0 ? 'text-bull' : 'text-bear'}`}>
        {(net ?? pnl) >= 0 ? '+' : ''}${(net ?? pnl).toFixed(2)}
      </span>
      {/* Bruto e taxa apenas se houver fee registrada */}
      {hasFee && (
        <div className="flex items-center gap-1.5">
          <span className="text-[9px] text-white/30 tabular-nums">
            bruto {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
          </span>
          <span className="text-[9px] text-bear/70 tabular-nums">
            -{fee.toFixed(4)}
          </span>
        </div>
      )}
      {/* Fee ainda não sincronizada */}
      {fee === null && pnl !== null && (
        <span className="text-[9px] text-muted/40 italic">fee pendente</span>
      )}
    </div>
  )
}

export default function TradeTable({ trades = [], showBotName = true }) {
  const { lang } = useLanguage()
  const locale = lang === 'en-US' ? 'en-US' : 'pt-BR'

  if (!trades.length) return (
    <div className="text-center text-muted py-10 text-sm">Nenhum trade registrado ainda.</div>
  )

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-muted text-[11px] uppercase tracking-wide">
            <th className="text-left py-2 px-3 font-medium">Data</th>
            {showBotName && <th className="text-left py-2 px-3 font-medium">Bot</th>}
            <th className="text-left py-2 px-3 font-medium">Tipo</th>
            <th className="text-left py-2 px-3 font-medium">Evento</th>
            <th className="text-left py-2 px-3 font-medium">Dir</th>
            <th className="text-left py-2 px-3 font-medium">Ativo</th>
            <th className="text-left py-2 px-3 font-medium">Qtd</th>
            <th className="text-left py-2 px-3 font-medium">Entrada</th>
            <th className="text-left py-2 px-3 font-medium">Saída</th>
            <th className="text-right py-2 px-3 font-medium">P&L (líquido)</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((tr) => (
            <tr key={tr.id} className="border-b border-border/40 hover:bg-panel/40 transition-colors">
              <td className="py-2.5 px-3 text-muted text-xs whitespace-nowrap">
                {tr.timestamp
                  ? new Date(tr.timestamp).toLocaleString(locale, { dateStyle: 'short', timeStyle: 'short' })
                  : '—'}
              </td>
              {showBotName && (
                <td className="py-2.5 px-3 text-xs font-medium max-w-[120px] truncate" title={tr.bot_name}>
                  {tr.bot_name ?? `Bot ${tr.bot_id}`}
                </td>
              )}
              <td className="py-2.5 px-3"><TypeBadge type={tr.type} /></td>
              <td className="py-2.5 px-3 text-xs text-muted">
                {EVENT_LABELS[tr.event] ?? tr.event ?? '—'}
              </td>
              <td className="py-2.5 px-3">
                {tr.direction === 'LONG' || tr.direction === 'long'
                  ? <span className="text-bull font-medium text-xs">LONG</span>
                  : tr.direction === 'SHORT' || tr.direction === 'short'
                  ? <span className="text-bear font-medium text-xs">SHORT</span>
                  : <span className="text-muted">—</span>}
              </td>
              <td className="py-2.5 px-3 text-xs font-mono">{tr.symbol ?? '—'}</td>
              <td className="py-2.5 px-3 text-center text-xs tabular-nums">{tr.size ?? '—'}</td>
              <td className="py-2.5 px-3 font-mono text-xs tabular-nums">
                {tr.entry_price ? `$${Number(tr.entry_price).toLocaleString(locale, { minimumFractionDigits: 2 })}` : '—'}
              </td>
              <td className="py-2.5 px-3 font-mono text-xs tabular-nums">
                {tr.exit_price ? `$${Number(tr.exit_price).toLocaleString(locale, { minimumFractionDigits: 2 })}` : '—'}
              </td>
              <td className="py-2.5 px-3 text-right">
                <PnlCell pnl={tr.pnl} net={tr.net_pnl} fee={tr.fee} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
