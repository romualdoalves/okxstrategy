import React, { useMemo } from 'react'
import { useLanguage } from '../i18n/LanguageContext'

const ASSET_META = {
  'BTC-USDT': { label: 'BTC', color: '#f7931a' },
  'ETH-USDT': { label: 'ETH', color: '#627eea' },
  'SOL-USDT': { label: 'SOL', color: '#9945ff' },
  'XRP-USDT': { label: 'XRP', color: '#00aae4' },
  'BNB-USDT': { label: 'BNB', color: '#f3ba2f' },
}

const COMMUNITY_COLORS = [
  '#2962ff', '#26a69a', '#ff9800', '#9c27b0', '#ef5350',
]

// Regime visual meta (cores são invariantes de idioma)
const REGIME_STYLE = {
  trending:   { color: '#26a69a', bg: 'bg-bull/15',         text: 'text-bull'          },
  lagging:    { color: '#ff9800', bg: 'bg-yellow-500/15',   text: 'text-yellow-400'    },
  neutral:    { color: '#787b86', bg: 'bg-border/50',       text: 'text-muted'         },
  transition: { color: '#9c27b0', bg: 'bg-purple-500/15',   text: 'text-purple-400'    },
  chaos:      { color: '#ef5350', bg: 'bg-bear/15',         text: 'text-bear'          },
}

const CX = 160, CY = 140, R = 105
const ASSET_ORDER   = Object.keys(ASSET_META)
const NODE_POSITIONS = ASSET_ORDER.reduce((acc, id, i) => {
  const angle = -Math.PI / 2 + (2 * Math.PI / 5) * i
  acc[id] = { x: CX + R * Math.cos(angle), y: CY + R * Math.sin(angle) }
  return acc
}, {})

function MetricRow({ label, value, color }) {
  return (
    <div className="flex items-center justify-between text-xs py-0.5">
      <span className="text-muted">{label}</span>
      <span className={`font-mono font-medium ${color ?? 'text-white'}`}>{value}</span>
    </div>
  )
}

export default function GraphView({ graphState }) {
  const { t } = useLanguage()

  if (!graphState || !graphState.regime) {
    return (
      <div className="flex items-center justify-center h-48 text-muted text-sm">
        {t('gv.waiting')}
      </div>
    )
  }

  const { regime, metrics, nodes, edges, cluster_members, sentiment } = graphState

  const nodeMap     = useMemo(() =>
    Object.fromEntries((nodes ?? []).map(n => [n.id, n])), [nodes])
  const regimeStyle = REGIME_STYLE[regime.name] ?? REGIME_STYLE.neutral
  const regimeLabel = t(`gv.regime.${regime.name}`) || regime.name

  const barsText = regime.bars_in_regime === 1
    ? t('gv.bar',  { n: regime.bars_in_regime })
    : t('gv.bars', { n: regime.bars_in_regime })

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

      {/* SVG */}
      <div className="flex flex-col items-center">
        <p className="text-xs text-muted mb-2 self-start">{t('gv.title')}</p>
        <svg viewBox="0 0 320 300" className="w-full max-w-xs">

          {(edges ?? []).map((e, i) => {
            const src = NODE_POSITIONS[e.source]
            const tgt = NODE_POSITIONS[e.target]
            if (!src || !tgt) return null
            const opacity = 0.15 + e.weight * 0.75
            const width   = 1 + e.weight * 4
            const isPos   = e.raw >= 0
            return (
              <g key={i}>
                <line
                  x1={src.x} y1={src.y} x2={tgt.x} y2={tgt.y}
                  stroke={isPos ? '#26a69a' : '#ef5350'}
                  strokeWidth={width} strokeOpacity={opacity}
                />
                <text
                  x={(src.x + tgt.x) / 2} y={(src.y + tgt.y) / 2 - 3}
                  textAnchor="middle" fontSize="8" fill="#787b86" opacity={0.9}
                >
                  {e.raw > 0 ? '+' : ''}{e.raw.toFixed(2)}
                </text>
              </g>
            )
          })}

          {ASSET_ORDER.map(id => {
            const pos  = NODE_POSITIONS[id]
            const node = nodeMap[id]
            const meta = ASSET_META[id]
            if (!pos) return null
            const inGraph    = !!node
            const cluster    = node?.cluster ?? 0
            const centrality = node?.centrality ?? 0
            const isBTC      = id === 'BTC-USDT'
            const nodeR      = inGraph ? 14 + centrality * 18 : 10
            const borderColor = inGraph ? (COMMUNITY_COLORS[cluster] ?? '#787b86') : '#2a2e3d'
            return (
              <g key={id} transform={`translate(${pos.x},${pos.y})`}>
                {isBTC && inGraph && (
                  <circle r={nodeR + 6} fill="none"
                    stroke={regimeStyle.color} strokeWidth={1.5}
                    strokeOpacity={0.4} strokeDasharray="4 2" />
                )}
                <circle r={nodeR}
                  fill={inGraph ? meta.color + '22' : '#1e2130'}
                  stroke={borderColor}
                  strokeWidth={inGraph ? 2 : 1}
                  strokeOpacity={inGraph ? 1 : 0.3}
                />
                <text textAnchor="middle" dominantBaseline="central"
                  fontSize={isBTC ? 10 : 9}
                  fontWeight={isBTC ? 'bold' : 'normal'}
                  fill={inGraph ? meta.color : '#787b86'}>
                  {meta.label}
                </text>
                {inGraph && (
                  <text y={nodeR + 10} textAnchor="middle" fontSize="7" fill="#787b86">
                    {centrality.toFixed(2)}
                  </text>
                )}
              </g>
            )
          })}

          {Array.from(new Set((nodes ?? []).map(n => n.cluster))).map((c, i) => (
            <g key={c} transform={`translate(${8 + i * 60}, 288)`}>
              <circle r={4} fill={COMMUNITY_COLORS[c] ?? '#787b86'} />
              <text x={8} dominantBaseline="central" fontSize="7" fill="#787b86">
                {t('gv.cluster', { n: c })}
              </text>
            </g>
          ))}
        </svg>

        <div className="flex items-center gap-4 mt-1 text-xs text-muted">
          <span className="flex items-center gap-1">
            <span className="w-4 h-0.5 bg-bull inline-block" /> {t('gv.positive')}
          </span>
          <span className="flex items-center gap-1">
            <span className="w-4 h-0.5 bg-bear inline-block" /> {t('gv.inverse')}
          </span>
        </div>
      </div>

      {/* Métricas */}
      <div className="space-y-4">

        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${regimeStyle.bg} ${regimeStyle.text}`}>
              {regimeLabel}
            </span>
            <span className="text-xs text-muted">{barsText}</span>
          </div>
          <span className="text-xs font-mono" style={{ color: regimeStyle.color }}>
            {t('gv.conf', { pct: (regime.confidence * 100).toFixed(0) })}
          </span>
        </div>

        <div className="w-full h-1.5 bg-border rounded-full overflow-hidden">
          <div className="h-full rounded-full transition-all duration-700"
            style={{ width: `${regime.confidence * 100}%`, backgroundColor: regimeStyle.color }} />
        </div>

        <div className="space-y-1 border-t border-border pt-3">
          <MetricRow label={t('gv.btc_centrality')}
            value={metrics?.centrality_btc?.toFixed(4) ?? '—'}
            color={metrics?.centrality_btc > 0.3 ? 'text-bull' : 'text-muted'} />
          <MetricRow label={t('gv.eth_centrality')}
            value={metrics?.centrality_eth?.toFixed(4) ?? '—'}
            color={metrics?.eth_leads ? 'text-yellow-400' : 'text-white'} />
          <MetricRow label={t('gv.density')}
            value={metrics?.graph_density?.toFixed(4) ?? '—'}
            color={metrics?.graph_density > 0.5 ? 'text-bull'
              : metrics?.graph_density < 0.25 ? 'text-bear' : 'text-white'} />
          <MetricRow label={t('gv.communities')} value={metrics?.n_communities ?? '—'} />
          <MetricRow label={t('gv.active_edges')} value={metrics?.n_edges ?? '—'} />
          <MetricRow label={t('gv.btc_degree')}  value={metrics?.btc_degree ?? '—'} />
        </div>

        {metrics?.eth_leads && (
          <div className="text-xs bg-yellow-500/10 border border-yellow-500/20 rounded-lg px-3 py-2 text-yellow-400">
            {t('gv.eth_leads_alert')}
          </div>
        )}

        {(cluster_members ?? []).length > 0 && (
          <div className="text-xs text-muted">
            {t('gv.btc_cluster')}{' '}
            <span className="text-white">
              {cluster_members.map(m => ASSET_META[m]?.label ?? m).join(' · ')}
            </span>
          </div>
        )}

        {sentiment != null ? (
          <div className={`text-xs rounded-lg px-3 py-2 border ${
            sentiment > 0.3  ? 'bg-bull/10 border-bull/20 text-bull'
            : sentiment < -0.3 ? 'bg-bear/10 border-bear/20 text-bear'
            : 'bg-border/50 border-border text-muted'
          }`}>
            {t('gv.fingpt')}{' '}
            <span className="font-mono font-semibold">
              {sentiment > 0 ? '+' : ''}{sentiment.toFixed(2)}
            </span>
            {' '}
            {sentiment > 0.6  ? t('gv.bullish_strong')
            : sentiment > 0.3  ? t('gv.bullish')
            : sentiment < -0.6 ? t('gv.bearish_strong')
            : sentiment < -0.3 ? t('gv.bearish')
            : t('gv.neutral_sent')}
          </div>
        ) : (
          <div className="text-xs text-muted/50 italic">
            {t('gv.fingpt_inactive')}
          </div>
        )}
      </div>
    </div>
  )
}
