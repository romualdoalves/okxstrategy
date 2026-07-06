import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Play, Square, ChevronRight, Activity, AlertTriangle, TrendingUp, Star, Zap, Clock, Bookmark, RefreshCw } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { startBot, stopBot, switchBotSymbol, liquidateBot, recaptureBaseline } from '../api'
import { useLanguage } from '../i18n/LanguageContext'
import StrategyChecklist, { getCriteriaCount } from './StrategyChecklist'
import AssetPriceCard from './AssetPriceCard'
import { useMarketStatus } from '../hooks/useMarketStatus'

const FIXED_STAKE_USD = 100

function fmtStarted(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const date = d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit' })
  const time = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  return `Iniciado ${date} ${time}`
}

function fmtShortTimestamp(iso) {
  if (!iso) return 'aguardando'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'aguardando'
  const date = d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: '2-digit' })
  const time = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  return `${date} ${time}`
}

const DOT_COLOR = {
  green:  'bg-bull',
  yellow: 'bg-yellow-400',
  red:    'bg-bear',
  grey:   'bg-muted/40',
  none:   'bg-transparent',
}

export default function BotCard({ bot, liveStatus }) {
  const nav = useNavigate()
  const qc  = useQueryClient()
  const { t } = useLanguage()
  const rt  = { ...(bot.runtime ?? {}), ...(liveStatus ?? {}) }
  const botForCriteria = { ...bot, runtime: rt }

  const isRunning  = rt.status === 'running'
  const pnl        = rt.daily_pnl ?? 0
  const fees       = rt.accumulated_fees ?? 0
  const direction  = rt.direction ?? 0
  const lastPrice  = rt.last_price ?? null
  const indicators = rt.last_indicators ?? null
  const orderError = rt.last_order_error ?? null
  const suggestedSymbol = orderError?.suggested_symbol || 'BTC-USDT'
  const canSwitchSymbol = orderError?.kind === 'symbol_restricted' && bot.symbol !== suggestedSymbol
  const hasIndicators = indicators && Object.keys(indicators).length > 0
  const priceChange = bot.market_data?.change_24h ?? 0
  const isPositioned = Math.abs(direction) > 0
  
  const unrealizedPct = isPositioned && lastPrice && rt.entry_price
    ? direction * ((lastPrice - rt.entry_price) / rt.entry_price) * 100
    : null

  const startedAt   = rt.started_at ?? null
  const cycle       = rt.execution_cycle ?? {}
  const cycleTf     = cycle.timeframe ?? bot.timeframe
  const cycleCount  = Number(cycle.closed_candles ?? 0)

  const criteria   = getCriteriaCount(botForCriteria, hasIndicators ? indicators : null)
  const { isOpen: isMarketOpen, opensIn } = useMarketStatus()

  // Ativos de Cripto (com barra, traço ou tickers conhecidos) ignoram a trava de mercado fechado
  const isCrypto = bot.symbol.includes('/') || bot.symbol.includes('-') || 
    ['BTC', 'ETH', 'SOL', 'XRP', 'BNB', 'LTC', 'DOGE', 'ADA', 'DOT'].some(c => bot.symbol.toUpperCase().includes(c))
  const isOpen = isCrypto ? true : isMarketOpen

  const criteriaLabel = !criteria
    ? null
    : !hasIndicators
    ? { text: t('sc.waiting'), cls: 'text-muted' }
    : criteria.hasRed
    ? { text: t('sc.partial', { n: criteria.ok, total: criteria.total }), cls: 'text-bear' }
    : criteria.ok === criteria.total && criteria.total > 0
    ? { text: t('sc.all_ok'),  cls: 'text-bull' }
    : { text: t('sc.partial', { n: criteria.ok, total: criteria.total }), cls: 'text-yellow-400' }

  async function toggle(e) {
    e.stopPropagation()
    if (isRunning) await stopBot(bot.id)
    else           await startBot(bot.id)
    qc.invalidateQueries({ queryKey: ['bots'] })
  }

  async function handleLiquidate(e) {
    e.stopPropagation()
    if (!confirm(t('bd.liquidate_confirm') || 'Fechar posição a mercado agora?')) return
    await liquidateBot(bot.id)
    qc.invalidateQueries({ queryKey: ['bots'] })
  }

  async function switchToSuggested(e) {
    e.stopPropagation()
    await switchBotSymbol(bot.id, suggestedSymbol)
    qc.invalidateQueries({ queryKey: ['bots'] })
  }

  const [capturingBaseline, setCapturingBaseline] = useState(false)
  async function handleRecaptureBaseline(e) {
    e.stopPropagation()
    setCapturingBaseline(true)
    try {
      await recaptureBaseline(bot.id)
      qc.invalidateQueries({ queryKey: ['bots'] })
    } finally {
      setCapturingBaseline(false)
    }
  }

  return (
    <div
      onClick={() => nav(`/bots/${bot.id}`)}
      className="card group cursor-pointer hover:border-accent/40 transition-all duration-300"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="font-bold truncate text-white/90 group-hover:text-accent transition-colors">
              {bot.name}
            </h3>
            <span className={`w-2 h-2 rounded-full shrink-0 ${
              isRunning ? 'bg-bull animate-pulse' : isPositioned ? 'bg-yellow-400' : 'bg-border'
            }`} />
          </div>
          <p className="text-xs text-muted truncate">
            {bot.symbol} · {bot.timeframe}
          </p>
          {startedAt && (
            <p className="text-[10px] text-muted flex items-center gap-1 mt-0.5 opacity-70">
              <Clock size={9} />
              {fmtStarted(startedAt)}
            </p>
          )}
          <p className="text-[10px] text-muted flex items-center gap-1 mt-0.5 opacity-70">
            <Activity size={9} />
            Ciclo {cycleTf} · último {fmtShortTimestamp(cycle.last_closed_at)} · {cycleCount} pós-início
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={bot.demo ? 'badge-muted' : 'badge-bull'}>
            {bot.demo ? t('c.demo') : t('c.live')}
          </span>
        </div>
      </div>

      {/* Market Data */}
      <AssetPriceCard symbol={bot.symbol} livePrice={lastPrice} className="mb-5" />

      {/* Criteria Checklists */}
      <div className="space-y-4 mb-5">
        <StrategyChecklist bot={botForCriteria} indicators={hasIndicators ? indicators : null} mode="entry" showLabels={false} />
        <StrategyChecklist bot={botForCriteria} indicators={hasIndicators ? indicators : null} mode="order" showLabels={false} />
        {isPositioned && (
          <StrategyChecklist bot={botForCriteria} indicators={hasIndicators ? indicators : null} mode="exit" showLabels={false} />
        )}
      </div>

      {/* ── Métricas do bot ── */}
      {orderError && (
        <div className="mb-3 rounded-lg border border-yellow-400/25 bg-yellow-400/10 p-2.5">
          <div className="flex items-start gap-2">
            <AlertTriangle size={14} className="mt-0.5 shrink-0 text-yellow-400" />
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold text-yellow-300">
                {t('card.order_blocked')}
              </p>
              <p className="mt-0.5 line-clamp-2 text-[11px] leading-4 text-yellow-100/75">
                {orderError.kind === 'symbol_restricted'
                  ? t('card.symbol_restricted', { symbol: bot.symbol })
                  : orderError.message}
              </p>
              {canSwitchSymbol && (
                <button
                  type="button"
                  onClick={switchToSuggested}
                  className="mt-2 rounded-md border border-yellow-300/30 px-2 py-1 text-[11px] font-medium text-yellow-100 hover:bg-yellow-300/10"
                >
                  {t('card.switch_symbol', { symbol: suggestedSymbol })}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-3 gap-2 mb-3 text-center">
        <div>
          <p className="text-xs text-muted">{t('c.stake')}</p>
          <p className="text-sm font-medium">${FIXED_STAKE_USD}</p>
        </div>
        <div>
          <p className="text-xs text-muted">{t('card.pnl_today')}</p>
          <p className={`text-sm font-medium ${pnl > 0 ? 'text-bull' : pnl < 0 ? 'text-bear' : 'text-white'}`}>
            {pnl > 0 ? '+' : ''}{pnl.toFixed(2)}
          </p>
          {fees > 0 && (
            <p className="text-[10px] text-bear/60 mt-0.5">-{fees.toFixed(4)} tx</p>
          )}
        </div>
        <div>
          <p className="text-xs text-muted">{t('card.position')}</p>
          <div className="text-sm font-medium flex flex-col items-center leading-tight">
            <span>{direction === 1 ? t('c.long') : direction === -1 ? t('c.short') : '—'}</span>
            {unrealizedPct !== null && (
              <span className={`text-[11px] font-bold ${unrealizedPct > 0 ? 'text-bull' : unrealizedPct < 0 ? 'text-bear' : 'text-white'}`}>
                {unrealizedPct > 0 ? '+' : ''}{unrealizedPct.toFixed(2)}%
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Baseline — holdings pré-existentes do ativo ao criar o bot */}
      {(() => {
        const baseline = bot.baseline_balance ?? 0
        const baseCcy  = bot.symbol.split('-')[0]
        const fmt = n => parseFloat(n.toPrecision(6)).toString()
        return (
          <div className="mb-3 rounded-lg border border-white/8 bg-white/4 text-[11px] overflow-hidden">
            <div className="flex items-center gap-2 px-2.5 py-1.5">
              <Bookmark size={11} className={`shrink-0 ${baseline > 0 ? 'text-accent/70' : 'text-muted/40'}`} />
              <span className="text-muted">Baseline</span>
              {baseline > 0 ? (
                <>
                  <span className="font-mono font-semibold text-white/80">{fmt(baseline)} {baseCcy}</span>
                  <span className="ml-auto text-muted/50 text-[9px] text-right leading-tight">
                    pré-existente<br />ignorado em divergências
                  </span>
                </>
              ) : (
                <>
                  <span className="font-mono text-muted/50">—</span>
                  <span className="ml-auto text-muted/40 text-[9px]">sem holdings ao criar</span>
                </>
              )}
            </div>
            <button
              onClick={e => { e.stopPropagation(); handleRecaptureBaseline(e) }}
              disabled={capturingBaseline}
              className="w-full flex items-center justify-center gap-1.5 px-2.5 py-1 border-t border-white/8 text-[10px] text-blue-300/70 hover:text-blue-300 hover:bg-blue-500/10 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={9} className={capturingBaseline ? 'animate-spin' : ''} />
              {capturingBaseline ? 'Capturando…' : 'Recapturar Baseline'}
            </button>
          </div>
        )
      })()}

      {/* Guaranteed Profit Stars */}
      {rt.guaranteed_pnl > 0 && (
        <div className="mb-4 p-2 rounded-lg bg-accent/5 border border-accent/10 flex items-center justify-between animate-in fade-in slide-in-from-bottom-1 duration-500">
          <div className="flex flex-col">
            <span className="text-[10px] text-muted uppercase font-bold tracking-wider">Lucro Garantido</span>
            <span className="text-xs font-bold text-accent">+{rt.guaranteed_pnl.toFixed(2)}%</span>
          </div>
          <div className="flex gap-0.5">
            {(() => {
              const val = rt.guaranteed_pnl
              let stars = []
              let count = 0
              let color = 'text-yellow-400' // default gold

              if (val < 1) {
                count = 1
                color = 'text-slate-400' // silver
              } else if (val < 2) count = 1
              else if (val < 3) count = 2
              else if (val < 4) count = 3
              else if (val < 5) count = 4
              else count = 5

              for (let i = 0; i < count; i++) {
                stars.push(<Star key={i} size={14} fill="currentColor" className={`${color} drop-shadow-[0_0_5px_rgba(250,204,21,0.5)]`} />)
              }
              return stars
            })()}
          </div>
        </div>
      )}

      {/* ── Mercado Fechado ── */}
      {!isOpen && (
        <div
          onClick={e => e.stopPropagation()}
          className="flex items-center gap-1.5 mb-2 px-2 py-1.5 rounded-md bg-yellow-500/10 border border-yellow-500/20 text-yellow-400 text-[10px]"
        >
          <Clock size={10} className="shrink-0" />
          <span>{t('market.closed')} · {t('market.opens_in', { time: opensIn })}</span>
        </div>
      )}

      {/* ── Ações ── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {isPositioned ? (
            <button
              type="button"
              disabled
              title="Há posição aberta; use Fechar para encerrar a operação."
              className="btn flex items-center gap-1.5 text-xs bg-yellow-500/10 text-yellow-300 border border-yellow-500/20 opacity-80 cursor-not-allowed"
            >
              <Activity size={12} /> Em posição
            </button>
          ) : (
            <button
              onClick={toggle}
              disabled={!isOpen}
              title={!isOpen ? t('market.ops_suspended') : undefined}
              className={`btn flex items-center gap-1.5 text-xs ${isRunning ? 'btn-danger' : 'btn-success'} ${!isOpen ? 'opacity-40 cursor-not-allowed' : ''}`}
            >
              {isRunning
                ? <><Square size={12} /> {t('c.stop')}</>
                : <><Play  size={12} /> {t('c.start')}</>
              }
            </button>
          )}

          {!isPositioned && (
            <button
              onClick={(e) => { e.stopPropagation(); nav(`/bots/${bot.id}/edit`) }}
              className="btn flex items-center gap-1.5 text-xs bg-panel border border-border text-white hover:bg-border transition-colors"
            >
              <Zap size={12} className="text-accent" />
              Reconfigurar
            </button>
          )}

          {isPositioned && (
            <button
              onClick={handleLiquidate}
              disabled={!isOpen}
              title={!isOpen ? t('market.ops_suspended') : undefined}
              className={`btn flex items-center gap-1.5 text-xs bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 hover:bg-yellow-500/30 transition-colors ${!isOpen ? 'opacity-40 cursor-not-allowed' : ''}`}
            >
              <TrendingUp size={12} /> Fechar
            </button>
          )}
        </div>
        <ChevronRight size={16} className="text-muted group-hover:text-white transition-colors" />
      </div>
    </div>
  )
}
