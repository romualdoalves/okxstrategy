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
    if (!isSpot || !ind) return null
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


    TF005: wrap((ind) => [
      { id: 'trend', label: `C1 ${t('sc.dinapoli_trend_label')}`, sub: t('sc.dinapoli_trend_sub'), value: ind?.trend_ok === 1 ? 'Bullish' : ind?.trend_ok === -1 ? 'Bearish' : '-', status: ind?.trend_ok !== 0 ? 'green' : 'grey', detail: t('sc.dinapoli_trend_sub') },
      { id: 'setup', label: `C2 ${t('sc.dinapoli_setup_label')}`, sub: t('sc.dinapoli_setup_sub'), value: ind?.setup_formed === 1 ? 'Fundo Asc.' : ind?.setup_formed === -1 ? 'Topo Desc.' : '-', status: ind?.setup_formed !== 0 ? 'green' : 'grey', detail: t('sc.dinapoli_setup_sub') },
      { id: 'trigger', label: `C3 ${t('sc.dinapoli_trigger_label')}`, sub: t('sc.dinapoli_trigger_sub'), value: ind?.trigger_crossed !== 0 ? 'Rompido' : '-', status: ind?.trigger_crossed !== 0 ? 'green' : 'grey', detail: t('sc.dinapoli_trigger_sub') }
    ]),

    // ── A: Autorais ───────────────────────────────────────────────────────────


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
      { id: 'test', label: 'C3 Proximidade', sub: 'Reteste', value: `${fmt(ind?.dist_close_pct, 2)}%`, status: ind?.c3_near_ok ? 'green' : 'yellow', detail: 'Distância do fechamento à EMA semanal dentro do limite configurado (0 = sem limite)' }
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





    PA005: wrap((ind) => [
      { id: 'support', label: 'C1 Suporte', sub: 'Zona de Demanda', value: ind?.support_ok ? 'DETECTADA' : 'BUSCANDO', status: ind?.support_ok ? 'green' : 'yellow', detail: 'Identificação de fundo anterior' },
      { id: 'sweep', label: 'C2 Varredura', sub: 'Liquidity Sweep', value: ind?.sweep_ok ? 'OK' : 'AGUARDANDO', status: ind?.sweep_ok ? 'green' : 'yellow', detail: 'Violação e retorno ao range' },
      { id: 'choch', label: 'C3 CHoCH', sub: 'Confirmação', value: ind?.choch_ok ? 'QUEBRA' : 'ESTÁVEL', status: ind?.choch_ok ? 'green' : 'yellow', detail: 'Quebra de estrutura de baixa' }
    ]),




    // ── M: Markov / Probabilísticas ──────────────────────────────────────────


    // ── I: Inteligência ───────────────────────────────────────────────────────
    



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
    TF002: wrapExit(reversalCriterion),
    TF013: wrapExit(reversalCriterion),
    TF003: wrapExit(reversalCriterion),
    TF005: wrapExit(reversalCriterion),
    TF007: wrapExit(reversalCriterion),
    MR001: wrapExit(reversalCriterion),
    TF008: wrapExit(reversalCriterion),
    TF010: wrapExit(reversalCriterion),
    PA004: wrapExit(reversalCriterion),
    PA005: wrapExit(reversalCriterion),
    TF009: wrapExit(reversalCriterion),
    TF014: wrapExit(reversalCriterion),
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
    : isOrder && orderState?.blocker?.status === 'grey'
    ? { text: 'Sinal não aplicável', cls: 'text-muted' }
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
