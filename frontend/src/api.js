const BASE = '/api'

async function req(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    let err = {}
    try {
      err = text ? JSON.parse(text) : {}
    } catch {
      err = { detail: text }
    }
    const detail = err.detail || err.message || text || res.statusText || `HTTP ${res.status}`
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return res.status === 204 ? null : res.json()
}

// Estratégias
export const getHealth           = ()       => req('/health')
export const getStrategies       = ()       => req('/strategies')
export const deleteStrategy  = (id)     => req(`/strategy-factory/strategies/${id}/permanent`, { method: 'DELETE' })
export const getMarketRegime = ()       => req('/market/regime')
export const getMarketClock = ()       => req('/market/clock')

// Bots
export const getBots         = ()       => req('/bots')
export const getPerformanceRanking = () => req('/bots/performance')
export const getActivePerformanceRanking = () => req('/bots/performance/active')
export const syncTrailingStops = () => req('/bots/sync-trailing', { method: 'POST' })
export const getBot          = (id)     => req(`/bots/${id}`)
export const createBot       = (data)   => req('/bots', { method: 'POST', body: JSON.stringify(data) })
export const updateBot       = (id, d)  => req(`/bots/${id}`, { method: 'PATCH', body: JSON.stringify(d) })
export const deleteBot       = (id)     => req(`/bots/${id}`, { method: 'DELETE' })
export const startBot        = (id)     => req(`/bots/${id}/start`,  { method: 'POST' })
export const stopBot         = (id)     => req(`/bots/${id}/stop`,   { method: 'POST' })
export const liquidateBot    = (id)     => req(`/bots/${id}/liquidate`, { method: 'POST' })
export const runBacktest      = (id)     => req(`/bots/${id}/backtest`,  { method: 'POST' })
export const optimizeBotParams = (id)     => req(`/bots/${id}/optimize`,  { method: 'POST' })
export const getBotStatus    = (id)     => req(`/bots/${id}/status`)
export const switchBotSymbol     = (id, symbol = 'BTC-USDT') =>
  req(`/bots/${id}/switch-symbol`, { method: 'POST', body: JSON.stringify({ symbol }) })
export const recaptureBaseline   = (id) => req(`/bots/${id}/recapture-baseline`, { method: 'POST' })

// Trades
export const getTrades        = (botId)  => req(`/trades${botId ? `?bot_id=${botId}` : ''}`)
export const getTradesSummary = (botId)  => req(`/trades/summary${botId ? `?bot_id=${botId}` : ''}`)
export const syncTradeFees    = (days = 30) => req(`/trades/sync-fees?days_back=${days}`, { method: 'POST' })

// Sistema
export const getAiUsageStatus    = () => req('/system/ai-usage')
export const getOpsDiagnostics   = (days = 7) => req(`/ops/diagnostics?days=${days}`)
export const getTelegramStatus   = () => req('/system/telegram-status')
export const testTelegram        = () => req('/system/telegram-test', { method: 'POST' })

// Conta
export const getBalance        = (ccy = 'USDT', demo = false) => req(`/account/balance?currency=${ccy}&demo=${demo}`)
export const getAccountSnapshot = (demo = false) => req(`/account/snapshot?demo=${demo}`)
export const getIntegrityCheck  = () => req('/integrity/check')

// Auto-Scan horário
export const getAutoScanStatus = () => req('/auto-scan/status')
export const toggleAutoScan    = (enabled) => req('/auto-scan/toggle', {
  method: 'POST', body: JSON.stringify({ enabled }),
})
export const getAutoScanRuns   = (filters = {}) => {
  const params = new URLSearchParams()
  const { limit = 20, symbol, strategyId, category, dateFrom, dateTo } = filters
  params.set('limit', limit)
  if (symbol)    params.set('symbol', symbol)
  if (strategyId) params.set('strategy_id', strategyId)
  if (category)  params.set('category', category)
  if (dateFrom)  params.set('date_from', dateFrom)
  if (dateTo)    params.set('date_to', dateTo)
  return req(`/auto-scan/runs?${params.toString()}`)
}
export const resetAccount      = () => req('/account/reset-data', { method: 'POST' })

// OKX — Conexão via banco
export const getOkxStatus      = () => req('/account/connection')
export const connectOkxAccount = (apiKey, apiSecret, passphrase, confirmClear = false) => req('/account/connect', {
  method: 'POST',
  body: JSON.stringify({ api_key: apiKey, api_secret: apiSecret, passphrase, confirm_clear: confirmClear }),
})
export const disconnectOkxAccount = () => req('/account/disconnect', { method: 'POST' })
export const setOkxCredentials = connectOkxAccount
export const getOrderRejections = (params = {}) => {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, v)
  })
  const suffix = qs.toString() ? `?${qs}` : ''
  return req(`/order-rejections${suffix}`)
}
export const updateOrderRejection = (id, data) =>
  req(`/order-rejections/${id}`, { method: 'PATCH', body: JSON.stringify(data) })

// Grafo
export const getGraphState          = (botId) => req(`/bots/${botId}/graph`)
export const getGraphInterpretation = (botId) => req(`/bots/${botId}/graph/interpret`)

// Signal logs / optimização
export const getBotAiAnalysis       = (botId) => req(`/bots/${botId}/ai-analysis`)
export const getSignalLogsReadiness = (botId) => req(`/bots/${botId}/signal-logs/readiness`)
export const getSignalLogsAnalysis  = (botId) => req(`/bots/${botId}/signal-logs/analysis`)

// Activities (dados oficiais OKX)
export const getActivities = (date) => req(`/activities${date ? `?date=${date}` : ''}`)

// Fábrica de Estratégias
export const factoryStatus   = () => req('/strategy-factory/status')
export const factoryPlan     = (description) =>
  req('/strategy-factory/plan', { method: 'POST', body: JSON.stringify({ description }) })
export const factoryGenerate = (plan) =>
  req('/strategy-factory/generate', { method: 'POST', body: JSON.stringify({ plan }) })
export const factoryValidate = (code, plan) =>
  req('/strategy-factory/validate', { method: 'POST', body: JSON.stringify({ code, plan }) })
export const factoryFix     = (code, plan, errors) =>
  req('/strategy-factory/fix',      { method: 'POST', body: JSON.stringify({ code, plan, errors }) })
export const factoryDeploy   = (code, plan, source_text = '') =>
  req('/strategy-factory/deploy', { method: 'POST', body: JSON.stringify({ code, plan, source_text }) })
export const factoryList     = () => req('/strategy-factory/strategies')
export const factoryGetCode  = (id) => req(`/strategy-factory/strategies/${id}/code`)
export const factoryDisable  = (id) => req(`/strategy-factory/strategies/${id}`, { method: 'DELETE' })

// Mercado
export const rankAssets  = (strategyId, timeframe) =>
  req(`/market/rank-assets?strategy_id=${strategyId}&timeframe=${timeframe}`)
export const getSymbolTradability = (symbols = [], demo = true) => {
  const csv = symbols.join(',')
  return req(`/market/symbol-tradability?symbols=${encodeURIComponent(csv)}&demo=${demo}`)
}
export const getCandles      = (symbol, tf, limit = 200) =>
  req(`/market/candles?symbol=${symbol}&timeframe=${tf}&limit=${limit}`)
export const getTicker       = (symbol) => req(`/market/ticker?symbol=${symbol}`)

// Market Data cache (OKX)
export const getTrackedMarketData = () => req('/market-data/tracked')
export const getAvailableMarketDataSymbols = () => req('/market-data/available-symbols')
export const bootstrapMarketData = (symbols = [], timeframes = ['1m', '5m', '15m', '1h', '1d']) =>
  req('/market-data/bootstrap-defaults', {
    method: 'POST',
    body: JSON.stringify({ symbols, timeframes }),
  })
export const trackMarketDataSymbol = (symbol, timeframes = ['1m', '5m', '15m', '1h', '1d']) =>
  req('/market-data/track', {
    method: 'POST',
    body: JSON.stringify({ symbol, timeframes }),
  })
export const forceSyncMarketData = (symbol, timeframe = null) =>
  req(`/market-data/force-sync/${encodeURIComponent(symbol)}${timeframe ? `/${encodeURIComponent(timeframe)}` : ''}`, {
    method: 'POST',
  })
export const forceSyncAllMarketData = (timeframe = null) =>
  req('/market-data/force-sync-all', {
    method: 'POST',
    body: JSON.stringify({ timeframe }),
  })
export const getMarketDataSyncJobs = (limit = 60, batchId = null) => {
  const qs = new URLSearchParams()
  qs.set('limit', String(limit))
  if (batchId) qs.set('batch_id', batchId)
  return req(`/market-data/sync-jobs?${qs.toString()}`)
}
export const removeTrackedMarketData = (symbol, timeframe = null) =>
  req(`/market-data/track/${encodeURIComponent(symbol)}${timeframe ? `/${encodeURIComponent(timeframe)}` : ''}`, {
    method: 'DELETE',
  })
