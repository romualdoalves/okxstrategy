// Renderiza a página "Meta 1% Hoje!" e a linha de meta do card (SSR) em vários estados: confere que não há erro de execução
// e que as explicações, os critérios e os botões aparecem (ou NÃO aparecem, quando não devem).
// Uso: node scripts/render-goal-states.mjs
import fs from 'node:fs'
import path from 'node:path'
import { build } from 'esbuild'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const root = process.cwd()
const outFile = path.join(root, '.tmp_goal_state.bundle.mjs')
const tmpEntry = path.join(root, 'src/pages/.tmp_goal_entry.jsx')

const CRITERIA = {
  target_pct: 1, window_hours: 24, min_trades: 10, min_profit_factor: 1.2, min_chance_pct: 20, top_n: 3,
  min_independent_windows: 10, fee_rate_pct: 0.1, exit_slippage_pct: 0.05, exit_margin_pct: 0.05, strategies_considered: 44,
  history_cap: { '1m': 3000, '5m': 3000, '15m': 3000, '1h': 4400 }, timeframes: ['1m', '5m', '15m', '1h'],
}
const row = (over = {}) => ({
  strategy_id: 'TF001', strategy_name: 'TF001 - EMA Cross', symbol: 'SOL-USDT', timeframe: '15m', chance_pct: 31.4, chance_lb_pct: 17.2,
  windows_hit: 220, windows_total: 701, independent_windows: 30.1, trades_count: 42, total_profit: 18.5, profit_factor: 1.8,
  max_drawdown: 6.2, win_rate: 58, history_days: 31, qualifies: true, unmet: [], in_top5_now: true, ...over,
})
const BASE = {
  state: 'done', progress: { done: 132, total: 132, current: null }, criteria: CRITERIA, evaluated: 130, qualified: 0, failed: 2,
  assets: { top5: [{ symbol: 'BTC-USDT', volume_usd_24h: 1.5e9 }, { symbol: 'SOL-USDT', volume_usd_24h: 2.5e8 }], used: ['BTC-USDT'], free: ['SOL-USDT'] },
  top: [], closest: [], age_seconds: 120, note: null, error: null,
}
const STATES = {
  idle: { ...BASE, state: 'idle', progress: { done: 0, total: 0, current: null }, assets: { top5: [], used: [], free: [] } },
  running: { ...BASE, state: 'running', progress: { done: 40, total: 132, current: 'TF001 · SOL-USDT' } },
  withTop: { ...BASE, qualified: 2, top: [row(), row({ strategy_id: 'MR002', strategy_name: 'MR002 - Reversão', chance_pct: 25, chance_lb_pct: 12 })] },
  stale: { ...BASE, qualified: 1, top: [row({ in_top5_now: false })] },
  none: { ...BASE, closest: [row({ qualifies: false, chance_pct: 3.7, chance_lb_pct: 0.6, total_profit: -29.4, profit_factor: 0.26,
    unmet: ['Lucro líquido total negativo no período (US$ -29.37).', 'Chance de bater a meta em 24 h de 3.7% (mínimo 20%).'] })] },
  error: { ...BASE, state: 'error', error: 'Não foi possível obter o ranking de ativos da OKX.' },
  allUsed: { ...BASE, note: 'Todos os ativos do top 5 já têm bot: não há ativo livre para uma nova meta.' },
}

const stubs = {
  'react-router-dom': 'export const useNavigate = () => () => {}',
  '@tanstack/react-query': `
    export const useQuery = () => ({ data: globalThis.__GOAL })
    export const useQueryClient = () => ({ invalidateQueries: async () => {} })`,
  '../api': 'export const getGoalStatus = () => {}; export const startGoalScan = () => {}; export const createBot = () => {}',
}

async function render(element) {
  fs.writeFileSync(tmpEntry, `import React from 'react'
import MetaHoje from './MetaHoje'
import GoalLine from '../components/GoalLine'
export const Page = MetaHoje
export const Goal = GoalLine
`)
  await build({
    entryPoints: [tmpEntry], bundle: true, platform: 'node', format: 'esm', outfile: outFile, write: true,
    external: ['react', 'react/jsx-runtime', 'react-dom', 'lucide-react'], jsx: 'automatic', loader: { '.jsx': 'jsx' },
    plugins: [{
      name: 'stubs',
      setup(b) {
        b.onResolve({ filter: /^(react-router-dom|@tanstack\/react-query)$/ }, a => ({ path: a.path, namespace: 'stub' }))
        b.onResolve({ filter: /\.\.\/api$/ }, a => ({ path: '../api', namespace: 'stub' }))
        b.onLoad({ filter: /.*/, namespace: 'stub' }, a => ({ contents: stubs[a.path], loader: 'js' }))
      },
    }],
  })
  const mod = await import('file:///' + outFile.split(String.fromCharCode(92)).join('/') + '?v=' + Math.random())
  return renderToStaticMarkup(element(mod))
}

const text = h => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
let failures = 0
const check = (name, cond) => { console.log((cond ? '  ok   ' : '  FALHA') + ' ' + name); if (!cond) failures++ }
const page = async key => { globalThis.__GOAL = STATES[key]; const h = await render(m => React.createElement(m.Page)); return { h, t: text(h) } }

try {
  console.log('Página: explicações sempre visíveis')
  const idle = await page('idle')
  check('renderiza sem exceção e mostra o título', idle.t.includes('Meta 1% Hoje!'))
  check('avisa que a meta é agressiva e mostra a conta composta', idle.t.includes('Leia antes') && /3\.678/.test(idle.t.replace(/\s/g, '')) )
  check('explica a meta líquida, as taxas e as 24 h corridas', idle.t.includes('US$ 1,00') && idle.t.includes('0.1% por lado') && idle.t.includes('24 horas corridas'))
  check('explica o top 5 dinâmico e a conferência na criação', idle.t.includes('Top 5') && idle.t.includes('recusa um ativo que saiu do ranking'))
  check('explica a métrica (janelas móveis, chance e piso de 95%)', idle.t.includes('janela de 24 h começa a cada hora') && idle.t.includes('piso'))
  check('lista os critérios com os valores do servidor', idle.t.includes('10 operações') && idle.t.includes('1,2') && idle.t.includes('20%'))
  check('explica o histórico mínimo', idle.t.includes('histórico mínimo') && idle.t.includes('10 janelas de 24 h independentes'))
  check('explica as regras do bot (meta, 24 h sem ordem, posição aberta, stop loss, trailing)',
    ['Para ao atingir a meta', 'nenhuma ordem', 'não para por tempo', 'Saída com prejuízo', 'trailing stop continua valendo'].every(x => idle.t.includes(x)))
  check('botão de busca disponível', idle.t.includes('Buscar candidatas agora'))
  check('sem resultados antes da busca', !idle.t.includes('Top 2 candidatas') && !idle.t.includes('Nenhuma estratégia atende') && !idle.t.includes('Mais próximas'))

  console.log('Página: buscando')
  const running = await page('running')
  check('mostra o progresso e o item atual', running.t.includes('40 de 132') && running.t.includes('TF001 · SOL-USDT') && running.t.includes('Buscando'))

  console.log('Página: com candidatas')
  const withTop = await page('withTop')
  check('mostra "Top 2 candidatas" e as duas estratégias', withTop.t.includes('Top 2 candidatas') && withTop.t.includes('TF001') && withTop.t.includes('MR002'))
  check('mostra chance, piso e janelas', withTop.t.includes('31,4%') && withTop.t.includes('17,2%') && withTop.t.includes('220 das 701'))
  check('tem o botão de criar bot com a meta', withTop.t.includes('Criar bot com meta de 1% em 24 h'))
  check('antes de criar não diz que o bot foi criado', !withTop.t.includes('Bot criado'))
  check('marca "no top 5 agora"', withTop.t.includes('no top 5 agora'))

  console.log('Página: ativo que saiu do top 5')
  const stale = await page('stale')
  check('avisa e desabilita o botão', stale.t.includes('saiu do top 5') && /<button[^>]*disabled[^>]*>[^<]*(<svg.*?<\/svg>)?\s*Criar bot/.test(stale.h))

  console.log('Página: nenhuma atende')
  const none = await page('none')
  check('diz com franqueza que nenhuma atende', none.t.includes('Nenhuma estratégia atende aos critérios agora'))
  check('mostra as mais próximas com os motivos', none.t.includes('Mais próximas') && none.t.includes('Lucro líquido total negativo') && none.t.includes('3,7%'))
  check('NÃO oferece botão de criar para quem não atende', !none.t.includes('Criar bot com meta'))

  console.log('Página: erro e sem ativo livre')
  check('mostra o erro da busca', (await page('error')).t.includes('A busca falhou'))
  check('explica quando não há ativo livre', (await page('allUsed')).t.includes('não há ativo livre'))

  console.log('Card: linha de meta')
  const goal = async g => { const h = await render(m => React.createElement(m.Goal, { goal: g })); return text(h) }
  const g0 = { enabled: true, target_pct: 1, window_hours: 24, started_at: new Date(Date.now() - 6 * 3600e3).toISOString(), deadline: new Date(Date.now() + 18 * 3600e3).toISOString(),
    session_net_pct: 0.4, realized_net_pct: 0.4, open_net_pct: 0, in_position: false, done: false, outcome: null }
  const t1 = await goal(g0)
  check('mostra o alvo, o progresso e o tempo restante', t1.includes('Meta 1% líquido') && t1.includes('+0.40% / 1%') && /restam 1[78] h/.test(t1))
  const t2 = await goal({ ...g0, done: true, outcome: 'reached', outcome_label: 'Meta atingida', session_net_pct: 1.1 })
  check('mostra "Meta atingida" ao encerrar', t2.includes('Meta atingida'))
  const t3 = await goal({ ...g0, deadline: new Date(Date.now() - 3600e3).toISOString(), in_position: true, open_net_pct: 0.2 })
  check('prazo esgotado com posição aberta explica que só sai por meta ou stop loss', t3.includes('só sai por meta ou stop loss'))
  const t4 = await goal({ ...g0, started_at: null, deadline: null, session_net_pct: null })
  check('antes de iniciar explica que a janela começa ao Iniciar', t4.includes('começa ao clicar em Iniciar'))
} catch (e) {
  failures++
  console.log('  FALHA exceção ao renderizar:', e.message)
} finally {
  fs.rmSync(tmpEntry, { force: true })
  fs.rmSync(outFile, { force: true })
}
console.log(failures ? `\n${failures} falha(s).` : '\nTodos os estados da Meta 1% Hoje! renderizam corretamente.')
process.exit(failures ? 1 : 0)
