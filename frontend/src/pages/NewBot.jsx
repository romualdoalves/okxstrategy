import React, { useState, useEffect, useRef } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getStrategies, createBot, rankAssets, getBots, getMarketClock } from '../api'
import { ArrowLeft, Sparkles, List, Loader2, AlertTriangle, CheckCircle2, Clock } from 'lucide-react'
import { useLanguage } from '../i18n/LanguageContext'
import AiUsageNotice from '../components/AiUsageNotice'

const SYMBOLS    = [
  'BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'XRP-USDT', 'BNB-USDT',
  'DOGE-USDT', 'ADA-USDT', 'AVAX-USDT', 'LINK-USDT', 'LTC-USDT',
  'DOT-USDT', 'UNI-USDT',
]
const TIMEFRAMES = ['1m','5m','15m','1h','4h','1D']
const MEDALS     = ['🥇','🥈','🥉','','','','','','','']

const LABEL_COLORS = {
  Excelente: 'text-green-400',
  Bom:       'text-blue-400',
  Moderado:  'text-yellow-400',
  Fraco:     'text-muted',
}

const LABEL_I18N_KEY = {
  Excelente: 'nb.label.excellent',
  Bom:       'nb.label.good',
  Moderado:  'nb.label.moderate',
  Fraco:     'nb.label.weak',
}

// ── Componente de card de ativo ranqueado ──────────────────────────────────────
function AssetCard({ asset, selected, onSelect, disabled, closedMarket }) {
  const { t } = useLanguage()
  const details = asset.details ?? {}
  const [baseSymbol, ...quoteParts] = asset.symbol.split('-')
  const suffix = quoteParts.join('-')
  const isDisabled = disabled || closedMarket
  return (
    <button
      type="button"
      onClick={() => !isDisabled && onSelect(asset.symbol)}
      disabled={isDisabled}
      className={`w-full text-left p-3 rounded-lg border transition-all ${
        isDisabled
          ? 'border-border opacity-40 cursor-not-allowed'
          : selected
          ? 'border-accent bg-accent/10'
          : 'border-border hover:border-muted/60'
      }`}
    >
      <div className="flex items-center justify-between">
        <span className={`text-sm font-medium ${isDisabled ? 'text-muted' : ''}`}>
          {MEDALS[asset.rank - 1]}{MEDALS[asset.rank - 1] ? ' ' : '    '}
          {baseSymbol}
          {suffix && (
            <span className="text-muted font-normal text-xs ml-1">
              -{suffix}
            </span>
          )}
        </span>
        <div className="flex items-center gap-2">
          {closedMarket
            ? <span className="text-[10px] font-bold text-red-400/70 bg-red-500/10 px-1.5 py-0.5 rounded border border-red-500/20">FECHADO</span>
            : disabled
            ? <span className="text-[10px] font-bold text-muted bg-white/5 px-1.5 py-0.5 rounded border border-white/10">EM USO</span>
            : <span className={`text-xs font-semibold ${LABEL_COLORS[asset.label] ?? 'text-muted'}`}>
                {t(LABEL_I18N_KEY[asset.label]) ?? asset.label}
              </span>
          }
          {selected && !isDisabled && <CheckCircle2 size={14} className="text-accent shrink-0" />}
        </div>
      </div>

      {/* Score bar */}
      <div className="mt-2 h-1 bg-border rounded-full">
        <div
          className="h-1 rounded-full transition-all"
          style={{
            width: `${Math.round(asset.score * 100)}%`,
            backgroundColor: disabled ? '#374151' : selected ? 'var(--color-accent, #22c55e)' : '#4b5563',
          }}
        />
      </div>

      {!details.error && !disabled && (
        <p className="text-xs text-muted mt-1.5">
          {details.metric === 'eigenvector_centrality'
            ? t('nb.metric_centrality', { score: asset.score.toFixed(3) })
            : t('nb.metric_atr', {
                atr:   details.atr_pct,
                trend: details.trend_strength_pct,
                mom:   details.momentum_pct,
              })
          }
        </p>
      )}
    </button>
  )
}

// ── Página principal ───────────────────────────────────────────────────────────
export default function NewBot() {
  const nav = useNavigate()
  const qc  = useQueryClient()
  const { t } = useLanguage()
  const [params] = useSearchParams()
  const defaultStrategy = params.get('strategy') ?? ''

  const { data: strategies = [] } = useQuery({
    queryKey: ['strategies'],
    queryFn:  getStrategies,
  })

  const { data: existingBots = [] } = useQuery({
    queryKey: ['bots'],
    queryFn:  getBots,
  })
  const usedSymbols = new Set(existingBots.map(b => b.symbol))

  const [form, setForm] = useState({
    name:            '',
    strategy_id:     defaultStrategy,
    symbol:          'BTC-USDT',
    timeframe:       '15m',
    demo:            true,
    stake_usd:       100,
    leverage:        1,
    stop_loss_usd:   -50,
    strategy_params: {},
  })
  const [customParams, setCustomParams] = useState({})
  const [error, setError]     = useState('')
  const [loading, setLoading] = useState(false)
  // Rastreia o último nome auto-preenchido para não sobrescrever edição manual
  const lastAutoNameRef = useRef('')

  const [assetMode,    setAssetMode]    = useState(null)
  const [rankLoading,  setRankLoading]  = useState(false)
  const [ranking,      setRanking]      = useState(null)

  // Verificação de mercado aberto para ativos não-crypto
  const isCrypto = form.symbol.includes('/') || form.symbol.includes('-')
  const { data: clock } = useQuery({
    queryKey:        ['market-clock'],
    queryFn:         getMarketClock,
    refetchInterval: 60_000,
    enabled:         !isCrypto,   // só busca para ações; crypto é sempre 24/7
  })
  const marketClosed = !isCrypto && clock && !clock.is_open

  const selectedStrategy = strategies.find(s => s.id === form.strategy_id)

  function setField(k, v) { setForm(f => ({ ...f, [k]: v })) }

  // Auto-ajuste de timeframe, símbolo e nome baseado na estratégia selecionada
  useEffect(() => {
    if (!selectedStrategy) return
    if (selectedStrategy.recommended_timeframe) {
      setField('timeframe', selectedStrategy.recommended_timeframe)
    }
    if (selectedStrategy.recommended_symbol) {
      setField('symbol', selectedStrategy.recommended_symbol)
      setAssetMode('manual')
    }
    // Preenche o nome com "ID - Nome da Estratégia" se o campo estiver vazio
    // ou se ainda tiver o valor auto-preenchido pela estratégia anterior
    if (!form.name || form.name === lastAutoNameRef.current) {
      setField('name', selectedStrategy.name)
      lastAutoNameRef.current = selectedStrategy.name
    }
  }, [form.strategy_id, strategies])

  useEffect(() => {
    setAssetMode(null)
    setRanking(null)
  }, [form.strategy_id, form.timeframe])

  async function fetchRanking() {
    if (!form.strategy_id) return
    setRankLoading(true)
    setAssetMode('auto')
    setError('')
    try {
      const data = await rankAssets(form.strategy_id, form.timeframe)
      setRanking(data)
      if (data.assets?.length > 0) {
        setField('symbol', data.assets[0].symbol)
      }
    } catch (err) {
      setError(t('nb.err_assets', { msg: err.message }))
      setAssetMode(null)
    } finally {
      setRankLoading(false)
    }
  }

  async function submit(e) {
    e.preventDefault()
    setError('')
    if (!form.name.trim()) return setError(t('nb.err_name'))
    if (!form.strategy_id) return setError(t('nb.err_strategy'))
    setLoading(true)
    try {
      const bot = await createBot({ ...form, strategy_params: customParams })
      qc.invalidateQueries({ queryKey: ['bots'] })
      nav(`/bots/${bot.id}`)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="p-6 max-w-2xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <button onClick={() => nav(-1)} className="btn-ghost btn p-2">
          <ArrowLeft size={16} />
        </button>
        <div>
          <h1 className="text-xl font-bold">{t('nb.title')}</h1>
          <p className="text-muted text-sm">{t('nb.subtitle')}</p>
        </div>
      </div>

      <form onSubmit={submit} className="space-y-5">

        {/* ── General Settings ───────────────────────────────────────────── */}
        <div className="card space-y-4">
          <h2 className="font-semibold text-sm">{t('nb.general')}</h2>

          <div>
            <label className="label">{t('nb.bot_name')}</label>
            <input
              className="input"
              placeholder={t('nb.name_ph')}
              value={form.name}
              onChange={e => setField('name', e.target.value)}
            />
          </div>

          <div>
            <label className="label">{t('nb.strategy')}</label>
            <select
              className="input"
              value={form.strategy_id}
              onChange={e => setField('strategy_id', e.target.value)}
            >
              <option value="">{t('nb.select')}</option>
              {(() => {
                const CATS = [
                  { prefix: 'TF', label: 'TF — Trend Following' },
                  { prefix: 'MR', label: 'MR — Mean Reversion' },
                  { prefix: 'PA', label: 'PA — Price Action' },
                  { prefix: 'SC', label: 'SC — Scalping' },
                  { prefix: 'RG', label: 'RG — Regime' },
                  { prefix: 'IF', label: 'IF — Information' },
                  { prefix: 'NW', label: 'NW — Network' },
                  { prefix: 'T', label: 'T — Testes' },
                ]
                return CATS.map(({ prefix, label }) => {
                  const group = strategies.filter(s => s.id.startsWith(prefix))
                  if (!group.length) return null
                  return (
                    <optgroup key={prefix} label={label}>
                      {group.map(s => (
                        <option key={s.id} value={s.id}>{s.name}</option>
                      ))}
                    </optgroup>
                  )
                })
              })()}
            </select>
            {selectedStrategy && (
              <p className="text-xs text-muted mt-1.5">{selectedStrategy.description}</p>
            )}
          </div>

          <div>
            <label className="label">{t('nb.timeframe')}</label>
            <select
              className="input"
              value={form.timeframe}
              onChange={e => setField('timeframe', e.target.value)}
            >
              {TIMEFRAMES.map(tf => <option key={tf}>{tf}</option>)}
            </select>
          </div>
        </div>

        {/* ── Asset Selection ────────────────────────────────────────────── */}
        {form.strategy_id && (
          <div className="card space-y-4">
            <h2 className="font-semibold text-sm">{t('nb.asset_section')}</h2>

            {assetMode === null && (
              <div className="space-y-3">
                <p className="text-sm text-muted">{t('nb.auto_ask')}</p>
                <AiUsageNotice consumesTokens={false} text={t('nb.local_ranking_notice')} />
                <div className="flex gap-3">
                  <button
                    type="button"
                    onClick={fetchRanking}
                    className="btn-primary btn flex-1 flex items-center justify-center gap-2"
                  >
                    <Sparkles size={14} />
                    {t('nb.auto_yes')}
                  </button>
                  <button
                    type="button"
                    onClick={() => setAssetMode('manual')}
                    className="btn-ghost btn flex-1 flex items-center justify-center gap-2"
                  >
                    <List size={14} />
                    {t('nb.auto_no')}
                  </button>
                </div>
              </div>
            )}

            {assetMode === 'auto' && rankLoading && (
              <div className="flex items-center justify-center gap-2 text-muted text-sm py-6">
                <Loader2 size={16} className="animate-spin" />
                {t('nb.analyzing', { n: 100 })}
              </div>
            )}

            {assetMode === 'auto' && !rankLoading && ranking && (
              <div className="space-y-3">

                {ranking.warning && (
                  <div className="flex items-start gap-2 text-yellow-400 text-xs bg-yellow-400/10 border border-yellow-400/20 rounded-lg p-2.5">
                    <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                    {ranking.warning}
                  </div>
                )}

                {ranking.graph_info && (
                  <div className="text-xs text-muted bg-surface rounded-lg px-3 py-2 flex flex-wrap gap-x-4 gap-y-1">
                    <span>
                      {t('nb.graph_regime')}{' '}
                      <strong className="text-white">
                        {t('nb.regime.' + ranking.graph_info.regime) ?? ranking.graph_info.regime}
                      </strong>
                    </span>
                    <span>
                      {t('nb.graph_conf')}{' '}
                      <strong className="text-white">
                        {(ranking.graph_info.regime_conf * 100).toFixed(0)}%
                      </strong>
                    </span>
                    <span>
                      {t('nb.graph_density')}{' '}
                      <strong className="text-white">{ranking.graph_info.density}</strong>
                    </span>
                    <span>
                      {t('nb.graph_edges')}{' '}
                      <strong className="text-white">{ranking.graph_info.n_edges}/10</strong>
                    </span>
                  </div>
                )}

                <div className="space-y-2">
                  {ranking.assets.map(asset => {
                    const assetIsCrypto = asset.symbol.includes('/') || asset.symbol.includes('-')
                    const assetClosed   = !assetIsCrypto && clock && !clock.is_open
                    return (
                      <AssetCard
                        key={asset.symbol}
                        asset={asset}
                        selected={form.symbol === asset.symbol}
                        onSelect={sym => setField('symbol', sym)}
                        disabled={usedSymbols.has(asset.symbol)}
                        closedMarket={assetClosed}
                      />
                    )
                  })}
                </div>

                <p className="text-xs text-muted">{ranking.note}</p>

                <div className="flex items-center gap-3 text-xs">
                  <button
                    type="button"
                    onClick={fetchRanking}
                    className="text-muted hover:text-white transition-colors"
                  >
                    {t('nb.re_analyze')}
                  </button>
                  <span className="text-border">·</span>
                  <button
                    type="button"
                    onClick={() => setAssetMode('manual')}
                    className="text-muted hover:text-white transition-colors"
                  >
                    {t('nb.choose_manually')}
                  </button>
                </div>
              </div>
            )}

            {assetMode === 'manual' && (
              <div className="space-y-2">
                <AiUsageNotice consumesTokens={false} text={t('nb.local_ranking_notice')} />
                <select
                  className="input"
                  value={form.symbol}
                  onChange={e => setField('symbol', e.target.value)}
                >
                  <optgroup label="OKX Spot — sem alavancagem">
                    {[
                      ['BTC-USDT','Bitcoin'],['ETH-USDT','Ethereum'],
                      ['SOL-USDT','Solana'],['XRP-USDT','Ripple'],
                      ['BNB-USDT','BNB'],['DOGE-USDT','Dogecoin'],
                      ['ADA-USDT','Cardano'],['AVAX-USDT','Avalanche'],
                      ['LINK-USDT','Chainlink'],['LTC-USDT','Litecoin'],
                      ['DOT-USDT','Polkadot'],['UNI-USDT','Uniswap'],
                    ].map(([sym, name]) => (
                      <option key={sym} value={sym} disabled={usedSymbols.has(sym)}>
                        {sym} ({name}){usedSymbols.has(sym) ? ' — em uso' : ''}
                      </option>
                    ))}
                  </optgroup>
                </select>
                <p className="text-[10px] text-muted/60">
                  Não encontrou? Digite diretamente no campo acima o instId spot OKX (ex: OP-USDT).
                </p>
                <button
                  type="button"
                  onClick={() => { setAssetMode(null); setRanking(null) }}
                  className="text-xs text-muted hover:text-white transition-colors"
                >
                  {t('nb.use_auto')}
                </button>
              </div>
            )}
          </div>
        )}

        {/* ── Account & Risk ─────────────────────────────────────────────── */}
        <div className="card space-y-4">
          <h2 className="font-semibold text-sm">{t('nb.account_risk')}</h2>

          <div className="flex gap-3">
            {[true, false].map(val => (
              <button
                key={String(val)}
                type="button"
                onClick={() => setField('demo', val)}
                className={`flex-1 py-2 rounded-lg border text-sm font-medium transition-all
                  ${form.demo === val
                    ? val
                      ? 'border-accent bg-accent/15 text-accent'
                      : 'border-red-500 bg-red-500/15 text-red-400'
                    : 'border-border text-muted hover:border-muted'}`}
              >
                {val ? t('nb.demo_btn') : t('nb.live_btn')}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label">{t('nb.stake')}</label>
              <input className="input" type="number" min="1" step="1"
                value={form.stake_usd}
                onChange={e => setField('stake_usd', Number(e.target.value))} />
            </div>
            <div>
              <label className="label">{t('nb.daily_stop')}</label>
              <input className="input" type="number" max="0" step="1"
                value={form.stop_loss_usd}
                onChange={e => setField('stop_loss_usd', Number(e.target.value))} />
            </div>
          </div>

          <p className="text-xs text-muted">
            {t('nb.position_calc', {
              stake: form.stake_usd,
            })}
          </p>
        </div>

        {/* ── Strategy Parameters ────────────────────────────────────────── */}
        {selectedStrategy && Object.keys(selectedStrategy.params).length > 0 && (
          <div className="card space-y-4">
            <h2 className="font-semibold text-sm">{t('nb.strat_params')}</h2>
            <p className="text-xs text-muted">{t('nb.params_hint')}</p>
            <div className="grid grid-cols-2 gap-4">
              {Object.entries(selectedStrategy.params).map(([k, p]) => (
                <div key={k}>
                  <label className="label">{k}</label>
                  <input
                    className="input"
                    type={p.type === 'bool' ? 'checkbox' : 'number'}
                    placeholder={t('nb.param_ph', { v: p.default })}
                    step={p.step ?? (p.type === 'int' ? 1 : 0.1)}
                    min={p.min ?? undefined}
                    max={p.max ?? undefined}
                    onChange={e => setCustomParams(prev => ({
                      ...prev,
                      [k]: p.type === 'int'
                        ? parseInt(e.target.value)
                        : parseFloat(e.target.value),
                    }))}
                  />
                  <p className="text-xs text-muted mt-0.5">{p.description}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Aviso: mercado fechado para ações */}
        {marketClosed && (
          <div className="flex items-start gap-3 rounded-xl border border-red-500/30 bg-red-500/8 p-4">
            <Clock size={18} className="text-red-400 shrink-0 mt-0.5" />
            <div className="space-y-1">
              <p className="text-sm font-semibold text-red-400">Mercado de Ações Fechado</p>
              <p className="text-xs text-red-300/70">
                O ativo <strong>{form.symbol}</strong> é negociado apenas durante o horário de pregão da NYSE/NASDAQ.
                A bolsa está fechada agora — o bot não conseguirá executar ordens.
              </p>
              {clock?.next_open && (
                <p className="text-xs text-muted">
                  Próxima abertura: <strong className="text-white/70">{new Date(clock.next_open).toLocaleString('pt-BR')}</strong>
                </p>
              )}
              <p className="text-xs text-muted mt-1">
                Criptomoedas (BTC/USD, ETH/USD, SOL/USD...) operam 24/7 sem restrição de horário.
              </p>
            </div>
          </div>
        )}

        {error && <p className="text-red-400 text-sm">{error}</p>}

        <div className="flex gap-3">
          <button type="button" onClick={() => nav(-1)} className="btn-ghost btn flex-1">
            {t('c.cancel')}
          </button>
          <button
            type="submit"
            disabled={loading || marketClosed}
            className={`btn flex-1 ${marketClosed ? 'btn-ghost opacity-50 cursor-not-allowed' : 'btn-primary'}`}
            title={marketClosed ? 'Mercado fechado — selecione um ativo crypto ou aguarde a abertura' : undefined}
          >
            {loading ? t('nb.creating') : marketClosed ? '🔒 Mercado Fechado' : t('nb.create')}
          </button>
        </div>
      </form>
    </div>
  )
}
