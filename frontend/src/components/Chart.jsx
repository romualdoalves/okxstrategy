import React, { useEffect, useRef } from 'react'
import { createChart } from 'lightweight-charts'

// Converte timestamp do DB para segundos Unix em UTC
function toUnixSec(ts) {
  if (!ts) return 0
  if (typeof ts === 'number') return ts > 1e10 ? Math.floor(ts / 1000) : ts
  let iso = String(ts).replace(' ', 'T')
  iso = iso.replace(/\.\d+/, '')  // strip microseconds (non-anchored — works before Z or at end)
  if (!iso.endsWith('Z') && !/[+-]\d{2}:?\d{2}$/.test(iso)) iso += 'Z'
  const ms = new Date(iso).getTime()
  return isNaN(ms) ? 0 : Math.floor(ms / 1000)
}

export default function Chart({ candles = [], indicators = {}, strategyId = '', trades = [], height = 400 }) {
  const containerRef = useRef(null)
  const chartRef     = useRef(null)
  const candleRef    = useRef(null)
  const ema21Ref     = useRef(null)
  const ema55Ref     = useRef(null)
  const vwapRef      = useRef(null)
  const ema50Ref     = useRef(null)
  const priceLinesRef = useRef([])

  // Debug: log quando dados mudam
  useEffect(() => {
    console.log('[Chart] indicators:', indicators)
    console.log('[Chart] strategyId:', strategyId)
    console.log('[Chart] trades count:', trades?.length)
    if (trades?.length > 0) {
      console.log('[Chart] last trade:', trades[trades.length - 1])
    }
  }, [indicators, strategyId, trades])

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout:     { background: { color: '#131722' }, textColor: '#787b86' },
      grid:       { vertLines: { color: '#1e2130' }, horzLines: { color: '#1e2130' } },
      crosshair:  { mode: 1 },
      rightPriceScale: { borderColor: '#1e2130' },
      timeScale:  { borderColor: '#1e2130', timeVisible: true },
      height,
    })

    candleRef.current = chart.addCandlestickSeries({
      upColor: '#26a69a', downColor: '#ef5350',
      borderUpColor: '#26a69a', borderDownColor: '#ef5350',
      wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    })
    chartRef.current = chart

    const ro = new ResizeObserver(() => {
      chart.applyOptions({ width: containerRef.current.clientWidth })
    })
    ro.observe(containerRef.current)

    return () => { ro.disconnect(); chart.remove() }
  }, [height])

  // Atualiza candles
  useEffect(() => {
    if (!candleRef.current || !candles.length) return
    // OKX retorna candles em ordem decrescente; lightweight-charts exige crescente e sem duplicatas
    const seen = new Set()
    const sorted = [...candles]
      .sort((a, b) => Number(a[0]) - Number(b[0]))
      .filter(c => { const t = Number(c[0]); if (seen.has(t)) return false; seen.add(t); return true })
    const data = sorted.map(c => ({
      time: Math.floor(Number(c[0]) / 1000),
      open: parseFloat(c[1]), high: parseFloat(c[2]),
      low:  parseFloat(c[3]), close: parseFloat(c[4]),
    }))
    try {
      candleRef.current.setData(data)
      chartRef.current?.timeScale().fitContent()
    } catch (e) {
      console.error('Chart setData error:', e)
    }
  }, [candles])

  // Atualiza indicadores tecnicos apenas quando a estrategia usa essas linhas.
  useEffect(() => {
    if (!chartRef.current || !candles.length) return

    const seen2 = new Set()
    const sorted = [...candles]
      .sort((a, b) => Number(a[0]) - Number(b[0]))
      .filter(c => { const t = Number(c[0]); if (seen2.has(t)) return false; seen2.add(t); return true })
    const closes = sorted.map(c => parseFloat(c[4]))
    const times  = sorted.map(c => Math.floor(Number(c[0]) / 1000))

    function ema(data, period) {
      const k = 2 / (period + 1)
      let val = data[0]
      return data.map((v, i) => {
        if (i === 0) return val
        val = v * k + val * (1 - k)
        return val
      })
    }

    const showEmaVwap = strategyId === 'TF001'
    const showEma21 = strategyId === 'PA002' || strategyId === 'PA001' || strategyId === 'RG001'

    setSeriesVisible(chartRef.current, ema21Ref, showEmaVwap || showEma21, {
      color: '#00bcd4', lineWidth: 1, title: showEma21 ? 'EMA21' : 'C1 EMA21',
    })
    setSeriesVisible(chartRef.current, ema55Ref, showEmaVwap, {
      color: '#ff9800', lineWidth: 2, title: 'C1 EMA55',
    })
    setSeriesVisible(chartRef.current, vwapRef, showEmaVwap, {
      color: '#9c27b0', lineWidth: 1, title: 'C2 VWAP', lineStyle: 2,
    })
    setSeriesVisible(chartRef.current, ema50Ref, strategyId === 'PA001', {
      color: '#ff9800', lineWidth: 2, title: 'C1 EMA50',
    })

    try {
      if (ema21Ref.current) {
        const e21 = ema(closes, 21)
        ema21Ref.current.setData(times.map((t, i) => ({ time: t, value: parseFloat(e21[i].toFixed(2)) })))
      }
      if (ema55Ref.current) {
        const e55 = ema(closes, 55)
        ema55Ref.current.setData(times.map((t, i) => ({ time: t, value: parseFloat(e55[i].toFixed(2)) })))
      }
    } catch (e) { console.error('EMA setData:', e) }

    // VWAP diário simplificado
    const hlc3 = sorted.map(c => (parseFloat(c[2]) + parseFloat(c[3]) + parseFloat(c[4])) / 3)
    const vols  = sorted.map(c => parseFloat(c[5]))
    let sumPV = 0, sumV = 0
    const vwapData = sorted.map((c, i) => {
      sumPV += hlc3[i] * vols[i]
      sumV  += vols[i]
      return { time: times[i], value: parseFloat((sumPV / (sumV || 1)).toFixed(2)) }
    })
    try {
      if (vwapRef.current) vwapRef.current.setData(vwapData)
    } catch (e) { console.error('VWAP setData:', e) }
  }, [candles, strategyId])

  // Marcadores de trades no gráfico
  useEffect(() => {
    if (!candleRef.current || !candles.length) return

    // Candle times (crescente) para snap — lightweight-charts exige time == bar.time exato
    const sortedTimes = [...candles]
      .sort((a, b) => Number(a[0]) - Number(b[0]))
      .map(c => Math.floor(Number(c[0]) / 1000))

    // Retorna o último candle que abriu em ou antes do timestamp do trade
    function snapToBar(tradeTs) {
      let snapped = sortedTimes[0]
      for (const t of sortedTimes) {
        if (t <= tradeTs) snapped = t
        else break
      }
      return snapped
    }

    const markers = trades
      .filter(t => t.timestamp)
      .map(t => {
        const time = snapToBar(toUnixSec(t.timestamp))
        const isLong = t.direction === 'LONG'

        if (t.type === 'entry') {
          return {
            time,
            position: isLong ? 'belowBar' : 'aboveBar',
            color:    isLong ? '#26a69a' : '#ef5350',
            shape:    isLong ? 'arrowUp' : 'arrowDown',
            text:     isLong
              ? `L ${t.entry_price ? '$' + Number(t.entry_price).toLocaleString() : ''}`
              : `S ${t.entry_price ? '$' + Number(t.entry_price).toLocaleString() : ''}`,
          }
        }
        if (t.type === 'tp1') {
          return {
            time,
            position: isLong ? 'aboveBar' : 'belowBar',
            color:    '#00bcd4',
            shape:    'circle',
            text:     `TP1 ${t.pnl != null ? (t.pnl >= 0 ? '+' : '') + '$' + Number(t.pnl).toFixed(2) : ''}`,
          }
        }
        // exit / SL / trailing
        return {
          time,
          position: isLong ? 'aboveBar' : 'belowBar',
          color:    '#ff9800',
          shape:    isLong ? 'arrowDown' : 'arrowUp',
          text:     `Exit ${t.event ?? ''} ${t.pnl != null ? (t.pnl >= 0 ? '+' : '') + '$' + Number(t.pnl).toFixed(2) : ''}`,
        }
      })
      .filter(Boolean)
      .sort((a, b) => a.time - b.time)  // lightweight-charts exige ordem ASC

    try {
      candleRef.current.setMarkers(markers)
    } catch (e) {
      console.error('setMarkers error:', e)
    }
  }, [trades, candles])

  // Strategy-specific criterion overlays + open position lines.
  useEffect(() => {
    if (!candleRef.current) return

    priceLinesRef.current.forEach(line => {
      try { candleRef.current.removePriceLine(line) } catch {}
    })
    priceLinesRef.current = []

    const overlays = getStrategyOverlays(strategyId, indicators)
    
    // Adiciona a linha de Entrada, SL e TP se houver posição aberta (entry sem exit_price)
    if (trades && trades.length > 0) {
      const openEntry = [...trades].reverse().find(t => t.type === 'entry' && !t.exit_price)
      if (openEntry && openEntry.entry_price) {
        const isLong = openEntry.direction === 'LONG'
        overlays.push({
          price: Number(openEntry.entry_price),
          title: `ENTRADA ${isLong ? 'LONG' : 'SHORT'}`,
          color: isLong ? '#10b981' : '#f43f5e',
          width: 2,
          style: 0, // Linha sólida
        })
        if (openEntry.sl_price) {
          overlays.push({
            price: Number(openEntry.sl_price),
            title: 'STOP LOSS',
            color: '#ef5350',
            width: 1,
            style: 2, // Linha tracejada
          })
        }
        if (openEntry.tp1_price) {
          overlays.push({
            price: Number(openEntry.tp1_price),
            title: 'TAKE PROFIT 1',
            color: '#26a69a',
            width: 1,
            style: 2, // Linha tracejada
          })
        }
      }
    }

    priceLinesRef.current = overlays.map(o =>
      candleRef.current.createPriceLine({
        price: o.price,
        color: o.color,
        lineWidth: o.width ?? 1,
        lineStyle: o.style ?? 2,
        axisLabelVisible: true,
        title: o.title,
      })
    )
  }, [strategyId, indicators, trades])

  return (
    <div ref={containerRef} className="w-full rounded-lg overflow-hidden" style={{ height }} />
  )
}

function getStrategyOverlays(strategyId, indicators = {}) {
  if (!indicators) return []
  const line = (key, title, color, style = 2, width = 1) => {
    const price = Number(indicators[key])
    return Number.isFinite(price) && price > 0
      ? [{ price, title, color, style, width }]
      : []
  }

  if (strategyId === 'TF001') { // EMA + VWAP
    return [
      ...line('ema_fast', 'C2 EMA21', '#00bcd4', 2, 1),
      ...line('ema_slow', 'C1 EMA55', '#ff9800', 2, 2),
      ...line('vwap',     'C2 VWAP',  '#9c27b0', 2, 1),
    ]
  }

  if (strategyId === 'TF013') { // Bollinger
    return [
      ...line('bb_upper', 'C1 BB superior', '#26a69a'),
      ...line('bb_mid',   'C2 BB media',    '#9ca3af', 3),
      ...line('bb_lower', 'C1 BB inferior', '#ef5350'),
    ]
  }

  if (strategyId === 'TF002') { // RSI + MA
    return line('ma', 'C2 MA200', '#f59e0b', 2, 2)
  }

  if (strategyId === 'TF003') { // MACD
    return line('trend_ma', 'C3 Trend MA', '#f59e0b', 2, 2)
  }

  if (strategyId === 'PA003') { // Value Zones (OTR)
    return [
      ...line('range_high', 'C1 Topo OTR', '#ef5350', 2, 2),
      ...line('range_low',  'C1 Fundo OTR', '#26a69a', 2, 2),
    ]
  }

  if (strategyId === 'TF011') { // CFM
    return [
      ...line('ema_slow', 'C1 EMA Slow', '#ff9800', 2, 2),
      ...line('ema_fast', 'C2 EMA Fast', '#00bcd4', 2, 2),
    ]
  }

  if (strategyId === 'PA002') { // ABCD
    return [
      ...line('a_price', 'C3 Topo A (Alvo)', '#26a69a', 2, 2),
      ...line('b_price', 'C1 Fundo B', '#f59e0b'),
      ...line('c_price', 'C1 Topo C', '#9ca3af'),
      ...line('d_price', 'C1 Fundo D (Stop)', '#ef5350', 2, 2),
    ]
  }

  if (strategyId === 'TF007') { // Multi-TF
    return [
      ...line('ema_slow_1h', 'C1 EMA55 1H', '#ff9800', 2, 2),
      ...line('ema_fast_1h', 'C2 EMA21 1H', '#00bcd4', 2, 1),
    ]
  }

  if (strategyId === 'TF010') { // Stochastic Reversal
    return [
      ...line('ema144', 'C1 EMA144', '#ff9800', 2, 2),
      ...line('ema34',  'C2 EMA34',  '#9ca3af', 2, 1),
      ...line('ema21',  'C3 EMA21',  '#00bcd4', 2, 1),
    ]
  }

  if (strategyId === 'TF012') { // Liquidity Clusters
    return line('ema200', 'C1 EMA200', '#f59e0b', 2, 2)
  }

  if (strategyId === 'MR003') { // Warrior Reversal
    return [
      ...line('bb_upper', 'C1 BB Superior', '#ef5350', 2, 1),
      ...line('bb_mid',   'C2 BB Média',    '#9ca3af', 3, 1),
      ...line('bb_lower', 'C1 BB Inferior', '#26a69a', 2, 1),
      ...line('ema9',     'C3 EMA9',        '#f59e0b', 2, 1),
    ]
  }

  if (strategyId === 'MR001') { // Mean Reversion
    return line('ema', 'C1 EMA Base', '#f59e0b', 2, 2)
  }

  if (strategyId === 'TF008') { // Cycle Shift
    return line('ema_21_w', 'C1 EMA 21 Semanal', '#f59e0b', 1, 2)
  }

  if (strategyId === 'PA001') { // Pivot Sniper
    return line('sr_level', 'C1 Pivot Sniper', '#ff9800', 1, 2)
  }

  if (strategyId === 'SC001') { // Pattern Scalp
    return [
      ...line('open_high', 'C1 Opening High', '#ef5350', 2, 1),
      ...line('open_low',  'C1 Opening Low',  '#26a69a', 2, 1),
    ]
  }

  if (strategyId === 'RG002') { // Graph Regime
    return [
      ...line('ema_fast', 'C1 EMA Fast', '#ef5350', 2, 1),
      ...line('ema_slow', 'C2 EMA Slow', '#26a69a', 2, 1),
    ]
  }

  if (strategyId === 'SC003') { // Whale Flow
    return [
      ...line('vah', 'C1 VAH Inst.', '#ef5350', 2, 2),
      ...line('val', 'C1 VAL Inst.', '#26a69a', 2, 2),
      ...line('vwap', 'POC Equilíbrio', '#9c27b0', 3, 1),
    ]
  }

  if (strategyId === 'TF004') { // SuperTrend
    return line('supertrend', 'SuperTrend Stop', '#00bcd4', 2, 2)
  }

  if (strategyId === 'TF005') { // Joe Di Napoli
    return [
      line('ema3', 'EMA 3', '#ffeb3b', 2, 0),
      line('ema_trend', 'EMA Trend', '#ff9800', 2, 0)
    ]
  }
  
  if (strategyId === 'PA005') { // Liquidity Sweep
    return [
      ...line('support_level', 'C1 Suporte', '#26a69a', 1, 2),
      ...line('channel_high',  'C3 Gatilho CHoCH', '#ff9800', 2, 1),
    ]
  }

  if (strategyId === 'PA004') { // Three Line Bar
    return [
      ...line('ema_trend', 'C2 EMA200',  '#f59e0b', 2, 2),
      ...line('c3_high',   'C1 High C3', '#26a69a', 2, 1),
      ...line('c1_low',    'C1 Low C1',  '#ef5350', 2, 1),
    ]
  }

  if (strategyId === 'MR002') { // Double Bollinger
    return [
      ...line('outer_u', 'BB 4σ Superior', '#ef5350', 2, 1),
      ...line('inner_u', 'BB 2σ Superior', '#ef5350', 3, 1),
      ...line('inner_l', 'BB 2σ Inferior', '#26a69a', 3, 1),
      ...line('outer_l', 'BB 4σ Inferior', '#26a69a', 2, 1),
    ]
  }

  if (strategyId === 'MR005') { // Bollinger Triple Threat
    return [
      ...line('bb_u', 'C1 BB Superior', '#26a69a'),
      ...line('bb_m', 'C2 BB Media',    '#9ca3af', 3),
      ...line('bb_l', 'C1 BB Inferior', '#ef5350'),
    ]
  }

  return []
}

function setSeriesVisible(chart, ref, visible, options) {
  if (visible && !ref.current) {
    ref.current = chart.addLineSeries(options)
    return
  }
  if (!visible && ref.current) {
    chart.removeSeries(ref.current)
    ref.current = null
  }
}
