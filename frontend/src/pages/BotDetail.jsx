import React, { useCallback, useEffect, useRef, useState, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Play, Square, Trash2, Wallet, Sparkles, AlertTriangle, Clock, Info, TrendingUp, ChevronDown, ChevronUp, Zap, BarChart, CheckCircle, XCircle } from 'lucide-react'
import { getBot, getTrades, getTradesSummary, getCandles, getBalance, getGraphState, getGraphInterpretation, getSignalLogsReadiness, getSignalLogsAnalysis, startBot, stopBot, deleteBot, getBotAiAnalysis, getAiUsageStatus, runBacktest } from '../api'
import { useWebSocket } from '../hooks/useWebSocket'
import { useMarketStatus } from '../hooks/useMarketStatus'
import { useLanguage } from '../i18n/LanguageContext'
import Chart             from '../components/Chart'
import TradeTable        from '../components/TradeTable'
import StatCard          from '../components/StatCard'
import GraphView         from '../components/GraphView'
import StrategyChecklist from '../components/StrategyChecklist'
import AssetPriceCard    from '../components/AssetPriceCard'
import AiUsageNotice     from '../components/AiUsageNotice'

const CHART_SUBTITLE = {
  TF001: 'EMA 21/55 + VWAP',
  TF002: 'RSI + MA200',
  TF013: 'Bollinger Bands',
  TF003: 'MACD + Trend MA',
  TF004: 'SuperTrend',
  TF005: 'Joe Di Napoli',
  TF011: 'EMA Fast + Slow (CFM)',
  PA002: 'ABCD + EMA21',
  TF007: 'Multi-TF EMA 4H/1H + MACD',
  MR001: 'Mean Reversion EMA',
  TF008: 'EMA 21 Semanal',
  PA001: 'Pivot Sniper + EMA21/50',
  PA003: 'OTR Value Zones',
  SC001: 'Opening Range High/Low',
  TF010: 'Stochastic Reversal',
  PA004: 'Three Line Bar + EMA200',
  MR002: 'Double Bollinger Bands',
  MR005: 'Bollinger Triple Threat',
  PA005: 'Liquidity Sweep',
  TF012: 'Liquidity Clusters',
  MR003: 'Warrior Trading Reversal',
  MR004: 'Hilega Milega RSI',
  RG002: 'Graph Regime EMA Fast/Slow',
  SC003: 'Whale Flow VWAP + VAH/VAL',
  IF001: 'Onchain Whale Context',
  IF003: 'Event-Driven Flow',
  NW001: 'Influencers and Followers',
  RG001: 'Markov Regime + EMA21',
}

function getBacktestRecommendation(r) {
  const pf = r.profit_factor ?? 0
  const tc = r.trades_count ?? 0
  const tp = r.total_profit ?? 0
  const dd = r.max_drawdown ?? 0

  if (tc === 0) return {
    verdict: 'NÃO INICIAR', level: 'danger',
    reasons: ['Nenhum trade fechado no período — impossível avaliar a estratégia.'],
  }
  if (tp <= 0) return {
    verdict: 'NÃO INICIAR', level: 'danger',
    reasons: [`PnL negativo (${tp.toFixed(2)} USD) — estratégia perdedora neste período.`],
  }
  if (pf < 1.0) return {
    verdict: 'NÃO INICIAR', level: 'danger',
    reasons: [`Profit Factor ${pf.toFixed(2)}: as perdas superam os ganhos brutos.`],
  }

  const highlights = []
  const issues = []

  highlights.push(`PnL positivo: +${tp.toFixed(2)} USD.`)

  if (pf >= 1.5) {
    highlights.push(`Profit Factor excelente: ${pf.toFixed(2)}.`)
  } else if (pf >= 1.2) {
    highlights.push(`Profit Factor adequado: ${pf.toFixed(2)} (idealmente acima de 1.5).`)
  } else {
    issues.push(`Profit Factor ${pf.toFixed(2)} abaixo do mínimo recomendado (1.2).`)
  }

  if (tc >= 5) {
    highlights.push(`${tc} trades fechados — amostra razoável.`)
  } else if (tc >= 3) {
    highlights.push(`${tc} trades fechados (mínimo aceitável).`)
  } else {
    issues.push(`Apenas ${tc} trade(s) fechado(s) — amostra insuficiente para confiança estatística.`)
  }

  const ddRatio = tp > 0 ? dd / tp : 999
  if (ddRatio <= 0.5) {
    highlights.push(`Drawdown controlado: $${dd.toFixed(2)} (${(ddRatio * 100).toFixed(0)}% do lucro).`)
  } else if (ddRatio <= 1.0) {
    issues.push(`Drawdown de $${dd.toFixed(2)} representa ${(ddRatio * 100).toFixed(0)}% do lucro total — elevado.`)
  } else {
    issues.push(`Drawdown de $${dd.toFixed(2)} supera o lucro total — risco muito elevado.`)
  }

  if (issues.length === 0) {
    return { verdict: 'INICIAR', level: 'success', reasons: highlights }
  }
  return { verdict: 'CUIDADO', level: 'warning', reasons: [...highlights, ...issues] }
}

function useElapsed(startedAt) {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!startedAt) { setElapsed(0); return }
    const origin = new Date(startedAt).getTime()
    const tick   = () => setElapsed(Math.max(0, Math.floor((Date.now() - origin) / 1000)))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [startedAt])
  if (!startedAt) return null
  const h = Math.floor(elapsed / 3600)
  const m = Math.floor((elapsed % 3600) / 60)
  const s = elapsed % 60
  const pad = n => String(n).padStart(2, '0')
  return h > 0 ? `${h}h ${pad(m)}m ${pad(s)}s` : `${m}m ${pad(s)}s`
}

export default function BotDetail() {
  const { id } = useParams()
  const nav    = useNavigate()
  const qc     = useQueryClient()
  const { t, lang } = useLanguage()
  const locale = lang === 'en-US' ? 'en-US' : 'pt-BR'
  const { isOpen: isMarketOpen, opensIn } = useMarketStatus()
  
  const { data: bot }     = useQuery({ queryKey: ['bot', id],     queryFn: () => getBot(id) })

  // Ativos de Cripto (com barra, traço ou tickers conhecidos) ignoram a trava de mercado fechado
  const isCrypto = bot?.symbol?.includes('/') || bot?.symbol?.includes('-') || 
    ['BTC', 'ETH', 'SOL', 'XRP', 'BNB', 'LTC', 'DOGE', 'ADA', 'DOT'].some(c => bot?.symbol?.toUpperCase().includes(c))
  const isOpen = isCrypto ? true : isMarketOpen
  const { data: aiUsage } = useQuery({ queryKey: ['ai-usage'], queryFn: getAiUsageStatus })

  const isRunning = bot?.runtime?.status === 'running'

  const { data: trades = [] } = useQuery({ queryKey: ['trades', id], queryFn: () => getTrades(id) })
  const { data: summary = {} } = useQuery({ queryKey: ['summary', id], queryFn: () => getTradesSummary(id) })
  const { data: graphState }    = useQuery({
    queryKey: ['graph', id],
    queryFn:  () => getGraphState(id),
    refetchInterval: isRunning ? 30_000 : false,
    enabled:  !!bot,
  })

  const [optimizeOpen, setOptimizeOpen]       = useState(false)
  const [analysis, setAnalysis]               = useState(null)
  const [analysisLoading, setAnalysisLoading] = useState(false)

  const { data: signalReadiness } = useQuery({
    queryKey: ['signal-readiness', id],
    queryFn:  () => getSignalLogsReadiness(id),
    refetchInterval: isRunning ? 30_000 : false,
    enabled: !!bot,
  })
  const optimizeReady = signalReadiness?.ready === true

  const fetchAnalysis = useCallback(async () => {
    if (!optimizeReady || analysisLoading) return
    setAnalysisLoading(true)
    try {
      const data = await getSignalLogsAnalysis(id)
      setAnalysis(data)
      setOptimizeOpen(true)
    } catch (e) {
      setAnalysis({ error: e.message })
      setOptimizeOpen(true)
    } finally {
      setAnalysisLoading(false)
    }
  }, [optimizeReady, analysisLoading, id])

  const [interp, setInterp]             = useState(null)
  const [interpLoading, setInterpLoading] = useState(false)
  const [backtestResult, setBacktestResult] = useState(null)
  const [backtestLoading, setBacktestLoading] = useState(false)

  const handleBacktest = async () => {
    setBacktestLoading(true)
    try {
      const res = await runBacktest(id)
      setBacktestResult(res)
    } catch (e) {
      alert("Falha no Backtest: " + e.message)
    } finally {
      setBacktestLoading(false)
    }
  }
  const lastInterpCandleTs              = useRef(null)

  const candleTs    = graphState?.last_candle_ts ?? null
  const interpReady = candleTs !== null && candleTs !== lastInterpCandleTs.current

  const generateInterp = useCallback(async () => {
    if (!interpReady || interpLoading) return
    setInterpLoading(true)
    try {
      const data = await getGraphInterpretation(id)
      setInterp({ text: data.interpretation, source: data.source })
      lastInterpCandleTs.current = candleTs
    } catch (e) {
      setInterp({ text: t('c.error', { msg: e.message }), source: 'error' })
    } finally {
      setInterpLoading(false)
    }
  }, [interpReady, interpLoading, id, candleTs, t])

  const { data: balance }       = useQuery({
    queryKey: ['balance', 'demo'],
    queryFn:  () => getBalance('USDT', true),   // demo=true: mesma conta dos bots
    refetchInterval: 15_000,
  })

  const { data: candleData }   = useQuery({
    queryKey: ['candles', bot?.symbol, bot?.timeframe],
    queryFn:  () => getCandles(bot.symbol, bot.timeframe),
    enabled:  !!bot,
  })

  const [livePrice, setLivePrice]             = useState(null)
  const [liveIndicators, setLiveIndicators]   = useState(null)
  const [liveHoldReason, setLiveHoldReason]   = useState('')
  const [liveOrderCriteria, setLiveOrderCriteria] = useState(null)
  const [liveMaintenance, setLiveMaintenance] = useState(null)
  const wsMsg = useWebSocket()

  useEffect(() => {
    if (!wsMsg || wsMsg.bot_id !== Number(id)) return
    if (wsMsg.type === 'price')      setLivePrice(wsMsg.price)
    if (wsMsg.type === 'indicators') {
      setLiveIndicators(wsMsg.indicators)
      setLiveHoldReason(wsMsg.hold_reason ?? '')
      setLiveOrderCriteria(wsMsg.order_criteria ?? null)
    }
    if (wsMsg.type === 'maintenance')     setLiveMaintenance(wsMsg.msg)
    if (wsMsg.type === 'maintenance_end') setLiveMaintenance(null)
    // Invalida queries de trades quando houver evento de trade (entry/exit/tp1)
    if (wsMsg.type === 'trade') {
      qc.invalidateQueries({ queryKey: ['trades', id] })
      qc.invalidateQueries({ queryKey: ['summary', id] })
    }
  }, [wsMsg, id, qc])

  const [diagnostico, setDiagnostico] = useState(null)
  const [diagnosticoLoading, setDiagnosticoLoading] = useState(false)

  const elapsed = useElapsed(
    bot?.runtime?.status === 'running' ? bot?.runtime?.started_at : null
  )

  const aiAnalysisConsumesTokens = aiUsage?.actions?.bot_ai_analysis?.consumes_ai_tokens === true
  const graphInterpConsumesTokens = aiUsage?.actions?.graph_interpretation?.consumes_ai_tokens === true

  if (!bot) return <div className="p-6 text-muted">{t('bd.loading')}</div>

  const rt      = bot.runtime ?? {}
  const candles = candleData?.data ?? []
  const pnl     = rt.daily_pnl ?? 0
  const rawIndicators = liveIndicators ?? rt.last_indicators ?? null
  const indicators = rawIndicators && Object.keys(rawIndicators).length > 0 ? rawIndicators : null
  const holdReason  = liveHoldReason || rt.hold_reason || ''
  const orderCriteria = liveOrderCriteria || rt.order_criteria || null
  const botWithLiveRuntime = {
    ...bot,
    runtime: {
      ...(bot.runtime ?? {}),
      order_criteria: orderCriteria,
    },
  }
  const maintenance = liveMaintenance || rt.maintenance || null
  const isMaintenance = rt.status === 'maintenance' || !!maintenance

  async function toggle() {
    if (isRunning) await stopBot(id)
    else           await startBot(id)
    qc.invalidateQueries({ queryKey: ['bot', id] })
    qc.invalidateQueries({ queryKey: ['bots'] })
  }

  async function remove() {
    if (!confirm(t('bd.delete_confirm', { name: bot.name }))) return
    await deleteBot(id)
    qc.invalidateQueries({ queryKey: ['bots'] })
    nav('/bots')
  }

  async function fetchDiagnostico() {
    setDiagnosticoLoading(true)
    setDiagnostico(null)
    try {
      const res = await getBotAiAnalysis(id)
      setDiagnostico(res.analysis)
    } catch (err) {
      setDiagnostico("Não foi possível gerar a análise. Tente novamente.")
    } finally {
      setDiagnosticoLoading(false)
    }
  }

  return (
    <div className="p-6 space-y-6">
      {/* ── Maintenance banner ────────────────────────────────────────────── */}
      {isMaintenance && (
        <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-yellow-500/10 border border-yellow-500/30 text-yellow-400">
          <AlertTriangle size={16} className="shrink-0 animate-pulse" />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold">{t('bd.maintenance')}</p>
            <p className="text-xs text-yellow-400/70 truncate">{maintenance || t('bd.maint_ongoing')}</p>
          </div>
          <Clock size={14} className="shrink-0 opacity-60" />
          <span className="text-xs opacity-60 whitespace-nowrap">{t('bd.checking')}</span>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button onClick={() => nav(-1)} className="btn-ghost btn p-2">
            <ArrowLeft size={16} />
          </button>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-bold">{bot.name}</h1>
              <span className={`w-2 h-2 rounded-full ${isRunning ? 'bg-bull animate-pulse' : 'bg-border'}`} />
              <span className={bot.demo ? 'badge-muted' : 'badge-bull'}>
                {bot.demo ? t('c.demo') : t('c.live')}
              </span>
            </div>
            <div className="flex items-center gap-3 mt-0.5">
              <p className="text-muted text-sm">{bot.symbol} · {bot.timeframe} · {bot.strategy_id}</p>
              {isRunning && rt.started_at && (
                <span className="flex items-center gap-1 text-xs text-muted/70">
                  <Clock size={11} />
                  {new Date(rt.started_at).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })}
                  {elapsed && <span className="text-accent font-mono">{elapsed}</span>}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="flex gap-2">
          <button 
            onClick={() => nav(`/bots/${id}/edit`)}
            disabled={Math.abs(rt.direction || 0) > 0}
            className="btn flex items-center gap-1.5 text-sm bg-orange-600 text-white hover:bg-orange-700 border-none shadow-lg"
          >
            <Zap size={14} />
            Reconfigurar
          </button>
          
          <button 
            onClick={handleBacktest}
            disabled={backtestLoading}
            className="btn flex items-center gap-2 bg-blue-600 text-white hover:bg-blue-700 shadow-[0_0_20px_rgba(37,99,235,0.4)] transition-all"
          >
            {backtestLoading ? (
              <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
            ) : (
              <BarChart size={16} />
            )}
            BACKTEST
          </button>

          <button 
            onClick={fetchDiagnostico} 
            disabled={diagnosticoLoading}
            title={aiAnalysisConsumesTokens ? t('bd.tokens_ai_analysis') : t('bd.no_tokens_ai_analysis')}
            className="btn flex items-center gap-1.5 text-sm bg-accent/10 text-accent hover:bg-accent/20 border border-accent/20"
          >
            <Sparkles size={14} className={diagnosticoLoading ? "animate-spin" : ""} />
            {diagnosticoLoading ? "Analisando..." : "Analisar com IA"}
          </button>
          <button onClick={toggle}
            disabled={!isOpen}
            title={!isOpen ? `${t('market.ops_suspended')} · ${t('market.opens_in', { time: opensIn })}` : undefined}
            className={`btn flex items-center gap-1.5 text-sm ${isRunning ? 'btn-danger' : 'btn-success'} ${!isOpen ? 'opacity-40 cursor-not-allowed' : ''}`}>
            {isRunning
              ? <><Square size={14} /> {t('c.stop')}</>
              : <><Play   size={14} /> {t('c.start')}</>
            }
          </button>
          <button onClick={remove} className="btn-ghost btn p-2 text-bear hover:bg-bear/10">
            <Trash2 size={16} />
          </button>
        </div>
      </div>

      <div className="grid gap-2 md:grid-cols-3">
        <AiUsageNotice
          consumesTokens={aiAnalysisConsumesTokens}
          text={aiAnalysisConsumesTokens ? t('bd.tokens_ai_analysis') : t('bd.no_tokens_ai_analysis')}
        />
        <AiUsageNotice
          consumesTokens={graphInterpConsumesTokens}
          text={graphInterpConsumesTokens ? t('bd.tokens_graph_interp') : t('bd.no_tokens_graph_interp')}
        />
        <AiUsageNotice
          consumesTokens={false}
          text={t('bd.no_tokens_optimize')}
        />
      </div>

      {/* Backtest Result Premium Panel */}
      {backtestResult && (
        <div className="p-6 rounded-2xl border border-accent/20 bg-panel/80 backdrop-blur-md shadow-2xl animate-in fade-in slide-in-from-top-4 duration-500 mb-6">
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-accent/20">
                <BarChart size={20} className="text-accent" />
              </div>
              <h3 className="text-lg font-bold text-white">Relatório de Backtest (Últimas 500 Velas)</h3>
            </div>
            <button 
              onClick={() => setBacktestResult(null)}
              className="p-1 hover:bg-white/10 rounded-full transition-colors"
            >
              <ChevronUp size={20} className="text-muted" />
            </button>
          </div>

          {backtestResult.assumptions?.length > 0 && (
            <div className="mb-4 rounded-lg border border-yellow-500/25 bg-yellow-500/8 p-3 text-xs text-yellow-300/90 space-y-1">
              {backtestResult.assumptions.map((item, i) => (
                <p key={i}>· {item}</p>
              ))}
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-6 gap-4 mb-6">
            <div className="p-4 rounded-xl bg-white/5 border border-white/10">
              <p className="text-xs text-muted uppercase tracking-wider mb-1">Lucro Total Estimado</p>
              <p className={`text-2xl font-bold ${backtestResult.total_profit >= 0 ? 'text-bull' : 'text-bear'}`}>
                {backtestResult.total_profit >= 0 ? '+' : ''}{backtestResult.total_profit.toFixed(2)} USD
              </p>
            </div>
            <div className="p-4 rounded-xl bg-white/5 border border-white/10">
              <p className="text-xs text-muted uppercase tracking-wider mb-1">Trades Fechados</p>
              <p className="text-2xl font-bold text-white">{backtestResult.trades_count}</p>
            </div>
            <div className="p-4 rounded-xl bg-white/5 border border-white/10">
              <p className="text-xs text-muted uppercase tracking-wider mb-1">Saldo Final Simulado</p>
              <p className="text-2xl font-bold text-accent">${backtestResult.final_balance.toFixed(2)}</p>
            </div>
            <div className="p-4 rounded-xl bg-white/5 border border-white/10">
              <p className="text-xs text-muted uppercase tracking-wider mb-1">Win Rate</p>
              <p className={`text-2xl font-bold ${(backtestResult.win_rate ?? 0) >= 50 ? 'text-bull' : 'text-bear'}`}>
                {(backtestResult.win_rate ?? 0).toFixed(1)}%
              </p>
              <p className="text-[10px] text-muted">{backtestResult.wins ?? 0}W · {backtestResult.losses ?? 0}L</p>
            </div>
            <div className="p-4 rounded-xl bg-white/5 border border-white/10">
              <p className="text-xs text-muted uppercase tracking-wider mb-1">Profit Factor</p>
              <p className={`text-2xl font-bold ${(backtestResult.profit_factor ?? 0) >= 1 ? 'text-bull' : 'text-bear'}`}>
                {(backtestResult.profit_factor ?? 0).toFixed(2)}
              </p>
            </div>
            <div className="p-4 rounded-xl bg-white/5 border border-white/10">
              <p className="text-xs text-muted uppercase tracking-wider mb-1">Drawdown Máx.</p>
              <p className="text-2xl font-bold text-bear">${(backtestResult.max_drawdown ?? 0).toFixed(2)}</p>
              <p className="text-[10px] text-muted">Pior flutuação aberta</p>
            </div>
          </div>

          {/* Recommendation block */}
          {(() => {
            const rec = getBacktestRecommendation(backtestResult)
            const cfg = {
              success: { border: 'border-green-500/30', bg: 'bg-green-500/10', title: 'text-green-400', badge: 'bg-green-500/20 text-green-300', Icon: CheckCircle },
              warning: { border: 'border-yellow-500/30', bg: 'bg-yellow-500/10', title: 'text-yellow-400', badge: 'bg-yellow-500/20 text-yellow-300', Icon: AlertTriangle },
              danger:  { border: 'border-red-500/30',   bg: 'bg-red-500/10',   title: 'text-red-400',   badge: 'bg-red-500/20 text-red-300',   Icon: XCircle },
            }[rec.level]
            return (
              <div className={`mb-4 rounded-xl border ${cfg.border} ${cfg.bg} p-4`}>
                <div className="flex items-center gap-2 mb-3">
                  <cfg.Icon size={16} className={cfg.title} />
                  <span className={`text-sm font-bold uppercase tracking-wide ${cfg.title}`}>Recomendação</span>
                  <span className={`ml-auto px-3 py-0.5 rounded-full text-xs font-bold ${cfg.badge}`}>
                    {rec.verdict}
                  </span>
                </div>
                <ul className="space-y-1.5">
                  {rec.reasons.map((item, i) => (
                    <li key={i} className="text-xs text-white/75 flex gap-2">
                      <span className={`shrink-0 ${cfg.title}`}>·</span>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )
          })()}

          {backtestResult.open_position && (
            <div className="mb-4 rounded-lg border border-blue-500/25 bg-blue-500/8 p-3 text-xs text-blue-300/90">
              Há posição simulada ainda aberta: entrada ${backtestResult.open_position.entry_price.toFixed(4)},
              último preço ${backtestResult.open_position.last_price.toFixed(4)},
              PnL não realizado {backtestResult.open_position.unrealized >= 0 ? '+' : ''}${backtestResult.open_position.unrealized.toFixed(2)}.
            </div>
          )}

          <div className="space-y-3 max-h-48 overflow-y-auto pr-2 custom-scrollbar">
            <h4 className="text-xs font-bold text-muted uppercase">Histórico de Sinais Simulados</h4>
            {backtestResult.trades.slice().reverse().map((trade, i) => (
              <div key={i} className="flex items-center justify-between p-3 rounded-lg bg-white/5 border border-white/5 hover:bg-white/10 transition-colors">
                <div className="flex items-center gap-3">
                  <span className={`w-2 h-2 rounded-full ${trade.type.includes('ENTRY') ? 'bg-accent' : trade.profit >= 0 ? 'bg-bull' : 'bg-bear'}`} />
                  <div>
                    <p className="text-sm font-medium text-white">{trade.type}</p>
                    <p className="text-[10px] text-muted">{trade.reason || 'Sinal Técnico'}</p>
                  </div>
                </div>
                <div className="text-right">
                  <p className="text-sm font-mono text-white">${trade.price.toFixed(4)}</p>
                  {trade.profit !== undefined && (
                    <p className={`text-[10px] font-bold ${trade.profit >= 0 ? 'text-bull' : 'text-bear'}`}>
                      {trade.profit >= 0 ? '+' : ''}{trade.profit.toFixed(2)} USD
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* AI Diagnostic Banner */}
      {(diagnostico || diagnosticoLoading) && (
        <div className="flex items-start gap-3 p-4 rounded-xl border border-accent/30 bg-accent/5">
          <Sparkles size={18} className="shrink-0 text-accent mt-0.5" />
          <div className="flex-1 text-sm text-white/90">
            {diagnosticoLoading ? (
              <p className="animate-pulse">A Inteligência Artificial está analisando o contexto em tempo real...</p>
            ) : (
              <p className="whitespace-pre-wrap leading-relaxed">{diagnostico}</p>
            )}
          </div>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label={t('bd.balance')}
          value={balance?.available != null ? `$${Number(balance.available).toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—'}
          sub={bot.demo ? t('bd.simulated') : t('c.live')}
          color="white"
          icon={Wallet} />
        <StatCard label={t('c.pnl_today')}
          value={`${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`}
          color={pnl >= 0 ? 'bull' : 'bear'}
          sub={`${rt.wins ?? 0}W · ${rt.losses ?? 0}L`} />
        <StatCard label={t('c.position')}
          value={rt.direction === 1 ? t('c.long') : rt.direction === -1 ? t('c.short') : t('c.flat')}
          color={rt.direction === 1 ? 'bull' : rt.direction === -1 ? 'bear' : 'white'}
          sub={rt.entry_price ? t('bd.entry_price', { price: Number(rt.entry_price).toLocaleString(locale) }) : ''} />
        <StatCard label={t('c.win_rate')}
          value={`${(summary.win_rate ?? 0).toFixed(1)}%`}
          sub={t('bd.trades_total', { n: summary.total_trades ?? 0 })}
          color={(summary.win_rate ?? 0) >= 50 ? 'bull' : 'bear'} />
      </div>

      {/* Asset price card */}
      <AssetPriceCard symbol={bot.symbol} livePrice={livePrice} />

      {/* Entry criteria checklist */}
      <div className="card space-y-6">
        <StrategyChecklist
          bot={botWithLiveRuntime}
          indicators={indicators}
          mode="entry"
        />
        <StrategyChecklist
          bot={botWithLiveRuntime}
          indicators={indicators}
          mode="order"
        />
        {Math.abs(rt.direction ?? 0) > 0 && (
          <StrategyChecklist
            bot={botWithLiveRuntime}
            indicators={indicators}
            mode="exit"
          />
        )}
      </div>

      {/* Open position */}
      {rt.direction !== 0 && (
        <div className="card border-accent/30 bg-accent/5 grid grid-cols-3 gap-4 text-sm">
          <div>
            <p className="text-muted text-xs mb-1">{t('bd.sl')}</p>
            <p className="text-bear font-semibold">${rt.sl_price ? Number(rt.sl_price).toLocaleString(locale) : '—'}</p>
          </div>
          <div>
            <p className="text-muted text-xs mb-1">{t('bd.tp1')}</p>
            <p className="text-bull font-semibold">${rt.tp1_price ? Number(rt.tp1_price).toLocaleString(locale) : '—'}</p>
          </div>
          <div>
            <p className="text-muted text-xs mb-1">{t('bd.tp1_hit')}</p>
            <p className="font-semibold">{rt.tp1_done ? t('bd.tp1_yes') : t('bd.tp1_no')}</p>
          </div>
        </div>
      )}

      {/* Hold reason */}
      {isRunning && rt.direction === 0 && !rt.halted && holdReason && (
        <div className="flex items-center gap-2.5 px-4 py-2.5 rounded-lg bg-border/30 border border-border text-sm">
          <Info size={14} className="shrink-0 text-muted" />
          <span className="text-muted">{t('bd.awaiting')}</span>
          <span className="text-white/80">{holdReason}</span>
        </div>
      )}

      {/* Chart */}
      <div className="card !p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <span className="text-sm font-medium">{t('bd.chart', { symbol: bot.symbol })}</span>
          <span className="text-xs text-muted">{bot.timeframe} · {CHART_SUBTITLE[bot.strategy_id] ?? 'Indicadores'}</span>
        </div>
        <div className="p-2">
          <Chart
            candles={candles}
            indicators={indicators}
            strategyId={bot.strategy_id}
            trades={trades}
            height={380}
          />
        </div>
      </div>

      {/* Correlation graph */}
      {(graphState?.nodes?.length > 0 || graphState?.regime) && (
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold">{t('bd.graph')}</h2>
            {graphState?.regime && (
              <span className="text-xs text-muted">{t('bd.graph_updated')}</span>
            )}
          </div>
          <GraphView graphState={graphState} />

          {/* AI Interpretation */}
          <div className="mt-5 border-t border-border pt-4">
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs font-semibold text-muted uppercase tracking-widest">
                {t('bd.ai_interp')}
              </span>
              <button
                onClick={generateInterp}
                disabled={!interpReady || interpLoading}
                title={graphInterpConsumesTokens ? t('bd.tokens_graph_interp') : t('bd.no_tokens_graph_interp')}
                className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg font-medium transition-all
                  ${interpReady && !interpLoading
                    ? 'bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 cursor-pointer'
                    : 'bg-border/30 text-muted border border-border cursor-not-allowed opacity-50'
                  }`}
              >
                {interpLoading
                  ? <><span className="w-3 h-3 rounded-full border-2 border-accent border-t-transparent animate-spin" /> {t('bd.generating')}</>
                  : <><Sparkles size={12} /> {t('bd.gen_interp')}</>
                }
              </button>
            </div>

            {interp?.text ? (
              <div>
                <p className="text-sm leading-relaxed text-white/80">{interp.text}</p>
                <p className="text-xs text-muted/40 italic mt-2">
                  {interp.source === 'deepseek' ? t('bd.src_deepseek') : t('bd.src_rules')}
                  {' '}{t('bd.src_candle')}
                </p>
              </div>
            ) : !interpReady ? (
              <p className="text-sm text-muted/50 italic">{t('bd.waiting_candle')}</p>
            ) : (
              <p className="text-sm text-muted/50 italic">{t('bd.click_generate')}</p>
            )}
          </div>
        </div>
      )}

      {/* Strategy Optimization */}
      <div className="card">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <TrendingUp size={16} className="text-accent" />
            <h2 className="font-semibold">{t('bd.optimize')}</h2>
          </div>
          <div className="flex items-center gap-3">
            {!optimizeReady && signalReadiness && (
              <div className="flex items-center gap-2">
                <div className="h-1.5 w-24 bg-border rounded-full overflow-hidden">
                  <div
                    className="h-full bg-accent/50 rounded-full transition-all"
                    style={{ width: `${Math.min(100, (signalReadiness.signal_count / signalReadiness.signal_count_required) * 100)}%` }}
                  />
                </div>
                <span className="text-xs text-muted whitespace-nowrap">
                  {t('bd.signals_progress', {
                    n:   signalReadiness.signal_count,
                    req: signalReadiness.signal_count_required,
                  })}
                </span>
              </div>
            )}
            <button
              onClick={fetchAnalysis}
              disabled={!optimizeReady || analysisLoading}
              title={optimizeReady ? t('bd.no_tokens_optimize') : t('bd.await_signals', {
                n: (signalReadiness?.signal_count_required ?? 50) - (signalReadiness?.signal_count ?? 0),
              })}
              className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg font-medium transition-all
                ${optimizeReady && !analysisLoading
                  ? 'bg-accent/20 text-accent border border-accent/30 hover:bg-accent/30 cursor-pointer'
                  : 'bg-border/30 text-muted border border-border cursor-not-allowed opacity-50'
                }`}
            >
              {analysisLoading
                ? <><span className="w-3 h-3 rounded-full border-2 border-accent border-t-transparent animate-spin" /> {t('bd.analyzing')}</>
                : <><TrendingUp size={12} /> {t('bd.analyze_btn')}</>
              }
            </button>
            {analysis && (
              <button onClick={() => setOptimizeOpen(o => !o)} className="text-muted hover:text-white p-1">
                {optimizeOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
              </button>
            )}
          </div>
        </div>

        {!optimizeReady && !analysis && (
          <p className="text-xs text-muted/50 italic mt-3">
            {isRunning ? t('bd.accumulating') : t('bd.start_to_log')}
          </p>
        )}

        <div className="mt-3">
          <AiUsageNotice consumesTokens={false} text={t('bd.no_tokens_optimize')} />
        </div>

        {optimizeOpen && analysis && !analysis.error && (
          <div className="mt-5 space-y-5 border-t border-border pt-4">

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                { label: t('bd.total_signals'), value: analysis.signal_count },
                { label: t('c.buy'),  value: `${((analysis.signal_distribution?.buy  ?? 0) / analysis.signal_count * 100).toFixed(0)}%`, color: 'text-bull' },
                { label: t('c.sell'), value: `${((analysis.signal_distribution?.sell ?? 0) / analysis.signal_count * 100).toFixed(0)}%`, color: 'text-bear' },
                { label: t('c.hold'), value: `${((analysis.signal_distribution?.hold ?? 0) / analysis.signal_count * 100).toFixed(0)}%`, color: 'text-muted' },
              ].map(k => (
                <div key={k.label} className="bg-border/20 rounded-lg p-3 text-center">
                  <p className="text-xs text-muted mb-1">{k.label}</p>
                  <p className={`font-bold text-lg ${k.color ?? 'text-white'}`}>{k.value}</p>
                </div>
              ))}
            </div>

            {analysis.trade_analysis && (
              <div className="bg-border/20 rounded-lg p-3 flex items-center justify-between">
                <div>
                  <p className="text-xs text-muted mb-0.5">{t('bd.win_rate_linked')}</p>
                  <p className={`font-bold text-2xl ${analysis.trade_analysis.win_rate >= 50 ? 'text-bull' : 'text-bear'}`}>
                    {analysis.trade_analysis.win_rate}%
                  </p>
                  <p className="text-xs text-muted">
                    {analysis.trade_analysis.wins}W · {analysis.trade_analysis.losses}L {t('bd.of')} {analysis.trade_analysis.total_linked} trades
                  </p>
                </div>
                {Object.keys(analysis.trade_analysis.regime_stats ?? {}).length > 0 && (
                  <div className="text-right space-y-1">
                    <p className="text-xs text-muted mb-1">{t('bd.win_rate_regime')}</p>
                    {Object.entries(analysis.trade_analysis.regime_stats).map(([regime, stats]) => (
                      <div key={regime} className="flex items-center gap-2 justify-end">
                        <span className="text-xs text-muted capitalize">{regime}</span>
                        <span className={`text-xs font-mono font-semibold ${stats.win_rate >= 50 ? 'text-bull' : 'text-bear'}`}>
                          {stats.win_rate}%
                        </span>
                        <span className="text-xs text-muted/50">({stats.total})</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {analysis.trade_analysis?.indicator_comparison &&
              Object.keys(analysis.trade_analysis.indicator_comparison).length > 0 && (
              <div>
                <p className="text-xs text-muted uppercase tracking-widest mb-2">{t('bd.ind_comparison')}</p>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border text-muted">
                        <th className="text-left py-1.5 pr-4">{t('bd.col_indicator')}</th>
                        <th className="text-right py-1.5 pr-4">{t('bd.col_winners')}</th>
                        <th className="text-right py-1.5 pr-4">{t('bd.col_losers')}</th>
                        <th className="text-right py-1.5">Δ</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(analysis.trade_analysis.indicator_comparison).map(([ind, vals]) => {
                        const delta = vals.win_avg != null && vals.lose_avg != null
                          ? vals.win_avg - vals.lose_avg : null
                        return (
                          <tr key={ind} className="border-b border-border/40">
                            <td className="py-1.5 pr-4 font-mono text-white/70">{ind}</td>
                            <td className="py-1.5 pr-4 text-right text-bull font-mono">
                              {vals.win_avg != null ? vals.win_avg : '—'}
                            </td>
                            <td className="py-1.5 pr-4 text-right text-bear font-mono">
                              {vals.lose_avg != null ? vals.lose_avg : '—'}
                            </td>
                            <td className={`py-1.5 text-right font-mono font-semibold ${delta != null ? (delta > 0 ? 'text-bull' : delta < 0 ? 'text-bear' : 'text-muted') : 'text-muted'}`}>
                              {delta != null ? `${delta > 0 ? '+' : ''}${delta.toFixed(4)}` : '—'}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {analysis.top_hold_reasons?.length > 0 && (
              <div>
                <p className="text-xs text-muted uppercase tracking-widest mb-2">{t('bd.top_hold')}</p>
                <div className="space-y-1.5">
                  {analysis.top_hold_reasons.map((item, i) => {
                    const maxCount = analysis.top_hold_reasons[0].count
                    return (
                      <div key={i} className="flex items-center gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center justify-between mb-0.5">
                            <span className="text-xs text-white/70 truncate">{item.reason}</span>
                            <span className="text-xs text-muted ml-2 shrink-0">{item.count}×</span>
                          </div>
                          <div className="h-1 bg-border rounded-full overflow-hidden">
                            <div className="h-full bg-accent/40 rounded-full" style={{ width: `${(item.count / maxCount) * 100}%` }} />
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {analysis.suggestions?.length > 0 && (
              <div className="rounded-lg border border-accent/20 bg-accent/5 p-4 space-y-2">
                <p className="text-xs font-semibold text-accent uppercase tracking-widest mb-2">{t('bd.suggestions')}</p>
                {analysis.suggestions.map((tip, i) => (
                  <p key={i} className="text-sm text-white/80 leading-relaxed">· {tip}</p>
                ))}
              </div>
            )}
          </div>
        )}

        {optimizeOpen && analysis?.error && (
          <p className="text-sm text-bear mt-3">{t('c.error', { msg: analysis.error })}</p>
        )}
      </div>

      {/* Trades */}
      <div className="card">
        <h2 className="font-semibold mb-4">{t('bd.trade_history')}</h2>
        <TradeTable trades={trades} />
      </div>
    </div>
  )
}
