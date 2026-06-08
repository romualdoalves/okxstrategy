import React, { useState } from 'react'
import { X, Eye, EyeOff, RefreshCcw, ShieldCheck, AlertTriangle, Plug, CheckCircle2 } from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useLanguage } from '../i18n/LanguageContext'
import { connectOkxAccount, getOkxStatus } from '../api'
import { toast } from '../hooks/useToast'

export default function OkxConnectModal({ onClose }) {
  const { t } = useLanguage()
  const qc = useQueryClient()

  const [apiKey,        setApiKey]        = useState('')
  const [apiSecret,     setApiSecret]     = useState('')
  const [passphrase,    setPassphrase]    = useState('')
  const [showKey,       setShowKey]       = useState(false)
  const [showSecret,    setShowSecret]    = useState(false)
  const [showPhrase,    setShowPhrase]    = useState(false)
  const [loading,       setLoading]       = useState(false)
  const [error,         setError]         = useState('')
  const [savedEquity,   setSavedEquity]   = useState(null)
  const [confirmClear,  setConfirmClear]  = useState(false)

  const { data: status } = useQuery({
    queryKey: ['okx-status'],
    queryFn:  getOkxStatus,
    staleTime: 30_000,
  })

  const canSubmit = apiKey.trim().length > 5
    && apiSecret.trim().length > 5
    && passphrase.trim().length > 2
    && confirmClear

  async function handleSubmit(e) {
    e.preventDefault()
    if (!canSubmit) { setError(t('okx.error_empty')); return }
    setError('')
    setLoading(true)
    try {
      const res = await connectOkxAccount(apiKey.trim(), apiSecret.trim(), passphrase.trim(), confirmClear)
      const equity = res.account?.equity ?? res.equity
      setSavedEquity(equity)
      qc.invalidateQueries({ queryKey: ['okx-status'] })
      qc.invalidateQueries({ queryKey: ['bots'] })
      qc.invalidateQueries({ queryKey: ['trades'] })
      qc.invalidateQueries({ queryKey: ['activities'] })
      toast({
        kind:  'success',
        title: t('okx.success_title'),
        body:  t('okx.success', { equity: equity?.toFixed?.(2) ?? '—' }),
      })
      onClose()
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const isDemo      = status?.demo !== false
  const isConnected = status?.configured === true

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-md bg-[#0f1117] border border-accent/30 rounded-2xl shadow-2xl">

        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-border">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-accent/15 flex items-center justify-center">
              <Plug size={18} className="text-accent" />
            </div>
            <div>
              <p className="font-bold text-white">{t('okx.modal_title')}</p>
              <p className="text-xs text-muted">{t('okx.modal_subtitle')}</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-muted hover:text-white p-1 rounded transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Status atual (se já conectada) */}
        {isConnected && status?.equity != null && (
          <div className="mx-5 mt-5 flex items-center gap-3 p-3 rounded-xl bg-bull/8 border border-bull/25">
            <CheckCircle2 size={15} className="text-bull shrink-0" />
            <div className="text-xs text-bull/90">
              <span className="font-semibold">OKX conectada.</span>
              {' '}{t('okx.balance_label')} <span className="font-mono font-bold">{status.equity.toFixed(2)} USDT</span>
            </div>
          </div>
        )}

        {/* Modo demo/live */}
        <div className={`mx-5 mt-4 flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium border
          ${isDemo
            ? 'bg-yellow-400/8 border-yellow-400/25 text-yellow-300'
            : 'bg-bear/8 border-bear/25 text-bear'
          }`}
        >
          <ShieldCheck size={13} className="shrink-0" />
          {isDemo ? t('okx.warn_demo') : t('okx.warn_live')}
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-5 space-y-4">

          {/* API Key */}
          <div>
            <label className="label">{t('okx.label_key')}</label>
            <div className="relative">
              <input
                className="input pr-10 font-mono text-sm w-full"
                type={showKey ? 'text' : 'password'}
                placeholder={t('okx.ph_key')}
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
              <button
                type="button"
                onClick={() => setShowKey(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-white transition-colors"
              >
                {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
          </div>

          {/* Secret Key */}
          <div>
            <label className="label">{t('okx.label_secret')}</label>
            <div className="relative">
              <input
                className="input pr-10 font-mono text-sm w-full"
                type={showSecret ? 'text' : 'password'}
                placeholder={t('okx.ph_secret')}
                value={apiSecret}
                onChange={e => setApiSecret(e.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
              <button
                type="button"
                onClick={() => setShowSecret(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-white transition-colors"
              >
                {showSecret ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
          </div>

          {/* Passphrase */}
          <div>
            <label className="label">{t('okx.label_passphrase')}</label>
            <div className="relative">
              <input
                className="input pr-10 font-mono text-sm w-full"
                type={showPhrase ? 'text' : 'password'}
                placeholder={t('okx.ph_passphrase')}
                value={passphrase}
                onChange={e => setPassphrase(e.target.value)}
                autoComplete="off"
                spellCheck={false}
              />
              <button
                type="button"
                onClick={() => setShowPhrase(v => !v)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-white transition-colors"
              >
                {showPhrase ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
          </div>

          {/* Info */}
          <p className="text-[11px] text-muted leading-relaxed">
            {t('okx.info')}
          </p>

          <label className="flex items-start gap-3 text-xs text-red-200 bg-red-500/10 border border-red-500/20 rounded-lg p-3">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={confirmClear}
              onChange={e => setConfirmClear(e.target.checked)}
            />
            <span>{t('okx.confirm_clear')}</span>
          </label>

          {/* Erro */}
          {error && (
            <div className="flex items-start gap-2 text-red-400 text-xs bg-red-500/10 border border-red-500/20 rounded-lg p-2.5">
              <AlertTriangle size={13} className="mt-0.5 shrink-0" />
              {error}
            </div>
          )}

          {/* Botões */}
          <div className="flex gap-3 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="btn btn-ghost flex-1"
              disabled={loading}
            >
              {t('c.cancel')}
            </button>
            <button
              type="submit"
              disabled={!canSubmit || loading}
              className={`btn flex-1 font-semibold transition-all ${
                canSubmit && !loading
                  ? 'bg-accent hover:bg-blue-500 text-white'
                  : 'btn-ghost opacity-50 cursor-not-allowed'
              }`}
            >
              {loading ? (
                <span className="flex items-center gap-2 justify-center">
                  <RefreshCcw size={13} className="animate-spin" />
                  {t('okx.submitting')}
                </span>
              ) : (
                t('okx.submit')
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
