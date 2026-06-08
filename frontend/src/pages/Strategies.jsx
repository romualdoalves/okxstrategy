import React, { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getStrategies, getMarketRegime, deleteStrategy } from '../api'
import { useNavigate } from 'react-router-dom'
import { useLanguage } from '../i18n/LanguageContext'
import { Zap, ChevronDown, ChevronUp, Hash, TrendingUp, Loader2, Trash2 } from 'lucide-react'

const tagColors = {
  trend:     'bg-blue-500/20 text-blue-400',
  momentum:  'bg-purple-500/20 text-purple-400',
  reversal:  'bg-orange-500/20 text-orange-400',
  crossover: 'bg-teal-500/20 text-teal-400',
  breakout:  'bg-yellow-500/20 text-yellow-400',
  vwap:      'bg-pink-500/20 text-pink-400',
  volume:    'bg-green-500/20 text-green-400',
  volatility:'bg-red-500/20 text-red-400',
  atr:       'bg-gray-500/20 text-gray-400',
  rsi:       'bg-indigo-500/20 text-indigo-400',
  macd:      'bg-cyan-500/20 text-cyan-400',
  bollinger: 'bg-amber-500/20 text-amber-400',
  arbitrage: 'bg-emerald-500/20 text-emerald-400',
  dex:       'bg-violet-500/20 text-violet-400',
  spread:    'bg-rose-500/20 text-rose-400',
  'multi_source': 'bg-sky-500/20 text-sky-400',
  advanced:  'bg-orange-600/20 text-orange-500',
}

// ─── Scoring de oportunidade ────────────────────────────────────────────────
// Pontua cada estratégia com base no regime e sessão atuais.
// Retorna um número 0..10.
function calcOpportunityScore(strategy, regime) {
  if (!regime || regime.regime === 'unknown') return 5  // neutro

  const tags     = strategy.tags || []
  const tf       = strategy.recommended_timeframe || '15m'
  const { regime: mkt, trend_dir, session, atr_pct } = regime

  let score = 5  // base neutra

  // ── Regime vs tags ──────────────────────────────────────────────────────
  const TRENDING_TAGS = ['trend', 'momentum', 'breakout', 'multi_tf', 'vwap', 'ICT', 'institutional']
  const RANGING_TAGS  = ['reversal', 'mean_reversion', 'bollinger', 'oscillator', 'range']
  const VOLATILE_TAGS = ['volatility', 'breakout', 'momentum', 'liquidity', 'price_action']

  if (mkt === 'trending') {
    const matchT = tags.filter(t => TRENDING_TAGS.includes(t)).length
    const matchR = tags.filter(t => RANGING_TAGS.includes(t)).length
    score += matchT * 1.2 - matchR * 0.8
    // Tendência de alta vs baixa: CHoCH e price action beneficiam ambas
    if (trend_dir === 'up'   && tags.includes('reversal'))    score -= 0.5
    if (trend_dir === 'down' && tags.includes('institutional')) score += 0.5
  }

  if (mkt === 'ranging') {
    const matchR = tags.filter(t => RANGING_TAGS.includes(t)).length
    const matchT = tags.filter(t => TRENDING_TAGS.includes(t)).length
    score += matchR * 1.2 - matchT * 0.5
    if (tags.includes('bollinger') || tags.includes('reversal')) score += 1
  }

  if (mkt === 'volatile') {
    const matchV = tags.filter(t => VOLATILE_TAGS.includes(t)).length
    score += matchV * 1.0
    if (tags.includes('scalping')) score -= 1  // scalping arrisca em alta volatilidade
  }

  // ── Sessão vs tipo de estratégia ────────────────────────────────────────
  if (session === 'ny') {
    // NY: alta liquidez — estratégias institucionais e de price action ganham
    if (tags.some(t => ['institutional', 'ICT', 'liquidity', 'price_action'].includes(t))) score += 1.5
    if (tags.includes('volume'))  score += 0.5
    if (tf === '5m' || tf === '15m') score += 0.5   // timeframes curtos têm liquidez boa
  }
  if (session === 'london') {
    if (tags.some(t => ['trend', 'momentum', 'breakout'].includes(t))) score += 1
    if (tf === '15m' || tf === '1h') score += 0.5
  }
  if (session === 'asia') {
    if (tags.some(t => ['range', 'reversal', 'mean_reversion', 'bollinger'].includes(t))) score += 1
    if (tf === '1h' || tf === '4h') score += 0.5
  }
  if (session === 'off') {
    // Fora de horário: preferir estratégias swing / mais lentas
    if (tf === '1h' || tf === '4h') score += 1
    if (tf === '5m') score -= 1
    if (tags.includes('scalping')) score -= 1.5
  }

  // ── Volatilidade absoluta ────────────────────────────────────────────────
  if (atr_pct > 1.0 && tags.some(t => ['momentum', 'breakout'].includes(t))) score += 0.5
  if (atr_pct < 0.4 && tags.some(t => ['reversal', 'bollinger'].includes(t))) score += 0.5

  return Math.max(0, Math.min(10, score))
}

// Cor do badge de oportunidade (0..10)
function opportunityColor(score) {
  if (score >= 8)  return 'text-bull border-bull/50 bg-bull/10'
  if (score >= 6)  return 'text-yellow-400 border-yellow-400/40 bg-yellow-400/8'
  if (score >= 4)  return 'text-orange-400 border-orange-400/40 bg-orange-400/8'
  return              'text-muted border-border/30 bg-border/10'
}

function opportunityLabel(score, regime) {
  if (!regime || regime.regime === 'unknown') return '— regime desconhecido'
  const mkt = regime.regime
  const sess = { ny: 'Sessão NY', london: 'Sessão Londres', asia: 'Sessão Ásia', off: 'Mercado Lento' }[regime.session] || ''
  const tag  = score >= 8 ? '🔥 Ótima' : score >= 6 ? '✓ Boa' : score >= 4 ? '~ Neutra' : '↓ Baixa'
  return `${tag} · ${mkt} · ${sess}`
}

// ─── Componente de badge ────────────────────────────────────────────────────
function OpportunityBadge({ score, regime }) {
  const color = opportunityColor(score)
  const stars = score >= 8 ? '★★★' : score >= 6 ? '★★' : score >= 4 ? '★' : '·'
  return (
    <span className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-full border ${color} shrink-0`}
          title={opportunityLabel(score, regime)}>
      {stars} {score.toFixed(1)}
    </span>
  )
}

// ─── Régua de contexto ──────────────────────────────────────────────────────
function RegimeBar({ regime, loading }) {
  if (loading) return (
    <div className="flex items-center gap-2 text-xs text-muted px-1">
      <Loader2 size={12} className="animate-spin" />
      <span>Avaliando mercado...</span>
    </div>
  )
  if (!regime || regime.regime === 'unknown') return null

  const regimeColor = { trending: 'text-bull', ranging: 'text-yellow-400', volatile: 'text-red-400' }[regime.regime] || 'text-muted'
  const sessionLabel = { ny: 'NY', london: 'Londres', asia: 'Ásia', off: 'Fora de horário' }[regime.session] || '—'
  const trendLabel   = { up: '▲ Alta', down: '▼ Baixa', flat: '→ Lateral' }[regime.trend_dir] || '—'

  return (
    <div className="flex flex-wrap items-center gap-3 text-[11px] px-1 py-2 rounded-lg bg-border/10 border border-border/30">
      <span className="text-muted font-semibold uppercase tracking-wider">Mercado Agora</span>
      <span className={`font-bold uppercase ${regimeColor}`}>{regime.regime}</span>
      <span className="text-muted">·</span>
      <span className="text-white/70">{trendLabel}</span>
      <span className="text-muted">·</span>
      <span className="text-white/70">Sessão: <strong>{sessionLabel}</strong></span>
      <span className="text-muted">·</span>
      <span className="text-white/50">ATR {regime.atr_pct?.toFixed(2)}%</span>
      <span className="text-muted">·</span>
      <span className="text-white/50">BBW {regime.bbw_pct?.toFixed(2)}%</span>
      <span className="ml-auto text-muted/40 text-[9px]">
        atualizado {regime.updated_at ? new Date(regime.updated_at).toLocaleTimeString('pt-BR') : '—'}
      </span>
    </div>
  )
}

// ─── Linha de parâmetro ─────────────────────────────────────────────────────
function ParamRow({ name, param }) {
  return (
    <tr className="border-b border-border/50 text-sm">
      <td className="py-2 pr-4 font-mono text-xs text-accent">{name}</td>
      <td className="py-2 pr-4 text-muted text-xs">{param.description}</td>
      <td className="py-2 pr-4 text-center">{String(param.default)}</td>
      <td className="py-2 text-center text-muted text-xs">
        {param.min !== null ? `${param.min} – ${param.max}` : '—'}
      </td>
    </tr>
  )
}

// ─── Card de estratégia ─────────────────────────────────────────────────────
function StrategyCard({ strategy, sortBy, score, regime }) {
  const [open, setOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const nav = useNavigate()
  const { t } = useLanguage()
  const qc = useQueryClient()

  const handleDelete = async () => {
    if (!confirm(`Excluir estratégia ${strategy.id} permanentemente?\n\nBots ativos usando esta estratégia podem parar de funcionar.`)) return
    setDeleting(true)
    try {
      await deleteStrategy(strategy.id)
      qc.invalidateQueries({ queryKey: ['strategies'] })
    } catch (e) {
      alert('Erro ao excluir: ' + e.message)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="card space-y-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-9 h-9 rounded-lg bg-accent/15 flex items-center justify-center shrink-0">
            <Zap size={16} className="text-accent" />
          </div>
          <div className="min-w-0">
            <p className="font-semibold truncate">{strategy.name}</p>
            <p className="text-xs text-muted font-mono">{strategy.id}</p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {sortBy === 'opportunity' && (
            <OpportunityBadge score={score} regime={regime} />
          )}
          {/* Botão excluir para estratégias de fábrica (prefixos semânticos + legado F/FX) */}
          {/^(TF|MR|PA|SC|RG|IF|NW|F|FX)\d/.test(strategy.id) && (
            <button
              onClick={handleDelete}
              disabled={deleting}
              className="p-1.5 text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded-lg transition-colors"
              title="Excluir estratégia"
            >
              <Trash2 size={14} />
            </button>
          )}
          <button
            onClick={() => nav(`/bots/new?strategy=${strategy.id}`)}
            className="btn-primary btn text-xs"
          >
            {t('c.use')}
          </button>
        </div>
      </div>

      <p className="text-sm text-muted leading-relaxed">{strategy.description}</p>

      <div className="flex flex-wrap gap-1.5">
        {strategy.tags.map(tag => (
          <span key={tag} className={`text-xs px-2 py-0.5 rounded-full ${tagColors[tag] ?? 'bg-border text-muted'}`}>
            {tag}
          </span>
        ))}
      </div>

      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1 text-xs text-muted hover:text-white transition-colors"
      >
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        {t('strat.params', { n: Object.keys(strategy.params).length })}
      </button>

      {open && (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-muted uppercase border-b border-border">
              <th className="text-left py-1">{t('c.param')}</th>
              <th className="text-left py-1">{t('c.description')}</th>
              <th className="text-center py-1">{t('c.default')}</th>
              <th className="text-center py-1">{t('c.range')}</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(strategy.params).map(([k, v]) => (
              <ParamRow key={k} name={k} param={v} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

// ─── Página principal ───────────────────────────────────────────────────────
export default function Strategies() {
  const { t } = useLanguage()
  const [sortBy, setSortBy] = useState('id')

  const { data: rawStrategies = [], isLoading } = useQuery({
    queryKey: ['strategies'],
    queryFn:  getStrategies,
  })

  const { data: regime, isLoading: regimeLoading } = useQuery({
    queryKey:        ['market-regime'],
    queryFn:         getMarketRegime,
    refetchInterval: 5 * 60 * 1000,   // reavalia a cada 5 min
    staleTime:       4 * 60 * 1000,
  })

  const strategies = [...rawStrategies].sort((a, b) => {
    if (sortBy === 'id') return a.id.localeCompare(b.id)
    // Oportunidade: maior score primeiro, empate por ID
    const sa = calcOpportunityScore(a, regime)
    const sb = calcOpportunityScore(b, regime)
    return sb !== sa ? sb - sa : a.id.localeCompare(b.id)
  })

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-xl font-bold">{t('strat.title')}</h1>
          <p className="text-muted text-sm mt-0.5">
            {t('strat.subtitle', { n: strategies.length })}
          </p>
        </div>

        {/* Seletor de ordenação */}
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs text-muted uppercase tracking-widest whitespace-nowrap">Ordenar por</span>
          <div className="flex rounded-lg border border-border overflow-hidden text-xs font-medium">
            <button
              onClick={() => setSortBy('id')}
              className={`flex items-center gap-1.5 px-3 py-1.5 transition-colors ${
                sortBy === 'id'
                  ? 'bg-accent text-black'
                  : 'text-muted hover:text-white hover:bg-border/50'
              }`}
            >
              <Hash size={12} />
              ID
            </button>
            <button
              onClick={() => setSortBy('opportunity')}
              className={`flex items-center gap-1.5 px-3 py-1.5 transition-colors border-l border-border ${
                sortBy === 'opportunity'
                  ? 'bg-bull text-black'
                  : 'text-muted hover:text-white hover:bg-border/50'
              }`}
            >
              <TrendingUp size={12} />
              Estratégia Oportuna
            </button>
          </div>
        </div>
      </div>

      {/* Régua de contexto de mercado (visível em modo Oportunidade) */}
      {sortBy === 'opportunity' && (
        <RegimeBar regime={regime} loading={regimeLoading} />
      )}

      <div className="space-y-4">
        {/* Dynamic Shadow Trigger */}
        <div className="bg-accent/10 border border-accent/20 rounded-xl p-6 flex gap-5 items-start">
          <Zap size={28} className="text-accent shrink-0 mt-1" />
          <div className="space-y-2">
            <p className="font-bold text-accent text-lg">Gatilho de Sombra Dinâmica (Dynamic Shadow Trigger)</p>
            <p className="text-sm text-muted leading-relaxed">
              Todas as estratégias operam sob uma gestão de risco institucional unificada: em vez de saídas fixas que limitam seu lucro,
              a plataforma utiliza um <strong>Trailing Stop 100% dinâmico baseado em ATR</strong>. O Stop Loss de segurança inicial
              permanece estático para absorver a volatilidade. Assim que a operação atinge exatamente <strong>+1.0% de PnL</strong>,
              o gatilho é acionado, eliminando o Stop Loss antigo e acoplando uma "sombra" matemática que persegue o preço de perto
              enquanto a tendência durar, tornando <strong>impossível sair no prejuízo</strong> a partir desse ponto.
            </p>
          </div>
        </div>

        {/* Painel Semântico */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {[
            { prefix: 'TF', name: 'Trend Following', color: 'blue', desc: 'Seguir a tendência. Estratégias que capturam a <strong>inércia do movimento</strong> provocada por fluxos de capital institucional. EMA cross, MACD, momentum, breakout.' },
            { prefix: 'MR', name: 'Mean Reversion', color: 'purple', desc: 'Reversão à média. O preço é um <strong>elástico</strong>: quando esticado demais, tende a voltar. Bollinger fade, RSI extreme, desvio, correção.' },
            { prefix: 'PA', name: 'Price Action', color: 'orange', desc: 'Ação do preço. O gráfico conta a história: <strong>padrões, pivôs, CHoCH, suporte/resistência</strong>. Sem indicadores pesados, pura leitura de estrutura.' },
            { prefix: 'SC', name: 'Scalping', color: 'red', desc: 'Execução rápida. Captura movimentos de <strong>segundos a minutos</strong> no fluxo de ordens. VWAP, volume delta, opening range, microstructure.' },
            { prefix: 'RG', name: 'Regime', color: 'amber', desc: 'Estado de mercado. <strong>Conheça o terreno antes de lutar</strong>: classifica o ambiente (tendência, range, volatilidade) e adapta o comportamento.' },
            { prefix: 'IF', name: 'Information', color: 'teal', desc: 'Dados externos. Informação é poder: <strong>on-chain, DEX spread, whale flow, eventos</strong>. O que o preço não mostra, os dados revelam.' },
            { prefix: 'NW', name: 'Network', color: 'cyan', desc: 'Relações entre ativos. <strong>Nada existe isolado</strong>: correlação, lead-lag, PageRank, grafos de influência. O mercado é um ecossistema.' },
            { prefix: 'T', name: 'Test', color: 'gray', desc: 'Teste da plataforma. Estratégia de <strong>validação e demonstração</strong> do framework. Não usar em produção real.' },
          ].map(({ prefix, name, color, desc }) => (
            <div key={prefix} className={`card p-4 border-${color}-500/20 bg-${color}-500/5 hover:bg-${color}-500/10 transition-colors group`}>
              <div className="flex items-center gap-2 mb-2">
                <span className={`w-8 h-6 flex items-center justify-center rounded bg-${color}-500/20 text-${color}-400 text-[10px] font-bold group-hover:scale-110 transition-transform`}>{prefix}</span>
                <p className={`text-xs font-bold uppercase tracking-widest text-${color}-400`}>{name}</p>
              </div>
              <p className="text-[11px] leading-relaxed text-muted" dangerouslySetInnerHTML={{ __html: desc }} />
            </div>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="text-muted text-center py-16">{t('c.loading')}</div>
      ) : (() => {
        const operational = strategies.filter(s => !s.id.startsWith('T'))
        const testStrats  = strategies.filter(s => s.id.startsWith('T'))
        return (
          <>
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
              {operational.map(s => (
                <StrategyCard key={s.id} strategy={s} sortBy={sortBy}
                  score={calcOpportunityScore(s, regime)} regime={regime} />
              ))}
            </div>
            {testStrats.length > 0 && (
              <div className="space-y-4 pt-4">
                <div className="flex items-center gap-3">
                  <div className="h-px flex-1 bg-border/40" />
                  <span className="text-[10px] font-bold uppercase tracking-[0.25em] text-muted/60 px-2">
                    T — Testes
                  </span>
                  <div className="h-px flex-1 bg-border/40" />
                </div>
                <p className="text-xs text-muted/60 text-center">
                  Estratégias de diagnóstico. Percorrem o pipeline completo sem restrição de sinal.
                </p>
                <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
                  {testStrats.map(s => (
                    <StrategyCard key={s.id} strategy={s} sortBy={sortBy}
                      score={0} regime={regime} />
                  ))}
                </div>
              </div>
            )}
          </>
        )
      })()}
    </div>
  )
}
