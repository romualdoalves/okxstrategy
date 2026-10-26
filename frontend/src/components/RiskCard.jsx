import React, { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ShieldAlert, ShieldCheck } from 'lucide-react'
import { getRisk, updateRisk, releaseRiskDrawdown } from '../api'

const BREACH_LABEL = {
  positions: 'Máx. posições abertas',
  exposure: 'Exposição máxima',
  daily_loss: 'Perda diária da conta',
  drawdown: 'Drawdown (liberação manual)',
}

const FIELDS = [
  ['max_open_positions', 'Posições abertas (máx.)', 1],
  ['max_exposure_usd', 'Exposição (US$)', 10],
  ['max_account_daily_loss_usd', 'Perda diária (US$)', 5],
  ['max_drawdown_pct', 'Drawdown (%)', 0.5],
  ['capital_base_usd', 'Base do drawdown (US$)', 100],
  ['max_auto_bots', 'Bots automáticos (máx.)', 1],
]

function Meter({ label, value, max, text, danger }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-muted">{label}</span>
        <span className={danger ? 'text-bear font-medium' : 'text-white'}>{text}</span>
      </div>
      <div className="h-1.5 rounded bg-white/5 overflow-hidden">
        <div className={`h-full ${danger ? 'bg-bear' : pct > 70 ? 'bg-yellow-500' : 'bg-bull'}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

export default function RiskCard() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['risk'], queryFn: getRisk, refetchInterval: 10_000 })
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({})
  const [error, setError] = useState('')

  if (!data) return null
  const { limits, usage, breaches, blocking, drawdown_halt } = data
  const loss = Math.max(0, -usage.daily_pnl_usd)

  async function save() {
    setError('')
    try {
      const body = {}
      for (const [k] of FIELDS) if (form[k] !== undefined && form[k] !== '') body[k] = Number(form[k])
      if (form.enabled !== undefined) body.enabled = form.enabled
      qc.setQueryData(['risk'], await updateRisk(body))
      setEditing(false)
      setForm({})
    } catch (e) {
      setError(e.message)
    }
  }

  async function release() {
    if (!window.confirm('Liberar o bloqueio de drawdown? A medição do pico recomeça a partir de agora.')) return
    qc.setQueryData(['risk'], await releaseRiskDrawdown())
  }

  return (
    <div className={`card space-y-3 ${blocking ? 'border border-bear/40' : ''}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {blocking ? <ShieldAlert size={16} className="text-bear" /> : <ShieldCheck size={16} className="text-bull" />}
          <span className="font-semibold text-sm">Risco global da conta</span>
          {!limits.enabled && <span className="text-xs text-yellow-500">desativado</span>}
        </div>
        <button className="text-xs text-muted hover:text-white" onClick={() => setEditing(!editing)}>
          {editing ? 'Fechar' : 'Editar limites'}
        </button>
      </div>

      {blocking && (
        <div className="text-xs text-bear bg-bear/10 rounded px-2 py-1.5">
          Novas entradas bloqueadas: {breaches.map((b) => BREACH_LABEL[b] ?? b).join(', ')}. Posições abertas seguem protegidas pelo SL.
          {breaches.includes('drawdown') && (
            <button className="ml-2 underline" onClick={release}>Liberar drawdown</button>
          )}
          {drawdown_halt && <span className="ml-1 opacity-70">(−{drawdown_halt.dd_pct}% em {drawdown_halt.at.slice(0, 16).replace('T', ' ')} UTC)</span>}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Meter label="Posições abertas" value={usage.open_positions} max={limits.max_open_positions}
          text={`${usage.open_positions} / ${limits.max_open_positions}`} danger={breaches.includes('positions')} />
        <Meter label="Exposição" value={usage.exposure_usd} max={limits.max_exposure_usd}
          text={`$${usage.exposure_usd} / $${limits.max_exposure_usd}`} danger={breaches.includes('exposure')} />
        <Meter label="Perda do dia" value={loss} max={limits.max_account_daily_loss_usd}
          text={`${usage.daily_pnl_usd >= 0 ? '+' : '−'}$${Math.abs(usage.daily_pnl_usd)} / −$${limits.max_account_daily_loss_usd}`}
          danger={breaches.includes('daily_loss')} />
        <Meter label="Drawdown" value={usage.drawdown_pct} max={limits.max_drawdown_pct}
          text={`${usage.drawdown_pct}% / ${limits.max_drawdown_pct}%`} danger={breaches.includes('drawdown')} />
      </div>

      {editing && (
        <div className="pt-2 border-t border-white/5 space-y-3">
          <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
            {FIELDS.map(([key, label, step]) => (
              <label key={key} className="text-xs text-muted space-y-1">
                <span>{label}</span>
                <input type="number" step={step} min="0"
                  className="w-full bg-black/30 border border-white/10 rounded px-2 py-1 text-white"
                  defaultValue={limits[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
              </label>
            ))}
          </div>
          <label className="flex items-center gap-2 text-xs text-muted">
            <input type="checkbox" defaultChecked={limits.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
            Limites ativos
          </label>
          {error && <p className="text-xs text-bear">{error}</p>}
          <button className="px-3 py-1 text-xs rounded bg-accent/20 text-accent hover:bg-accent/30" onClick={save}>Salvar</button>
        </div>
      )}
    </div>
  )
}
