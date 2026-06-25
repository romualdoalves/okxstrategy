import { useEffect, useRef } from 'react'
import { useLanguage } from '../i18n/LanguageContext'

const STATUS = {
  green:  { dot: 'bg-bull',       border: 'border-bull/25',       bg: 'bg-bull/8',       val: 'text-bull',       sub: 'text-bull/70'       },
  yellow: { dot: 'bg-yellow-400', border: 'border-yellow-400/25', bg: 'bg-yellow-400/8', val: 'text-yellow-400', sub: 'text-yellow-400/70'  },
  red:    { dot: 'bg-bear',       border: 'border-bear/25',       bg: 'bg-bear/8',       val: 'text-bear',       sub: 'text-bear/70'       },
  grey:   { dot: 'bg-muted/40',   border: 'border-border',        bg: 'bg-border/20',    val: 'text-white/70',   sub: 'text-muted'         },
  none:   { dot: 'bg-border',     border: 'border-border/40',     bg: 'bg-border/10',    val: 'text-muted/60',   sub: 'text-muted/40'      },
}

const fmt  = (v, dec = 2) => (v != null && isFinite(v)) ? Number(v).toFixed(dec) : '—'
const fmtS = (v, dec = 1) => {
  if (v == null || !isFinite(v)) return '—'
  return (v >= 0 ? '+' : '') + Number(v).toFixed(dec)
}

function hasOfficialCriteria(ind) {
  return Number(ind?._criteria_total || 0) > 0
}

function officialEntryCriteria(ind) {
  if (!hasOfficialCriteria(ind)) return null
  const met = Math.max(0, Number(ind._criteria_met || 0))
  const total = Math.max(0, Number(ind._criteria_total || 0))
  const criteriaNames = ind._criteria_names || []
  const items = []

  for (let i = 0; i < total; i += 1) {
    const name = criteriaNames[i] || `C${i + 1}`
    items.push({
      id: `official_${i + 1}`,
      label: name,
      sub: 'Estratégia',
      value: i < met ? 'OK' : 'PENDENTE',
      status: i < met ? 'green' : 'yellow',
      detail: i < met ? 'Critério oficial atendido pelo backend' : 'Critério oficial ainda pendente no backend',
    })
  }

  return items
}

function emasOk(ind) {
  return ind && ind.ema_fast != null && ind.ema_slow != null && isFinite(ind.ema_fast) && isFinite(ind.ema_slow)
}

function emaCrossStatus(ind, prevInd, crossedRef) {
  if (!emasOk(ind)) return 'none'
  if (crossedRef?.current) return 'green'
  const gap = ind.ema_fast - ind.ema_slow
  const pct = Math.abs(gap) / (ind.close || 1)
  if (pct < 0.0008) return 'yellow'
  return 'green'
}

function emaCrossDetail(ind, crossedRef, t) {
  if (!emasOk(ind)) return t('sc.no_data')
  if (crossedRef?.current) return t(crossedRef.current)
  const gap = ind.ema_fast - ind.ema_slow
  const pct = Math.abs(gap) / (ind.close || 1)
  if (pct < 0.0008) return t('sc.ema_imminent')
  return gap > 0 ? t('sc.ema_fast_above') : t('sc.ema_fast_below')
}

function emaValue(ind) {
  if (!emasOk(ind)) return '—'
  const gap = ind.ema_fast - ind.ema_slow
  return `${gap >= 0 ? '+' : ''}${fmt(gap, 0)} pts`
}

function makeCriteriaMap(t, bot) {
  const isDeriv = bot?.symbol?.includes('-SWAP') || bot?.symbol?.includes('-FUTURES')
  const isSpot  = bot && !isDeriv

  const spotCriterion = (ind) => {
    if (!isSpot) return null
    const signal = ind?.signal || 'hold'
    const isShort = signal === 'sell'
    return {
      id: 'spot_dir', label: t('sc.spot_label'), sub: t('sc.spot_sub'),
      value: isShort ? 'Short' : 'Long',
      status: isShort ? 'red' : 'green',
      detail: isShort ? t('sc.spot_fail') : t('sc.spot_ok')
    }
  }

  const wrap = (baseList) => (ind, prev, ref) => {
    const list = baseList(ind, prev, ref)
    const sc   = spotCriterion(ind)
    return sc ? [...list, sc] : list
  }

  return {
    // ── B: Básicas ────────────────────────────────────────────────────────────
    TF001: wrap((ind, _prev, crossedRef) => [
      { id: 'ema', label: `C1 ${t('sc.ema_label')}`, sub: t('sc.ema_sub'), value: emaValue(ind), status: emaCrossStatus(ind, _prev, crossedRef), detail: emaCrossDetail(ind, crossedRef, t) },
      { id: 'vwap', label: `C2 ${t('sc.vwap_label')}`, sub: t('sc.vwap_sub'), value: (() => { if (!ind?.vwap || !ind?.close) return '—'; const diff = ind.close - ind.vwap; return `${diff >= 0 ? '+' : ''}${fmt(diff, 0)} pts` })(), status: (() => { if (!ind?.vwap || !ind?.close) return 'none'; return (ind.close > ind.vwap && ind.ema_fast > ind.ema_slow) || (ind.close < ind.vwap && ind.ema_fast < ind.ema_slow) ? 'green' : 'red' })(), detail: ind?.close > ind?.vwap ? t('sc.vwap_above') : t('sc.vwap_below') },
      { id: 'atr', label: `C3 ${t('sc.atr_label')}`, sub: t('sc.atr_sub'), value: fmt(ind?.atr, 2), status: ind?.atr ? 'green' : 'none', detail: t('sc.atr_detail') }
    ]),

    TF002: wrap((ind) => [
      { id: 'rsi', label: `C1 ${t('sc.rsi_label')}`, sub: t('sc.rsi_sub'), value: fmt(ind?.rsi, 1), status: (() => { const r = ind?.rsi; if (!r) return 'none'; if (r <= 32 || r >= 68) return 'green'; if (r <= 40 || r >= 60) return 'yellow'; return 'red' })(), detail: ind?.rsi > 70 ? t('sc.rsi_overbought_imm') : ind?.rsi < 30 ? t('sc.rsi_oversold_imm') : t('sc.rsi_neutral') },
      { id: 'ma', label: `C2 ${t('sc.ma_label')}`, sub: t('sc.ma_sub'), value: (() => { if (!ind?.ma || !ind?.close) return '—'; return `${(ind.close - ind.ma) >= 0 ? '+' : ''}${fmt(ind.close - ind.ma, 0)} pts` })(), status: (() => { const m = ind?.ma; const c = ind?.close; if (!m || !c) return 'none'; return (ind.rsi <= 32 && c > m) || (ind.rsi >= 68 && c < m) ? 'green' : 'red' })(), detail: ind?.close > ind?.ma ? t('sc.ma_above') : t('sc.ma_below') }
    ]),

    TF013: wrap((ind) => [
      { id: 'bb_pos', label: `C1 ${t('sc.bb_pos_label')}`, sub: t('sc.bb_pos_sub'), value: (() => { const { bb_upper: u, bb_mid: m, close: c } = ind ?? {}; if (!u || !m || !c) return '—'; return `${((c - m) / ((u - m) || 1) * 100).toFixed(0)}%` })(), status: (() => { const { bb_upper: u, bb_lower: l, close: c } = ind ?? {}; if (!u || !l || !c) return 'none'; if (c > u || c < l) return 'green'; return 'grey' })(), detail: ind?.close > ind?.bb_upper ? t('sc.bb_above_upper') : ind?.close < ind?.bb_lower ? t('sc.bb_below_lower') : t('sc.bb_inside') },
      { id: 'bb_width', label: `C2 ${t('sc.bb_width_label')}`, sub: t('sc.bb_width_sub'), value: (() => { const { bb_upper: u, bb_lower: l, bb_mid: m } = ind ?? {}; if (!u || !l || !m) return '—'; return `${fmt((u - l) / m * 100, 1)}%` })(), status: (() => { const { bb_upper: u, bb_lower: l, bb_mid: m } = ind ?? {}; if (!u || !l || !m) return 'none'; const w = (u - l) / m * 100; return w >= 4 ? 'green' : w >= 2 ? 'yellow' : 'red' })(), detail: t('sc.bb_width_sub') },
      { id: 'volume', label: `C3 ${t('sc.vol_label')}`, sub: t('sc.vol_sub'), value: (() => { const v = ind?.volume; const a = ind?.vol_avg; if (!v || !a) return '—'; return `${fmt(v / a, 2)}x` })(), status: (() => { const r = ind?.volume / ind?.vol_avg; if (!r) return 'none'; return r >= 1.5 ? 'green' : r >= 1.0 ? 'yellow' : 'red' })(), detail: t('sc.vol_sub') }
    ]),

    TF003: wrap((ind) => [
      { id: 'macd_cross', label: `C1 ${t('sc.macd_label')}`, sub: t('sc.macd_sub'), value: fmtS(ind?.macd - ind?.signal, 4), status: ind?.macd != null && ind?.signal != null && Math.abs(ind.macd - ind.signal) < 0.0001 ? 'yellow' : 'green', detail: ind?.macd > ind?.signal ? t('sc.macd_above') : t('sc.macd_below') },
      { id: 'hist', label: `C2 ${t('sc.hist_label')}`, sub: t('sc.hist_sub'), value: fmtS(ind?.hist, 4), status: ind?.hist != null && Math.abs(ind.hist) < 0.00005 ? 'yellow' : 'green', detail: ind?.hist > 0 ? t('sc.hist_pos') : t('sc.hist_neg') },
      { id: 'trend_ma', label: `C3 ${t('sc.trend_ma_label')}`, sub: t('sc.trend_ma_sub'), value: fmtS(ind?.close - ind?.trend_ma, 0), status: ind?.close > ind?.trend_ma ? 'green' : 'red', detail: t('sc.trend_ma_sub') }
    ]),

    TF004: wrap((ind) => [
      { id: 'direction', label: `C1 ${t('sc.st_label')}`, sub: t('sc.st_sub'), value: ind?.direction === 1 ? t('sc.st_bull') : ind?.direction === -1 ? t('sc.st_bear') : '—', status: ind?.direction === 1 ? 'green' : ind?.direction === -1 ? 'red' : 'grey', detail: t('sc.st_sub') },
      { id: 'rsi', label: `C2 ${t('sc.rsi_label')}`, sub: t('sc.rsi_confirm_sub'), value: fmt(ind?.rsi, 1), status: (ind?.direction === 1 && ind?.rsi >= 50) || (ind?.direction === -1 && ind?.rsi <= 50) ? 'green' : 'red', detail: t('sc.rsi_confirm_sub') },
      { id: 'st_dist', label: `C3 ${t('sc.st_dist_label')}`, sub: t('sc.st_dist_sub'), value: `${fmt(Math.abs(ind?.close - ind?.supertrend) / ind?.close * 100, 2)}%`, status: 'grey', detail: t('sc.st_dist_sub') }
    ]),

    TF005: wrap((ind) => [
      { id: 'trend', label: `C1 ${t('sc.dinapoli_trend_label')}`, sub: t('sc.dinapoli_trend_sub'), value: ind?.trend_ok === 1 ? 'Bullish' : ind?.trend_ok === -1 ? 'Bearish' : '-', status: ind?.trend_ok !== 0 ? 'green' : 'grey', detail: t('sc.dinapoli_trend_sub') },
      { id: 'setup', label: `C2 ${t('sc.dinapoli_setup_label')}`, sub: t('sc.dinapoli_setup_sub'), value: ind?.setup_formed === 1 ? 'Fundo Asc.' : ind?.setup_formed === -1 ? 'Topo Desc.' : '-', status: ind?.setup_formed !== 0 ? 'green' : 'grey', detail: t('sc.dinapoli_setup_sub') },
      { id: 'trigger', label: `C3 ${t('sc.dinapoli_trigger_label')}`, sub: t('sc.dinapoli_trigger_sub'), value: ind?.trigger_crossed !== 0 ? 'Rompido' : '-', status: ind?.trigger_crossed !== 0 ? 'green' : 'grey', detail: t('sc.dinapoli_trigger_sub') }
    ]),

    // ── A: Autorais ───────────────────────────────────────────────────────────
    TF011: wrap((ind, _prev, crossedRef) => {
      const tUp   = ind?.trend_up   === 1
      const tDown = ind?.trend_down === 1
      const slope = ind?.rsi_slope ?? 0
      const rsi   = ind?.rsi ?? 50
      const rsiOk = (tUp && slope > 0 && rsi > 50) || (tDown && slope < 0 && rsi < 50)
      const rsiConflict = (tUp && slope < 0) || (tDown && slope > 0)
      return [
        { id: 'regime', label: `C1 ${t('sc.chop_label')}`, sub: t('sc.chop_sub'), value: fmt(ind?.chop, 1), status: ind?.chop < 50 ? 'green' : ind?.chop < 61.8 ? 'yellow' : 'red', detail: ind?.chop < 50 ? t('sc.chop_trending') : t('sc.chop_sideways') },
        { id: 'ema',    label: `C2 ${t('sc.ema_label')}`,  sub: t('sc.ema_tf_sub'),   value: emaValue(ind), status: tUp || tDown ? 'green' : 'grey', detail: t('sc.ema_tf_sub') },
        { id: 'volume', label: `C3 ${t('sc.vol_label')}`,  sub: t('sc.vol_cfm_sub'),  value: `${fmt(ind?.volume_ratio, 2)}x`, status: ind?.volume_ratio >= 1.2 ? 'green' : 'yellow', detail: t('sc.vol_cfm_sub') },
        { id: 'rsi',    label: `C4 ${t('sc.rsi_slope_label')}`, sub: t('sc.rsi_slope_sub'), value: fmtS(slope, 1), status: rsiOk ? 'green' : rsiConflict ? 'red' : 'yellow', detail: rsiConflict ? `Momentum contrário à tendência EMA` : t('sc.rsi_slope_sub') },
      ]
    }),

    PA002: wrap((ind) => [
      { id: 'abcd', label: 'C1 AB≈CD', sub: 'Simetria', value: fmt(ind?.ab_cd_ratio, 2), status: ind?.ab_cd_ratio != null && ind.ab_cd_ratio >= 0.8 && ind.ab_cd_ratio <= 1.2 ? 'green' : 'red', detail: 'Simetria da correção detectada' },
      { id: 'trend', label: 'C2 EMA21', sub: 'Inércia', value: fmtS(ind?.ema_slope_pct, 2), status: ind?.trend_ok === 1 ? 'green' : 'red', detail: 'Preço acima da EMA21 inclinada' }
    ]),

    TF007: wrap((ind) => {
      const bias  = ind?.bias_4h  ?? 0
      const trend = ind?.trend_1h ?? 0
      const dirConflict = bias !== 0 && trend !== 0 && bias !== trend
      const trigAligned = (bias === 1 && ind?.trigger_bull) || (bias === -1 && ind?.trigger_bear)
      return [
        { id: 'bias_4h',  label: 'C1 4H Bias',   sub: 'Macro',       value: bias  === 1 ? 'Bull' : bias  === -1 ? 'Bear' : '—', status: bias  !== 0 ? 'green' : 'red',    detail: 'EMA 4H Slope' },
        { id: 'trend_1h', label: 'C2 1H Trend',  sub: 'Tendência',   value: trend === 1 ? 'Bull' : trend === -1 ? 'Bear' : '—', status: dirConflict ? 'red' : trend !== 0 ? 'green' : 'red', detail: dirConflict ? `Conflito: bias ${bias === 1 ? 'alta' : 'baixa'} ≠ trend ${trend === 1 ? 'alta' : 'baixa'}` : 'EMA 21/55 + RSI' },
        { id: 'trigger',  label: 'C3 Gatilho',   sub: 'MACD Cross',  value: fmtS(ind?.macd_hist, 4), status: trigAligned ? 'green' : 'yellow', detail: 'Cruzamento do Histograma' },
      ]
    }),

    MR001: wrap((ind) => {
      const buyZone  = !!ind?.in_buy_zone
      const sellZone = !!ind?.in_sell_zone
      const crossUp  = !!ind?.cross_up
      const crossDwn = !!ind?.cross_down
      const crossAligned = (buyZone && crossUp) || (sellZone && crossDwn)
      const crossConflict = (buyZone && crossDwn) || (sellZone && crossUp)
      return [
        { id: 'zone',  label: `C1 ${t('sc.mr_zone_label')}`,  sub: t('sc.mr_zone_sub'),  value: `${fmt(ind?.deviation, 2)}%`, status: buyZone || sellZone ? 'green' : 'red', detail: t('sc.mr_zone_sub') },
        { id: 'cross', label: `C2 ${t('sc.mr_cross_label')}`, sub: t('sc.mr_cross_sub'), value: fmtS(ind?.deviation - ind?.signal_line, 2), status: crossAligned ? 'green' : crossConflict ? 'yellow' : 'grey', detail: crossConflict ? 'Cruzamento na direção errada — aguardando reversão' : t('sc.mr_cross_sub') },
      ]
    }),

    TF008: wrap((ind) => [
      { id: 'cycle', label: 'C1 Ciclo Macro', sub: 'Weekly EMA 21', value: ind?.c1_full_above ? 'Rompido' : 'Bear', status: ind?.c1_full_above ? 'green' : 'red', detail: 'Vela semanal completa acima da EMA 21' },
      { id: 'support', label: 'C2 Suporte', sub: 'Confirmação', value: ind?.c2_support_ok ? 'Mantido' : 'Abaixo', status: ind?.c2_support_ok ? 'green' : 'red', detail: 'Preço sustentado acima da média semanal' },
      { id: 'test', label: 'C3 Proximidade', sub: 'Reteste', value: `${fmt(ind?.dist_ema_w, 2)}%`, status: ind?.dist_ema_w != null && Math.abs(ind.dist_ema_w) < 5 ? 'green' : 'yellow', detail: 'Distância ideal para entrada (até 5%)' }
    ]),

    IF002: wrap((ind) => [
      { id: 'spread', label: 'C1 Spread', sub: 'DEX vs OKX', value: ind?.spread_pct != null ? `${fmt(ind.spread_pct, 2)}%` : '—', status: ind?.spread_pct != null && Math.abs(ind.spread_pct) >= 0.3 ? 'green' : 'yellow', detail: ind?.spread_pct != null ? `Spread ${fmt(Math.abs(ind.spread_pct), 2)}% (mín: 0.3%)` : 'Aguardando dados DEX' },
      { id: 'net_spread', label: 'C2 Net Spread', sub: 'Pós taxas', value: ind?.net_spread_pct != null ? `${fmt(ind.net_spread_pct, 2)}%` : '—', status: ind?.net_spread_pct != null && ind.net_spread_pct > 0 ? 'green' : 'red', detail: 'Spread líquido após slippage + gas' },
      { id: 'liquidity', label: 'C3 Liquidez', sub: 'Volume DEX', value: ind?.liquidity_ok ? 'OK' : '—', status: ind?.liquidity_ok ? 'green' : 'yellow', detail: 'Liquidez mínima $100K nos pools' },
      { id: 'data_fresh', label: 'C4 Dados', sub: 'Freshness', value: ind?.data_age_sec != null ? `${Math.round(ind.data_age_sec)}s` : '—', status: ind?.data_age_sec != null && ind.data_age_sec <= 60 ? 'green' : 'red', detail: 'Dados DEX com até 60 segundos' }
    ]),
    TF006: wrap((ind) => {
      const adxVal  = ind?.adx_w ?? null
      const adxOk   = adxVal != null && adxVal >= 25 && ind?.adx_rising === 1
      const adxSt   = adxVal == null ? 'none' : adxVal >= 25 && ind?.adx_rising === 1 ? 'green' : adxVal >= 20 ? 'yellow' : 'red'
      const donBreak = ind?.donchian_break ?? 0
      const donSt    = donBreak !== 0 ? 'green' : adxOk ? 'yellow' : 'none'
      const donLabel = donBreak === 1 ? 'Máxima 52W' : donBreak === -1 ? 'Mínima 52W' : 'Dentro'
      const touched  = ind?.touched_ema21 === 1
      const touchSt  = touched ? 'green' : (donBreak !== 0 && adxOk) ? 'yellow' : 'none'
      const distPct  = ind?.dist_ema21_pct != null ? `${ind.dist_ema21_pct > 0 ? '+' : ''}${fmt(ind.dist_ema21_pct, 2)}%` : '—'
      const trigType = ind?.trigger_type ?? 0
      const trigSt   = trigType > 0 ? 'green' : (touched && adxOk && donBreak !== 0) ? 'yellow' : 'none'
      const trigLabel = trigType === 1 ? 'Rompeu semana' : trigType === 2 ? 'EMA 8 virou' : 'Aguardando'
      return [
        { id: 'adx',      label: 'C1 ADX Semanal',  sub: `${fmt(adxVal, 1)} / ${ind?.adx_rising === 1 ? '↑' : '→'}`,
          value:  adxVal != null ? fmt(adxVal, 1) : '—',
          status: adxSt,
          detail: adxOk ? `ADX ${fmt(adxVal, 1)} ≥ 25 e em alta — tendência forte confirmada`
                        : adxVal < 20 ? `ADX ${fmt(adxVal, 1)} < 20 — ativo lateral ignorado`
                        : `ADX ${fmt(adxVal, 1)} — aguardando cruzar 25 em alta` },
        { id: 'donchian', label: 'C2 Donchian 52W',  sub: 'Renovando extremo',
          value:  donLabel,
          status: donSt,
          detail: donBreak !== 0 ? `Renovando ${donLabel} — pressão vendedora/compradora histórica eliminada`
                                 : `Preço dentro do canal — aguardando rompimento (U:${fmt(ind?.donchian_upper, 2)} / L:${fmt(ind?.donchian_lower, 2)})` },
        { id: 'ema21',    label: 'C3 Recuo EMA 21',  sub: distPct,
          value:  touched ? 'Tocou' : distPct,
          status: touchSt,
          detail: touched ? 'EMA 21 tocada — preço em regressão à média confirmada'
                          : `Aguardando recuo à EMA 21 (${fmt(ind?.ema21, 2)})` },
        { id: 'trigger',  label: 'C4 Gatilho',        sub: trigLabel,
          value:  trigLabel,
          status: trigSt,
          detail: trigType === 1 ? `Fechou acima da máxima/mínima da semana anterior (${fmt(ind?.prev_week_high ?? ind?.prev_week_low, 4)})`
                : trigType === 2 ? 'EMA 8 reverteu na direção da tendência'
                : 'Aguardando: romper semana anterior OU EMA 8 virar' },
      ]
    }),

    PA001: wrap((ind) => [
      { id: 'bias', label: `C1 ${t('sc.bias_label')}`, sub: t('sc.bias_sub'), value: ind?.bias === 1 ? 'Bull' : 'Bear', status: ind?.bias === 1 ? 'green' : 'red', detail: t('sc.bias_sub') },
      { id: 'pullback', label: `C2 ${t('sc.pb_label')}`, sub: t('sc.pb_sub'), value: `${fmt(ind?.ema_dist_pct, 2)}%`, status: ind?.ema_dist_pct <= 0.5 ? 'green' : 'yellow', detail: t('sc.pb_sub') },
      { id: 'sr', label: `C3 ${t('sc.sr_label')}`, sub: t('sc.sr_sub'), value: fmt(ind?.sr_level, 2), status: ind?.sr_level > 0 ? 'green' : 'grey', detail: t('sc.sr_sub') }
    ]),

    PA003: wrap((ind) => [
      { id: 'zone', label: 'C1 Zona', sub: 'Retângulo', value: ind?.zone === 1 ? 'Topo' : ind?.zone === -1 ? 'Fundo' : 'Meio', status: ind?.zone !== 0 ? 'green' : 'red', detail: 'Localização no range lateral' },
      { id: 'range', label: 'C2 Range/ATR', sub: 'Espaço', value: fmt(ind?.range_atr, 1), status: ind?.atr_ok === 1 ? 'green' : 'red', detail: 'Amplitude do retângulo' },
      { id: 'wave', label: 'C3 Onda', sub: 'Wave 1/2', value: ind?.wave_ok ? 'OK' : '—', status: ind?.wave_ok ? 'green' : 'yellow', detail: 'Onda 1/2 confirmou topo/fundo' },
      { id: 'trigger', label: 'C4 Gatilho', sub: 'Upbar/Downbar', value: ind?.trigger_ok ? 'OK' : '—', status: ind?.trigger_ok ? 'green' : 'yellow', detail: 'Candle de gatilho na borda do range' },
      { id: 'rr', label: 'C5 R:R', sub: 'Mínimo', value: fmt(ind?.rr, 2), status: ind?.rr >= 2 ? 'green' : 'yellow', detail: 'Risco/retorno mínimo do setup' }
    ]),

    SC001: wrap((ind) => [
      { id: 'manipulation', label: 'C1 Manipulação', sub: '>20% ATR Diário', value: ind?.atr_manipulation ? 'SIM' : 'NÃO', status: ind?.atr_manipulation ? 'green' : 'yellow', detail: ind?.atr_manipulation ? 'Candle de exaustão detectado' : 'Aguardando exaustão' },
      { id: 'pattern', label: 'C2 Padrão Rejeição', sub: 'John Wick / Power Tower', value: ind?.pattern || '—', status: ind?.pattern ? 'green' : 'none', detail: ind?.pattern_desc || 'Buscando sinal' }
    ]),

    TF010: wrap((ind) => [
      { id: 'stoch', label: 'C1 Stoch K/D', sub: 'Exaustão', value: `${fmt(ind?.stoch_k, 0)}/${fmt(ind?.stoch_d, 0)}`, status: ind?.stoch_k > 80 || ind?.stoch_k < 20 ? 'green' : 'yellow', detail: 'Níveis de sobrecompra/sobrevenda' }
    ]),

    PA004: wrap((ind) => [
      { id: 'pattern_3lb', label: 'C1 3LB Pattern', sub: 'C1 < C2 < C3', value: ind?.c3_high ? 'DETECTADO' : 'AGUARDANDO', status: ind?.c3_high ? 'green' : 'yellow', detail: ind?.c3_high ? `C3 ${fmt(ind?.c3_high)} > C2 ${fmt(ind?.c2_high)} > C1 ${fmt(ind?.c1_low)}` : 'Aguardando C3 > C2 > C1' },
      { id: 'context', label: 'C2 Contexto', sub: 'Acima/abaixo EMA200', value: ind?.in_uptrend ? `Favoravel (RR ${fmt(ind?.rr)}:1)` : `Reversao (RR ${fmt(ind?.rr)}:1)`, status: ind?.c3_high ? 'green' : 'grey', detail: `EMA200 = ${fmt(ind?.ema_trend)}, Close = ${fmt(ind?.close)}` },
      { id: 'rsi', label: 'C3 RSI', sub: '<30 sobrevenda', value: `${fmt(ind?.rsi, 0)}`, status: ind?.rsi < 30 ? 'green' : 'red', detail: `RSI ${fmt(ind?.rsi, 0)} ${ind?.rsi < 30 ? '<' : '>='} 30` },
      { id: 'c3_bullish', label: 'C4 Candle C3', sub: 'Close > Open', value: ind?.c3_bullish ? 'ALTA' : 'BAIXA', status: ind?.c3_bullish ? 'green' : 'yellow', detail: ind?.c3_bullish ? 'C3 fechou em alta ✓' : 'C3 fechou em baixa (menos ideal)' },
      { id: 'volume', label: 'C5 Volume', sub: `${fmt(ind?.vol_ratio, 1)}x media`, value: `${fmt(ind?.vol_ratio, 1)}x`, status: ind?.vol_ratio >= 1.0 ? 'green' : 'grey', detail: `Volume ${fmt(ind?.vol_ratio, 1)}x da media` },
    ]),

    MR003: wrap((ind) => {
      const isOutside = ind?.outside_band === 1.0;
      return [
        { id: 'exhaustion', label: 'C1 Exaustão', sub: 'Fora da Banda', value: isOutside ? 'DETECTADA' : 'DENTRO', status: isOutside ? 'green' : 'yellow', detail: 'Candle anterior totalmente fora' },
        { id: 'rsi', label: 'C2 RSI', sub: 'Filtro Força', value: fmt(ind?.rsi, 1), status: (ind?.rsi < 40 || ind?.rsi > 60) ? 'green' : 'grey', detail: 'RSI em zona de exaustão' },
        { id: 'macd', label: 'C3 MACD', sub: 'Momento', value: 'Perda Força', status: 'green', detail: 'Convergência iniciada' },
        { id: 'trigger', label: 'C4 Gatilho', sub: 'Rompimento', value: 'Pendente', status: 'yellow', detail: 'Aguardando quebra do extremo' }
      ]
    }),

    MR004: wrap((ind) => [
      { id: 'rsi_force', label: 'C1 Força RSI', sub: 'Filtro 50', value: fmt(ind?.rsi, 1), status: ind?.rsi > 50 ? 'green' : ind?.rsi < 50 ? 'red' : 'yellow', detail: ind?.rsi > 50 ? 'Bullish (Acima 50)' : 'Bearish (Abaixo 50)' },
      { id: 'momentum', label: 'C2 Momentum', sub: 'EMA 3 vs WMA 21', value: ind?.ema_rsi > ind?.wma_rsi ? 'ALTA' : 'BAIXA', status: (ind?.rsi > 50 && ind?.ema_rsi > ind?.wma_rsi) || (ind?.rsi < 50 && ind?.ema_rsi < ind?.wma_rsi) ? 'green' : 'yellow', detail: 'Cruzamento de linhas HM' },
      { id: 'volume', label: 'C3 Volume', sub: 'WMA 21 vs RSI', value: 'Confirmando', status: (ind?.rsi > 50 && ind?.wma_rsi < ind?.rsi) || (ind?.rsi < 50 && ind?.wma_rsi > ind?.rsi) ? 'green' : 'yellow', detail: 'Posição da linha vermelha' },
      { id: 'gap', label: 'C4 Explosão', sub: 'Gap de Linhas', value: fmt(ind?.dist_hm, 2), status: ind?.dist_hm > 2.0 ? 'green' : 'grey', detail: 'Distância entre médias do RSI' }
    ]),

    MR002: (_ind) => {
      const ind = _ind
      const aboveOuter = ind && ind.close > ind.outer_u
      const belowOuter = ind && ind.close < ind.outer_l
      const aboveInner = ind && ind.close > ind.inner_u
      const belowInner = ind && ind.close < ind.inner_l
      return [
        { id: 'outer', label: 'C1 Banda 4σ', sub: 'Exaustão Ext.',
          value:  !ind ? '—' : (aboveOuter ? 'Acima' : belowOuter ? 'Abaixo' : 'Dentro'),
          status: !ind ? 'none' : (aboveOuter || belowOuter) ? 'green' : 'yellow',
          detail: !ind ? '—' : (aboveOuter || belowOuter) ? 'Toque na banda externa detectado' : 'Aguardando toque externo' },
        { id: 'inner', label: 'C2 Banda 2σ', sub: 'Rejeição Int.',
          value:  !ind ? '—' : (aboveInner ? 'Acima' : belowInner ? 'Abaixo' : 'Dentro'),
          status: !ind ? 'none' : (aboveInner || belowInner) ? 'yellow' : 'green',
          detail: !ind ? '—' : 'Posição em relação à banda interna (2σ)' },
        { id: 'zone', label: 'C3 Zona R/S', sub: 'Oferta/Demanda',
          value:  !ind ? '—' : `R:${fmt(ind.resistencia,0)} / S:${fmt(ind.suporte,0)}`,
          status: !ind ? 'none' : (ind.close > ind.resistencia || ind.close < ind.suporte) ? 'green' : 'grey',
          detail: !ind ? '—' : 'Rompimento de zona de oferta ou demanda' },
      ]
    },

    MR005: (_ind) => {
      const ind = _ind
      return [
        { id: 'rsi', label: 'C1 RSI', sub: 'Exaustão',
          value:  fmt(ind?.rsi, 1),
          status: !ind ? 'none' : (ind.rsi < 30 || ind.rsi > 70) ? 'green' : (ind.rsi < 40 || ind.rsi > 60) ? 'yellow' : 'grey',
          detail: !ind ? '—' : ind.rsi < 30 ? 'Sobrevenda — Rubber Band Long' : ind.rsi > 70 ? 'Sobrecompra — Rubber Band Short' : 'Neutro' },
        { id: 'bb_pos', label: 'C2 Posição BB', sub: 'Toque Banda',
          value:  !ind ? '—' : ind.close > ind.bb_u ? 'Acima Upper' : ind.close < ind.bb_l ? 'Abaixo Lower' : 'Dentro',
          status: !ind ? 'none' : (ind.close > ind.bb_u || ind.close < ind.bb_l) ? 'green' : 'grey',
          detail: !ind ? '—' : 'Slingshot ou Rubber Band ativo' },
        { id: 'sma', label: 'C3 SMA 20', sub: 'Staircase',
          value:  !ind ? '—' : ind.close > ind.bb_m ? 'Acima' : 'Abaixo',
          status: !ind ? 'none' : ind.close > ind.bb_m ? 'green' : 'red',
          detail: !ind ? '—' : ind.close > ind.bb_m ? 'Preço acima da SMA 20 (bias alta)' : 'Preço abaixo da SMA 20 (bias baixa)' },
      ]
    },

    PA005: wrap((ind) => [
      { id: 'support', label: 'C1 Suporte', sub: 'Zona de Demanda', value: ind?.support_ok ? 'DETECTADA' : 'BUSCANDO', status: ind?.support_ok ? 'green' : 'yellow', detail: 'Identificação de fundo anterior' },
      { id: 'sweep', label: 'C2 Varredura', sub: 'Liquidity Sweep', value: ind?.sweep_ok ? 'OK' : 'AGUARDANDO', status: ind?.sweep_ok ? 'green' : 'yellow', detail: 'Violação e retorno ao range' },
      { id: 'choch', label: 'C3 CHoCH', sub: 'Confirmação', value: ind?.choch_ok ? 'QUEBRA' : 'ESTÁVEL', status: ind?.choch_ok ? 'green' : 'yellow', detail: 'Quebra de estrutura de baixa' }
    ]),

    PA006: wrap((ind) => {
      const sweepOk  = ind?.sweep_detected === 1.0
      const chochOk  = ind?.choch_detected === 1.0
      const fibLo    = ind?.fib_low  || 0
      const fibHi    = ind?.fib_high || 0
      const price    = ind?.close    || 0
      const inFib    = fibLo > 0 && fibHi > 0 && price >= fibLo && price <= fibHi
      const atrOk    = ind?.entry > 0  // entry price set = ATR ativo
      const sweepType = ind?.sweep_type || ''
      return [
        { id: 'sweep',    label: 'C1 Varredura HTF', sub: 'SSL / BSL + Rejeição',
          value:  sweepOk ? (sweepType === 'ssl' ? 'SSL ✓' : sweepType === 'bsl' ? 'BSL ✓' : 'OK') : 'BUSCANDO',
          status: sweepOk ? 'green' : 'yellow',
          detail: sweepOk ? `Captura ${sweepType.toUpperCase()} confirmada com pavio de rejeição` : 'Aguardando varredura de liquidez no 1H' },
        { id: 'reject',   label: 'C2 Rejeição',      sub: 'Fechamento no Range',
          value:  sweepOk ? 'CONFIRMADA' : '—',
          status: sweepOk ? 'green' : 'grey',
          detail: sweepOk ? 'Vela fechou de volta ao range após a varredura' : 'Depende da C1' },
        { id: 'choch',    label: 'C3 CHoCH 5M',      sub: 'Quebra de Estrutura',
          value:  chochOk ? 'QUEBROU' : sweepOk ? 'AGUARDANDO' : '—',
          status: chochOk ? 'green' : sweepOk ? 'yellow' : 'grey',
          detail: chochOk ? 'Rompimento do último topo descendente no 5M confirmado' : 'Aguardando Change of Character no 5M' },
        { id: 'fib',      label: 'C4 Zona Fibonacci', sub: '61.8% – 78.6%',
          value:  inFib ? 'NA ZONA' : chochOk && fibHi > 0 ? `Alvo ${fmt(fibLo,2)}–${fmt(fibHi,2)}` : '—',
          status: inFib ? 'green' : chochOk && fibHi > 0 ? 'yellow' : 'grey',
          detail: inFib ? 'Preço na zona de desconto — entrada permitida' : chochOk ? 'Aguardando recuo para a zona Fib' : 'Calculada após CHoCH' },
        { id: 'liquidity', label: 'C5 Liquidez ATR',  sub: 'Sessão ativa',
          value:  atrOk ? 'ATIVO' : '—',
          status: atrOk ? 'green' : 'grey',
          detail: atrOk ? 'ATR ativo — mercado com liquidez suficiente para entrada' : 'Aguardando zona Fib' },
      ]
    }),

    SC002: wrap((ind) => {
      const bias = ind?.daily_bias || 0
      const force = ind?.force_close || 0
      const fvg = ind?.fvg_signal || 0
      const ignored = ind?.ignored_bar_signal || 0
      const playersAvailable = ind?.market_players_available === 1
      const playersOk = ind?.market_players_confirmation === 1
      const playersRequired = ind?.market_players_required === 1
      const signal = ind?.signal || 'hold'
      const confirmationOk = fvg !== 0 || ignored !== 0
      return [
        { id: 'opening_vwap', label: `C1 ${t('sc.viana_opening_label')}`, sub: t('sc.viana_opening_sub'),
          value: bias === 1 ? t('sc.viana_bias_buy') : bias === -1 ? t('sc.viana_bias_sell') : t('sc.viana_bias_neutral'),
          status: bias !== 0 ? 'green' : 'yellow',
          detail: ind?.opening_high ? `${t('sc.viana_opening_range')} ${fmt(ind.opening_low, 2)}-${fmt(ind.opening_high, 2)} | VWAP ${fmt(ind?.vwap, 2)}` : t('sc.no_data') },
        { id: 'vwap_rejection', label: `C2 ${t('sc.viana_rejection_label')}`, sub: t('sc.viana_rejection_sub'),
          value: ind?.vwap_rejection === 1 ? t('sc.viana_rejected') : `${fmt(ind?.price_vs_vwap_pct, 2)}%`,
          status: ind?.vwap_rejection === 1 ? 'green' : 'yellow',
          detail: ind?.vwap_rejection === 1 ? t('sc.viana_rejection_ok') : t('sc.viana_rejection_wait') },
        { id: 'force_close', label: `C3 ${t('sc.viana_force_label')}`, sub: t('sc.viana_force_sub'),
          value: force === 1 ? 'BUY' : force === -1 ? 'SELL' : '—',
          status: force !== 0 ? 'green' : 'yellow',
          detail: force === 1 ? t('sc.viana_force_buy') : force === -1 ? t('sc.viana_force_sell') : t('sc.viana_force_wait') },
        { id: 'confirmation', label: `C4 ${t('sc.viana_confirm_label')}`, sub: 'FVG / Inside',
          value: fvg !== 0 ? 'FVG' : ignored !== 0 ? t('sc.viana_ignored_value') : '—',
          status: confirmationOk ? 'green' : 'yellow',
          detail: fvg === 1 ? t('sc.viana_fvg_buy') : fvg === -1 ? t('sc.viana_fvg_sell') : ignored === 1 ? t('sc.viana_ignored_buy') : ignored === -1 ? t('sc.viana_ignored_sell') : t('sc.viana_confirm_wait') },
        { id: 'players', label: `C5 ${t('sc.viana_players_label')}`, sub: t('sc.viana_players_sub'),
          value: playersOk ? t('sc.viana_players_ok_value') : playersAvailable ? t('sc.viana_players_no_value') : t('sc.viana_players_missing_value'),
          status: playersOk ? 'green' : playersRequired ? 'red' : playersAvailable ? 'yellow' : 'grey',
          detail: playersOk ? t('sc.viana_players_ok') : playersAvailable ? t('sc.viana_players_no') : t('sc.viana_players_missing') },
        { id: 'volume', label: `C6 ${t('sc.vol_label')}`, sub: t('sc.vol_sub'),
          value: `${fmt(ind?.volume_ratio, 2)}x`,
          status: ind?.volume_ratio >= 1 ? 'green' : 'yellow',
          detail: t('sc.vol_sub') },
        { id: 'risk', label: `C7 ${t('sc.viana_risk_label')}`, sub: 'SL / TP1',
          value: signal === 'buy' ? t('c.buy') : signal === 'sell' ? t('c.sell') : 'HOLD',
          status: signal === 'buy' || signal === 'sell' ? 'green' : 'grey',
          detail: ind?.sl_price ? `SL ${fmt(ind.sl_price, 2)} | TP1 ${fmt(ind.tp1_price, 2)}` : t('sc.viana_risk_wait') },
      ]
    }),

    TF012: wrap((ind) => {
      const isLong = ind?.trend_metric >= 0;
      const extremeHit = (ind?.trend_metric > ind?.extreme_high) || (ind?.trend_metric < ind?.extreme_low);
      return [
        { id: 'trend', label: 'C1 Tendência', sub: 'EMA 200', value: ind?.close > ind?.ema200 ? 'ALTA' : 'BAIXA', status: (ind?.close > ind?.ema200 ? 'green' : 'red'), detail: 'Preço em relação à média' },
        { id: 'liquidity', label: 'C2 Liquidez', sub: 'Rejeição', value: extremeHit ? 'EXTREMO' : 'NORMAL', status: extremeHit ? 'green' : 'yellow', detail: 'Identificação de ondas de rejeição' },
        { id: 'trigger', label: 'C3 Gatilho', sub: 'Trend Metric', value: fmt(ind?.trend_metric, 4), status: Math.abs(ind?.trend_metric) < 0.001 ? 'yellow' : 'green', detail: 'Cruzamento da linha zero' }
      ]
    }),

    // ── M: Markov / Probabilísticas ──────────────────────────────────────────
    RG001: (_ind, _prev, _ref) => {
      const ind = _ind
      const regimeName = !ind ? '—' : ind.current_regime === 0 ? 'Bear' : ind.current_regime === 1 ? 'Sideways' : 'Bull'
      return [
        { id: 'signal',      label: 'C1 Sinal',       sub: 'P(Bull)−P(Bear)',
          value:  ind ? fmtS(ind.signal, 3)      : '—',
          status: !ind ? 'none' : ind.signal > 0 ? 'green' : ind.signal < -0.10 ? 'red' : 'yellow',
          detail: !ind ? '—' : ind.signal > 0 ? 'Favorece alta' : ind.signal < -0.10 ? 'Reversal iminente' : 'Neutro' },
        { id: 'regime',      label: 'C2 Regime',      sub: 'Não Bear',
          value:  regimeName,
          status: !ind ? 'none' : ind.current_regime !== 0 ? 'green' : 'red',
          detail: !ind ? '—' : ind.current_regime !== 0 ? `Regime ${regimeName} ativo` : 'Regime Bear — sem entrada' },
        { id: 'persistence', label: 'C3 Persistência', sub: 'P(i→i) ≥ 0.50',
          value:  fmt(ind?.persistence, 2),
          status: !ind ? 'none' : ind.persistence >= 0.5 ? 'green' : 'yellow',
          detail: `P(manter regime) = ${fmt(ind?.persistence, 2)}` },
        { id: 'stationary',  label: 'C4 Estacionário', sub: 'Bull > Bear LP',
          value:  !ind ? '—' : `${fmt((ind.stat_bull ?? 0) * 100, 0)}% / ${fmt((ind.stat_bear ?? 0) * 100, 0)}%`,
          status: !ind ? 'none' : (ind.stat_bull ?? 0) > (ind.stat_bear ?? 0) ? 'green' : 'red',
          detail: !ind ? '—' : (ind.stat_bull ?? 0) > (ind.stat_bear ?? 0) ? 'Longo prazo favorece Bull' : 'Longo prazo favorece Bear' },
      ]
    },

    RG004: (ind) => [
      { id: 'location', label: 'C1 Localização', sub: 'Caro / Barato',
        value: ind?.location_score != null ? fmtS(ind.location_score, 2) : '—',
        status: !ind ? 'none' : ind.c1_location === 1 ? 'green' : 'yellow',
        detail: ind?.location_score != null
          ? 'Preço vs abertura do dia e 50% macro'
          : 'Aguardando contexto macro de 15m' },
      { id: 'volatility', label: 'C2 Frequência', sub: 'Range / ATR',
        value: ind?.vol_signature != null ? `${fmt(ind.vol_signature, 2)}×` : '—',
        status: !ind ? 'none' : ind.c2_volatility === 1 ? 'green' : 'yellow',
        detail: ind?.vol_signature != null
          ? 'Expansão/desbalanceamento ou equilíbrio extremo'
          : 'Aguardando assinatura de volatilidade' },
      { id: 'rejection', label: 'C3 Absorção', sub: 'Pavio / Liquidez',
        value: ind?.lower_wick_ratio != null || ind?.upper_wick_ratio != null
          ? `${fmt(Math.max(ind.lower_wick_ratio ?? 0, ind.upper_wick_ratio ?? 0), 2)}`
          : '—',
        status: !ind ? 'none' : ind.c3_rejection === 1 ? 'green' : 'yellow',
        detail: ind?.lower_wick_ratio != null || ind?.upper_wick_ratio != null
          ? 'Pavio dominante tocando máxima/mínima estrutural'
          : 'Aguardando captura de liquidez' },
      { id: 'markov', label: 'C4 Markov', sub: 'P(transição) + R:R',
        value: ind?.p_buy != null || ind?.p_sell != null
          ? `B ${fmt((ind.p_buy ?? 0) * 100, 0)}% / S ${fmt((ind.p_sell ?? 0) * 100, 0)}%`
          : '—',
        status: !ind ? 'none' : ind.c4_markov === 1 ? 'green' : 'yellow',
        detail: ind?.transition_count != null
          ? `Amostra ${fmt(ind.transition_count, 0)} · RR B ${fmt(ind.rr_buy, 2)} / S ${fmt(ind.rr_sell, 2)}`
          : 'Aguardando matriz de transição' },
    ],

    // ── I: Inteligência ───────────────────────────────────────────────────────
    RG002: wrap((ind) => [
      { id: 'regime', label: `C1 ${t('sc.gr_regime_label')}`, sub: t('sc.gr_regime_sub'), value: ind?.regime ?? '—', status: ind?.regime === 'trending' ? 'green' : 'yellow', detail: t('sc.gr_regime_sub') },
      { id: 'confidence', label: `C2 ${t('sc.conf_label')}`, sub: 'Confiança', value: `${fmt(ind?.regime_conf * 100, 0)}%`, status: ind?.regime_conf >= 0.7 ? 'green' : 'yellow', detail: t('sc.conf_label') }
    ]),
    
    NW001: wrap((ind) => {
      const hasInfluencer = ind?.influencer != null || ind?.regime === 'trending'
      const influencerName = ind?.influencer ?? (ind?.regime === 'trending' ? 'ID...' : '—')
      const hasFollowerMatch = ind?.target_is_follower === 1
      const followerMismatch = ind?.follower != null && !hasFollowerMatch
      const followerName = ind?.follower ?? '—'
      const impulseRatio = ind?.leader_impulse_ratio
      const impulseOk = impulseRatio != null && impulseRatio >= 2.0
      const followerRsi = ind?.follower_rsi
      const rsiOk = followerRsi != null && followerRsi < 62.0
      return [
        { id: 'influencer', label: 'C1 Líder', sub: 'PageRank',
          value: influencerName,
          status: hasInfluencer ? 'green' : 'grey',
          detail: hasInfluencer ? `Influenciador: ${ind?.influencer ?? 'identificado'}` : 'Grafo calculando influenciador' },
        { id: 'follower', label: 'C2 Seguidor', sub: 'Símbolo do bot',
          value: hasFollowerMatch ? 'OK' : followerName,
          status: hasFollowerMatch ? 'green' : followerMismatch ? 'red' : 'yellow',
          detail: hasFollowerMatch ? 'Símbolo configurado é o seguidor'
            : followerMismatch ? `Grafo quer operar: ${followerName}`
            : 'Aguardando identificação de seguidor' },
        { id: 'impulse', label: 'C3 Impulso', sub: '× ATR',
          value: impulseRatio != null ? `${fmt(impulseRatio, 2)}× ATR` : '—',
          status: impulseOk ? 'green' : impulseRatio != null ? 'yellow' : 'grey',
          detail: impulseOk ? 'Vela impulsiva do líder confirmada' : 'Aguardando vela impulsiva (≥ 2× ATR)' },
        { id: 'follower_rsi', label: 'C4 RSI Seguidor', sub: '< 62 (long)',
          value: followerRsi != null ? fmt(followerRsi, 1) : '—',
          status: rsiOk ? 'green' : followerRsi != null ? 'yellow' : 'grey',
          detail: rsiOk ? 'RSI em zona de entrada' : `RSI fora da zona (target < 62 para long)` },
      ]
    }),

    SC003: wrap((ind) => [
      { id: 'zone', label: 'C1 Zona Inst.', sub: 'VAH / VAL', value: ind?.val != null ? ((ind?.close ?? 0) < ind.val ? 'Fundo (VAL)' : (ind?.close ?? 0) > (ind?.vah ?? 0) ? 'Topo (VAH)' : 'Meio (POC)') : '—', status: (ind?.close != null && ind?.val != null && ind.close < ind.val) || (ind?.close != null && ind?.vah != null && ind.close > ind.vah) ? 'green' : 'yellow', detail: 'Buscando extremidades' },
      { id: 'delta', label: 'C2 Delta', sub: 'Agressão', value: ind?.delta === 1 ? 'Compra' : ind?.delta === -1 ? 'Venda' : 'Neutro', status: ind?.delta !== 0 ? 'green' : 'yellow', detail: 'Fluxo Institucional' },
      { id: 'climax', label: 'C3 Clímax', sub: 'Volume', value: `${fmt(ind?.vol_ratio, 1)}x`, status: ind?.vol_ratio > 1.5 ? 'green' : 'grey', detail: 'Volume climático' }
    ]),

    IF001: wrap((ind) => [
      { id: 'whale_score', label: 'C1 Whale score', sub: 'Contexto On-chain', value: `${fmt(ind?.whale_score, 0)}/100`, status: ind?.whale_score >= 70 ? 'green' : 'yellow', detail: 'Pontuação de movimentação de baleias' }
    ]),
    IF003: wrap((ind) => [
      { id: 'poc', label: 'C1 Liquidez', sub: 'POC / Zona Valor', value: fmt(ind?.poc, 2), status: ind?.c1_ok ? 'green' : 'yellow', detail: 'Proximidade do Volume Profile' },
      { id: 'atemporal', label: 'C2 Atemporal', sub: 'Bloco Reversão', value: ind?.last_block_type === 1.0 ? 'BULL' : ind?.last_block_type === -1.0 ? 'BEAR' : '—', status: ind?.c2_ok ? 'green' : 'grey', detail: 'Fechamento de bloco Range' },
      { id: 'aggression', label: 'C3 Agressão', sub: 'Volume Delta', value: ind?.c3_ok ? 'ALTA' : 'NORMAL', status: ind?.c3_ok ? 'green' : 'grey', detail: 'Fluxo institucional detectado' },
      { id: 'confirmation', label: 'C4 Confirm.', sub: 'Fechamento', value: ind?.c4_ok ? 'FORTE' : 'NEUTRO', status: ind?.c4_ok ? 'green' : 'grey', detail: 'Aceitação do novo preço' }
    ]),

    TF009: wrap((ind) => {
      const fast = ind?.fast_ma
      const slow = ind?.slow_ma
      const hasMa = fast != null && slow != null && isFinite(fast) && isFinite(slow)
      const gap   = hasMa ? fast - slow : null
      const gapPct = hasMa ? Math.abs(gap) / (ind.close || 1) * 100 : null
      const maStatus = !hasMa ? 'none' : gap > 0 ? 'green' : 'red'
      const maValue  = hasMa ? `${gap >= 0 ? '+' : ''}${fmt(gap, 0)} pts` : '—'
      const strengthStatus = gapPct == null ? 'none' : gapPct >= 0.1 ? 'green' : gapPct >= 0.03 ? 'yellow' : 'grey'
      return [
        { id: 'ma_cross', label: 'C1 Cruzamento MA', sub: 'Rápida vs Lenta',
          value: maValue,
          status: maStatus,
          detail: !hasMa ? 'Aguardando dados' : gap > 0 ? 'MA rápida acima da lenta — tendência de alta' : 'MA rápida abaixo da lenta — tendência de baixa' },
        { id: 'ma_strength', label: 'C2 Força', sub: 'Distância %',
          value: gapPct != null ? `${fmt(gapPct, 2)}%` : '—',
          status: strengthStatus,
          detail: gapPct != null ? `Separação entre MAs: ${fmt(gapPct, 2)}%` : 'Aguardando dados' },
        { id: 'atr', label: `C3 ${t('sc.atr_label')}`, sub: t('sc.atr_sub'),
          value: fmt(ind?.atr, 2),
          status: ind?.atr ? 'green' : 'none',
          detail: t('sc.atr_detail') },
      ]
    }),

    TF014: wrap((ind) => {
      const bias = ind?.bias
      const hasBias = bias != null && bias !== 0
      const biasLabel = bias === 1 ? 'Green Light ↑' : bias === -1 ? 'Red Light ↓' : 'Neutro'
      const biasStatus = bias === 1 ? 'green' : bias === -1 ? 'red' : 'grey'
      const closePx = ind?.close
      const ema_micro = ind?.ema_micro
      const ema_macro = ind?.ema_macro
      const hasMicro = closePx != null && ema_micro != null
      const microDist = hasMicro ? closePx - ema_micro : null
      const microStatus = !hasMicro ? 'none' : bias === 1
        ? (closePx > ema_micro ? 'green' : Math.abs(microDist / closePx) < 0.002 ? 'yellow' : 'red')
        : bias === -1
        ? (closePx < ema_micro ? 'green' : Math.abs(microDist / closePx) < 0.002 ? 'yellow' : 'red')
        : 'grey'
      return [
        { id: 'bias', label: 'C1 Viés EMA Macro', sub: `EMA${ind ? '' : ''}`,
          value: biasLabel,
          status: biasStatus,
          detail: hasBias
            ? `Preço ${bias === 1 ? 'acima' : 'abaixo'} da EMA macro com inclinação ${bias === 1 ? 'positiva' : 'negativa'}`
            : 'EMA macro sem inclinação clara — lateralização' },
        { id: 'trigger', label: 'C2 Gatilho', sub: 'Pullback / Rompimento',
          value: hasMicro ? `${microDist >= 0 ? '+' : ''}${fmt(microDist, 0)} pts` : '—',
          status: microStatus,
          detail: !hasMicro ? 'Aguardando dados' : bias === 1
            ? (closePx > ema_micro ? 'Preço acima da EMA micro — pullback confirmado' : 'Aguardando recuo à EMA micro')
            : bias === -1
            ? (closePx < ema_micro ? 'Preço abaixo da EMA micro — pullback confirmado' : 'Aguardando recuo à EMA micro')
            : 'Sem viés ativo' },
      ]
    }),

    PA007: wrap((ind) => {
      const bias = ind?.bias
      const emaFast = ind?.ema_fast
      const emaSlow = ind?.ema_slow
      const hasBias = bias != null && bias !== 0
      const biasStatus = bias === 1 ? 'green' : bias === -1 ? 'red' : 'grey'
      const biasLabel  = bias === 1 ? 'Alta' : bias === -1 ? 'Baixa' : 'Neutro'
      const volRatio = (ind?.close != null && ind?.volume_ma != null && ind?.volume_ma > 0)
        ? null : null
      const zoneTouch = ind?.zone_touch === 1.0
      const chochOk   = ind?.choch === 1.0
      return [
        { id: 'bias', label: 'C1 Viés', sub: 'EMA Fast/Slow',
          value: biasLabel,
          status: biasStatus,
          detail: hasBias ? `EMA rápida ${bias === 1 ? 'acima' : 'abaixo'} da lenta — tendência ${bias === 1 ? 'de alta' : 'de baixa'}` : 'EMAs próximas — sem direção clara' },
        { id: 'volume', label: 'C2 Volume', sub: 'Acima da Média',
          value: ind?.volume_ma != null ? 'Verificando' : '—',
          status: ind?.volume_ma != null ? 'green' : 'grey',
          detail: 'Volume acima da média confirma pressão direcional' },
        { id: 'zone', label: 'C3 Zona S/D', sub: 'Oferta/Demanda',
          value: zoneTouch ? 'TOCANDO' : 'FORA',
          status: zoneTouch ? 'green' : 'yellow',
          detail: zoneTouch ? 'Preço na zona de oferta/demanda identificada' : 'Aguardando toque na zona relevante' },
        { id: 'choch', label: 'C4 CHoCH', sub: 'Quebra Estrutura',
          value: chochOk ? 'QUEBROU' : 'ESTÁVEL',
          status: chochOk ? 'green' : 'yellow',
          detail: chochOk ? 'Quebra de estrutura confirmada — entrada válida' : 'Aguardando Change of Character' },
      ]
    }),

    PA008: wrap((ind) => {
      const orbHigh = ind?.orb_high
      const orbLow  = ind?.orb_low
      const orbMid  = ind?.orb_mid
      const breakDir = ind?.breakout_direction
      const closePx  = ind?.close
      const orbDefined = orbHigh != null && orbLow != null && orbHigh > 0 && orbLow > 0
      const orbRangePct = orbDefined ? ((orbHigh - orbLow) / orbLow) * 100 : null
      const orbStatus = !orbDefined ? 'none' : orbRangePct <= 2.0 ? 'green' : 'yellow'
      const brkStatus = !breakDir ? 'grey' : breakDir !== 0 ? 'green' : 'yellow'
      const brkLabel  = breakDir === 1 ? 'Alta ↑' : breakDir === -1 ? 'Baixa ↓' : 'Sem Rompimento'
      const inRetest = orbDefined && breakDir !== 0 && closePx != null
        ? (breakDir === 1 ? (closePx <= orbHigh && closePx >= orbMid) : (closePx >= orbLow && closePx <= orbMid))
        : false
      return [
        { id: 'orb', label: 'C1 ORB', sub: 'Range de Abertura',
          value: orbDefined ? `${fmt(orbRangePct, 2)}% range` : '—',
          status: orbStatus,
          detail: orbDefined ? `ORB: ${fmt(orbLow, 2)} – ${fmt(orbHigh, 2)} (${fmt(orbRangePct, 2)}%)` : 'Aguardando definição do range de abertura' },
        { id: 'breakout', label: 'C2 Rompimento', sub: 'Fora do ORB',
          value: brkLabel,
          status: brkStatus,
          detail: breakDir === 1 ? `Fechou acima do ORB high (${fmt(orbHigh, 2)})` : breakDir === -1 ? `Fechou abaixo do ORB low (${fmt(orbLow, 2)})` : 'Aguardando fechamento fora do range' },
        { id: 'retest', label: 'C3 Reteste', sub: 'Retorno à Linha',
          value: inRetest ? 'RETESTANDO' : breakDir !== 0 ? 'AGUARDANDO' : '—',
          status: inRetest ? 'green' : breakDir !== 0 ? 'yellow' : 'grey',
          detail: inRetest ? 'Preço retestando a linha rompida — entrada válida' : breakDir !== 0 ? `Aguardando recuo para zona (mid: ${fmt(orbMid, 2)})` : 'Depende do rompimento' },
      ]
    }),

    RG003: (ind) => {
      const regime = ind?.gex_regime
      const regimeLabel = regime === 1 ? 'Gama Positivo' : regime === -1 ? 'Gama Negativo' : 'Neutro'
      const regimeStatus = regime === 1 ? 'green' : regime === -1 ? 'yellow' : 'grey'
      const c2Ok = ind?.c2_level === 1.0
      const c3Ok = ind?.c3_volume === 1.0
      const c4Ok = ind?.c4_atr === 1.0
      const gexVal = ind?.gex_value
      const pcr = ind?.pcr
      return [
        { id: 'regime', label: 'C1 Regime GEX', sub: 'Deribit / BBW',
          value: regimeLabel,
          status: regimeStatus,
          detail: regime === 1 ? 'Gama Positivo — mercado amortecido, range strategy' : regime === -1 ? 'Gama Negativo — mercado explosivo, breakout strategy' : 'Regime neutro — aguardando clareza' },
        { id: 'level', label: 'C2 Nível OI', sub: 'Suporte/Resistência',
          value: c2Ok ? 'PRÓXIMO' : ind?.nearest_support ? `S:${fmt(ind.nearest_support, 0)}` : '—',
          status: c2Ok ? 'green' : 'yellow',
          detail: c2Ok ? 'Preço próximo ou rompendo nível relevante de Open Interest' : `S:${fmt(ind?.nearest_support, 0)} / R:${fmt(ind?.nearest_resistance, 0)}` },
        { id: 'volume', label: 'C3 Volume', sub: 'Spike Institucional',
          value: ind?.vol_ratio != null ? `${fmt(ind.vol_ratio, 2)}x` : '—',
          status: c3Ok ? 'green' : 'yellow',
          detail: c3Ok ? 'Volume acima da média — agressão institucional confirmada' : 'Aguardando spike de volume' },
        { id: 'atr_bbw', label: 'C4 ATR/BBW', sub: 'Alinhamento Regime',
          value: pcr != null ? `PCR ${fmt(pcr, 2)}` : '—',
          status: c4Ok ? 'green' : 'yellow',
          detail: c4Ok ? 'ATR/BBW alinhado com o regime de gama esperado' : 'ATR/BBW divergindo do regime' },
      ]
    },

    // ── T: Testes ─────────────────────────────────────────────────────────────
    T000: (_ind) => [
      { id: 'test_ok', label: 'C1 Pipeline', sub: 'Teste ativo',
        value: 'OK', status: 'green',
        detail: 'Sinal de diagnóstico ativo — valida o pipeline completo' },
    ],

    default: wrap((ind) => {
      if (!ind) return []
      return Object.keys(ind).filter(k => typeof ind[k] === 'number' && !['close','high','low','open','volume'].includes(k)).slice(0, 4).map((k, i) => ({ id: `gen_${k}`, label: `IND ${i+1}`, sub: k.toUpperCase(), value: fmt(ind[k], 2), status: 'grey', detail: 'Auto-detectado' }))
    }),
  }
}

function makeExitCriteriaMap(t, bot) {
  const rt = bot?.runtime || {}
  const dir = rt.direction || 0
  const tp  = rt.tp1_price || rt.tp_price
  const sl  = rt.sl_price
  const tp1Done = rt.tp1_done || false

  const common = (ind) => {
    const list = []
    // Mostra status do Dynamic Shadow Trigger
    if (tp1Done) {
      list.push({ id: 'shadow', label: 'Sombra Ativa', sub: 'Trailing Stop', value: 'ON', status: 'green', detail: 'Stop dinâmico protegendo lucros' })
    }
    if (tp && ind?.close) {
      const dist = Math.abs(ind.close - tp) / ind.close
      const isNear = dist < 0.005
      list.push({ id: 'exit_tp', label: t('sc.exit_tp'), sub: `$${fmt(tp, 2)}`, value: `${fmt(dist * 100, 1)}% dist`, status: isNear ? 'yellow' : 'grey', detail: isNear ? 'Perto do alvo!' : 'Aguardando alvo' })
    }
    if (sl && ind?.close) {
      const dist = Math.abs(ind.close - sl) / ind.close
      const isNear = dist < 0.01
      list.push({ id: 'exit_sl', label: t('sc.exit_sl'), sub: `$${fmt(sl, 2)}`, value: `${fmt(dist * 100, 1)}% dist`, status: isNear ? 'red' : 'grey', detail: isNear ? 'Perto do stop!' : 'Stop protegido' })
    }
    return list
  }

  const wrapExit = (stratFn) => (ind) => {
    const base = common(ind)
    const specific = stratFn ? stratFn(ind) : []
    return [...base, ...specific]
  }

  // Critério de reversão genérico: sinal oposto à posição atual
  const reversalCriterion = (ind) => {
    const signal = ind?.signal || 'hold'
    const isOpposite = (dir === 1 && signal === 'sell') || (dir === -1 && signal === 'buy')
    return [{ id: 'exit_rev', label: t('sc.exit_rev'), sub: 'Inversão', value: isOpposite ? 'Sinal!' : 'OK', status: isOpposite ? 'yellow' : 'green', detail: isOpposite ? 'Tendência virou — considere saída' : 'Tendência alinhada' }]
  }

  return {
    TF001: wrapExit(reversalCriterion),
    TF002: wrapExit(reversalCriterion),
    TF013: wrapExit(reversalCriterion),
    TF003: wrapExit(reversalCriterion),
    TF004: wrapExit(reversalCriterion),
    TF005: wrapExit(reversalCriterion),
    TF011: wrapExit(reversalCriterion),
    PA002: wrapExit(reversalCriterion),
    TF007: wrapExit(reversalCriterion),
    MR001: wrapExit(reversalCriterion),
    TF008: wrapExit(reversalCriterion),
    PA001: wrapExit(reversalCriterion),
    PA003: wrapExit(reversalCriterion),
    SC001: wrapExit(reversalCriterion),
    TF010: wrapExit(reversalCriterion),
    PA004: wrapExit(reversalCriterion),
    MR002: wrapExit(reversalCriterion),
    MR005: wrapExit(reversalCriterion),
    PA005: wrapExit(reversalCriterion),
    TF012: wrapExit(reversalCriterion),
    MR003: wrapExit(reversalCriterion),
    MR004: wrapExit(reversalCriterion),
    PA006: wrapExit(reversalCriterion),
    TF009: wrapExit(reversalCriterion),
    TF014: wrapExit(reversalCriterion),
    PA007: wrapExit(reversalCriterion),
    PA008: wrapExit(reversalCriterion),
    RG003: wrapExit(reversalCriterion),
    SC002: wrapExit(reversalCriterion),
    RG002: wrapExit(reversalCriterion),
    SC003: wrapExit(reversalCriterion),
    IF001: wrapExit(reversalCriterion),
    IF003: wrapExit(reversalCriterion),
    NW001: wrapExit(reversalCriterion),
    RG001: wrapExit((ind) => {
      const signal = ind?.signal || 0
      const regime = ind?.current_regime  // 0=Bear, 1=Side, 2=Bull
      const isReversal = signal < -0.10 || regime === 0
      const regimeName = regime === 0 ? 'Bear' : regime === 1 ? 'Sideways' : regime === 2 ? 'Bull' : '—'
      return [
        { id: 'exit_markov', label: t('sc.exit_markov'), sub: 'P(Bull)−P(Bear)', value: fmtS(signal, 3), status: isReversal ? 'red' : signal > 0 ? 'green' : 'yellow', detail: isReversal ? t('sc.exit_markov_reversal') : signal > 0 ? t('sc.exit_markov_bull') : t('sc.exit_markov_neutral') },
        { id: 'exit_trigger', label: t('sc.exit_trigger'), sub: t('sc.exit_trigger_sub'), value: isReversal ? t('sc.exit_trigger_fired') : t('sc.exit_trigger_pending'), status: isReversal ? 'green' : 'grey', detail: isReversal ? t('sc.exit_trigger_detail_fired') : `${t('sc.exit_trigger_detail_regime')} ${regimeName} · ${t('sc.exit_trigger_detail_signal')} ${fmtS(signal, 3)}` },
      ]
    }),
    default: wrapExit(() => [])
  }
}

export function getCriteriaCount(bot, indicators, mode = 'entry') {
  if (mode === 'order') {
    const order = bot?.runtime?.order_criteria
    const statuses = order?.items?.map(c => c.status) ?? []
    return {
      ok: order?.ok ?? statuses.filter(s => s === 'green').length,
      total: order?.total ?? statuses.filter(s => s !== 'none').length,
      hasRed: statuses.some(s => s === 'red'),
      hasYellow: statuses.some(s => s === 'yellow'),
      statuses
    }
  }
  const strategyId = String(bot?.strategy_id || '').trim().toUpperCase()
  const t = (k) => k
  const map = mode === 'exit' ? makeExitCriteriaMap(t, bot) : makeCriteriaMap(t, bot)
  const explicitFactory = map[strategyId]

  if (mode === 'entry' && !explicitFactory && hasOfficialCriteria(indicators)) {
    const criteria = officialEntryCriteria(indicators) || []
    const statuses = criteria.map(c => c.status)
    return {
      ok: statuses.filter(s => s === 'green').length,
      total: statuses.filter(s => s !== 'none').length,
      hasRed: statuses.some(s => s === 'red'),
      hasYellow: statuses.some(s => s === 'yellow'),
      statuses
    }
  }
  const factory = explicitFactory || map['default']
  if (!factory) return { ok: 0, total: 0, hasRed: false, hasYellow: false, statuses: [] }
  const criteria = factory(indicators, null, { current: null })
  const statuses = criteria.map(c => c.status)
  return { ok: statuses.filter(s => s === 'green').length, total: statuses.filter(s => s !== 'none').length, hasRed: statuses.some(s => s === 'red'), hasYellow: statuses.some(s => s === 'yellow'), statuses }
}

export default function StrategyChecklist({ bot, indicators, mode = 'entry', showLabels = true }) {
  const strategyId = String(bot?.strategy_id || '').trim().toUpperCase()
  const { t } = useLanguage()
  const isExit = mode === 'exit'
  const isOrder = mode === 'order'
  const prevRef    = useRef(indicators)
  const crossedRef = useRef(null)
  const timerRef   = useRef(null)

  useEffect(() => {
    if (!emasOk(indicators) || !emasOk(prevRef.current)) { prevRef.current = indicators; return }
    const gap = indicators.ema_fast - indicators.ema_slow
    const prevGap = prevRef.current.ema_fast - prevRef.current.ema_slow
    if (prevGap !== 0 && Math.sign(gap) !== Math.sign(prevGap)) {
      crossedRef.current = gap > 0 ? 'sc.ema_cross_bull' : 'sc.ema_cross_bear'
      clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => { crossedRef.current = null }, 45 * 60 * 1000)
    }
    prevRef.current = indicators
  }, [indicators])

  const map = isExit ? makeExitCriteriaMap(t, bot) : makeCriteriaMap(t, bot)
  const explicitFactory = map[strategyId]
  const factory = explicitFactory || map['default']
  if (!isOrder && !factory) return null
  const criteria = isOrder
    ? (bot?.runtime?.order_criteria?.items ?? [])
    : (!isExit && !explicitFactory && hasOfficialCriteria(indicators))
    ? (officialEntryCriteria(indicators) ?? [])
    : factory(indicators, prevRef.current, crossedRef)
  const orderState = bot?.runtime?.order_criteria
  const nGreen = criteria.filter(c => c.status === 'green').length
  const hasRed = criteria.some(c => c.status === 'red')
  const hasYellow = criteria.some(c => c.status === 'yellow')
  const total = criteria.filter(c => c.status !== 'none').length

  const hasData = isOrder ? criteria.length > 0 : !!indicators
  const allOk = total > 0 && nGreen === total && !hasRed && !hasYellow
  const badge = !hasData
    ? { text: t('sc.waiting'), cls: 'text-muted' }
    : isOrder && orderState?.blocker?.id === 'signal'
    ? { text: 'Aguardando sinal', cls: 'text-yellow-400' }
    : hasRed
    ? { text: t('sc.blocked'), cls: 'text-bear' }
    : allOk
    ? { text: t('sc.all_ok'), cls: 'text-bull' }
    : { text: t('sc.partial', { n: nGreen, total }), cls: 'text-yellow-400' }
  const title = isOrder ? t('sc.order_title') : isExit ? t('sc.exit_title') : t('sc.title')

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-[10px] font-bold text-muted uppercase tracking-[0.2em]">{title}</h3>
        <span className={`text-[10px] font-bold ${badge.cls}`}>{badge.text}</span>
      </div>
      <div className="flex gap-1.5 h-2">
        {criteria.map((c, i) => <div key={i} className={`flex-1 rounded-full ${STATUS[c.status]?.dot || 'bg-border'} shadow-sm`} title={c.label} />)}
      </div>
      {showLabels && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1">
          {criteria.map((c, i) => {
            const st = STATUS[c.status] || STATUS.none
            return (
              <div key={i} className={`flex flex-col p-2 rounded-lg border ${st.border} ${st.bg}`}>
                <div className="flex items-center gap-1.5 mb-1">
                  <div className={`w-1.5 h-1.5 rounded-full ${st.dot}`} />
                  <span className="text-[9px] font-bold text-white/50 uppercase tracking-wider truncate">{c.label}</span>
                </div>
                <div className={`text-[11px] font-mono font-bold ${st.val} truncate`}>{c.value}</div>
                <div className="text-[9px] text-muted/60 truncate mt-0.5">{c.detail}</div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
