// Renderiza a página "Meta 1% Bruto (24 h)" e a linha de meta do card (SSR) em vários estados: confere que não há erro de
// execução e que as explicações, o critério para sair da ilusão, os critérios e os botões aparecem (ou NÃO aparecem, quando
// não devem).
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
  target_pct: 1, net_equivalent_pct: 0.8, window_hours: 24, min_trades: 10, min_profit_factor: 1.2, min_chance_pct: 20, top_n: 3,
  min_independent_windows: 10, fee_rate_pct: 0.1, exit_slippage_pct: 0.05, exit_margin_pct: 0.05, strategies_considered: 44,
  history_cap: { '1m': 3000, '5m': 3000, '15m': 3000, '1h': 4400 }, timeframes: ['1m', '5m', '15m', '1h'],
}
const row = (over = {}) => ({
  strategy_id: 'TF001', strategy_name: 'TF001 - EMA Cross', symbol: 'SOL-USDT', timeframe: '15m', chance_pct: 31.4, chance_lb_pct: 17.2,
  gross_chance_pct: 44.0, windows_hit: 220, windows_illusory: 88, windows_stop_loss: 70, windows_expired: 150, windows_no_order: 173,
  stop_loss_pct: 10.0, expected_net_pct: 0.21, windows_total: 701, independent_windows: 30.1, trades_count: 42,
  total_profit: 18.5, profit_factor: 1.8, max_drawdown: 6.2, win_rate: 58, history_days: 31, qualifies: true, unmet: [],
  in_top5_now: true, ...over,
})
const BASE = {
  state: 'done', progress: { done: 132, total: 132, current: null }, criteria: CRITERIA, evaluated: 130, qualified: 0, failed: 2,
  assets: { top5: [{ symbol: 'BTC-USDT', volume_usd_24h: 1.5e9 }, { symbol: 'SOL-USDT', volume_usd_24h: 2.5e8 }], used: ['BTC-USDT'], free: ['SOL-USDT'] },
  top: [], closest: [], age_seconds: 120, note: null, error: null,
}
const STATES = {
  idle: { ...BASE, state: 'idle', progress: { done: 0, total: 0, current: null }, assets: { top5: [], used: [], free: [] } },
  running: { ...BASE, state: 'running', progress: { done: 40, total: 132, current: 'TF001 · SOL-USDT' } },
  withTop: { ...BASE, qualified: 2, top: [row(), row({ strategy_id: 'MR002', strategy_name: 'MR002 - Reversão', chance_pct: 25, chance_lb_pct: 12, windows_illusory: 0 })] },
  stale: { ...BASE, qualified: 1, top: [row({ in_top5_now: false })] },
  none: { ...BASE, closest: [row({ qualifies: false, chance_pct: 3.7, chance_lb_pct: 0.6, total_profit: -29.4, profit_factor: 0.26, expected_net_pct: -0.31, stop_loss_pct: 12.0,
    unmet: ['Lucro líquido total negativo no período (US$ -29.37).', 'Chance REAL de +1% bruto em 24 h (com líquido positivo) de 3.7% (mínimo 20%).'] })] },
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
  check('renderiza sem exceção e mostra o título da meta BRUTA', idle.t.includes('Meta 1% Bruto (24 h)'))
  check('avisa que a meta é agressiva e BRUTA e mostra a conta composta', idle.t.includes('Leia antes') && idle.t.includes('é BRUTA') && /3\.678/.test(idle.t.replace(/\s/g, '')))
  check('define bruto x líquido com o equivalente líquido do 1% bruto', idle.t.includes('antes das taxas') && idle.t.includes('+0,8% líquido'))
  check('explica a meta em 24 horas corridas', idle.t.includes('US$ 1,00') && idle.t.includes('0.1% por lado') && idle.t.includes('24 horas corridas'))
  check('explica o top 5 dinâmico e a conferência na criação', idle.t.includes('Top 5') && idle.t.includes('recusa um ativo que saiu do ranking'))
  check('explica a métrica (janelas móveis, chance real e piso de 95%)', idle.t.includes('janela de 24 h começa a cada hora') && idle.t.includes('Chance real') && idle.t.includes('piso'))

  console.log('Página: critério para sair da ilusão')
  check('tem a seção "Critério para sair da ilusão"', idle.t.includes('Critério para sair da ilusão'))
  check('regra 1: acerto REAL exige líquido positivo; ilusório fica fora da chance',
    idle.t.includes('só é REAL se') && idle.t.includes('líquido da sessão for positivo') && idle.t.includes('acerto ilusório') && idle.t.includes('não entra na chance'))
  check('regra 2: Top 3 só com resultado LÍQUIDO positivo no histórico',
    idle.t.includes('resultado LÍQUIDO positivo no histórico') && idle.t.includes('Profit Factor líquido')
    && idle.t.includes('resultado líquido médio por sessão de 24 h positivo'))
  check('regra 3: bruto e líquido lado a lado e aviso expresso de ilusão no card', idle.t.includes('lado a lado') && idle.t.includes('foi uma ilusão'))
  check('lista os critérios com os valores do servidor', idle.t.includes('10 operações') && idle.t.includes('1,2') && idle.t.includes('20%') && idle.t.includes('10 janelas de 24 h independentes'))
  check('explica as regras do bot (meta bruta, 24 h sem ordem, posição aberta, stop loss, trailing)',
    ['Para ao atingir a meta bruta', 'nenhuma ordem', 'não para por tempo', 'Saída com prejuízo', 'trailing stop continua valendo'].every(x => idle.t.includes(x)))
  check('o bot registra o resultado bruto E líquido ao parar', idle.t.includes('bruto e líquido'))
  check('botão de busca disponível e nenhum resultado antes da busca',
    idle.t.includes('Buscar candidatas agora') && !idle.t.includes('Top 2 candidatas') && !idle.t.includes('Mais próximas'))

  console.log('Página: buscando')
  const running = await page('running')
  check('mostra o progresso e o item atual', running.t.includes('40 de 132') && running.t.includes('TF001 · SOL-USDT') && running.t.includes('Buscando'))

  console.log('Página: com candidatas')
  const withTop = await page('withTop')
  check('mostra "Top 2 candidatas" e as duas estratégias', withTop.t.includes('Top 2 candidatas') && withTop.t.includes('TF001') && withTop.t.includes('MR002'))
  check('chance REAL em destaque, com piso e janelas', withTop.t.includes('Chance REAL de +1% bruto') && withTop.t.includes('31,4%') && withTop.t.includes('17,2%') && withTop.t.includes('220 das 701'))
  check('mostra bruto x real: chance bruta e acertos ilusórios', withTop.t.includes('Bruto x real') && withTop.t.includes('44,0%') && withTop.t.includes('88') && withTop.t.includes('acertos ilusórios'))
  check('sem ilusão, diz que nenhum acerto foi ilusório', withTop.t.includes('Nenhum acerto foi ilusório'))
  check('mostra como terminam as sessões (meta, ilusão, stop loss, expirou, sem ordem) e o líquido médio',
    withTop.t.includes('Como terminam as sessões de 24 h') && withTop.t.includes('Stop loss') && withTop.t.includes('Sem ordem em 24 h')
    && withTop.t.includes('Líquido médio por sessão') && withTop.t.includes('+0,21%') && withTop.t.includes('inclusive as que terminam em stop loss'))
  check('tem o botão de criar bot com a meta bruta', withTop.t.includes('Criar bot com meta de 1% bruto em 24 h'))
  check('antes de criar não diz que o bot foi criado', !withTop.t.includes('Bot criado'))
  check('marca "no top 5 agora"', withTop.t.includes('no top 5 agora'))

  console.log('Página: ativo que saiu do top 5')
  const stale = await page('stale')
  check('avisa e desabilita o botão', stale.t.includes('saiu do top 5') && /<button[^>]*disabled[^>]*>[^<]*(<svg.*?<\/svg>)?\s*Criar bot/.test(stale.h))

  console.log('Página: nenhuma atende')
  const none = await page('none')
  check('diz com franqueza que nenhuma atende', none.t.includes('Nenhuma estratégia atende aos critérios agora'))
  check('mostra as mais próximas com os motivos', none.t.includes('Mais próximas') && none.t.includes('Lucro líquido total negativo') && none.t.includes('3,7%'))
  check('na que não atende, o líquido médio por sessão aparece negativo', none.t.includes('-0,31%'))
  check('NÃO oferece botão de criar para quem não atende', !none.t.includes('Criar bot com meta'))

  console.log('Página: erro e sem ativo livre')
  check('mostra o erro da busca', (await page('error')).t.includes('A busca falhou'))
  check('explica quando não há ativo livre', (await page('allUsed')).t.includes('não há ativo livre'))

  console.log('Card: linha de meta (bruto como régua, líquido ao lado)')
  const goal = async g => { const h = await render(m => React.createElement(m.Goal, { goal: g })); return text(h) }
  const g0 = { enabled: true, target_pct: 1, net_equivalent_pct: 0.8, window_hours: 24, started_at: new Date(Date.now() - 6 * 3600e3).toISOString(),
    deadline: new Date(Date.now() + 18 * 3600e3).toISOString(), session_gross_pct: 0.4, session_net_pct: 0.2, open_gross_pct: 0, open_net_pct: 0,
    in_position: false, done: false, outcome: null, illusory: false }
  const t1 = await goal(g0)
  check('mostra a meta bruta, o progresso bruto, o líquido ao lado e o tempo restante',
    t1.includes('Meta 1% bruto') && t1.includes('+0.40% / 1%') && t1.includes('líquido') && t1.includes('+0.20%') && /restam 1[78] h/.test(t1))
  check('lembra que +1% bruto numa operação deixa ≈ +0,80% líquido', t1.includes('≈ +0.80% líquido'))
  const t2 = await goal({ ...g0, done: true, outcome: 'reached', outcome_label: 'Meta bruta atingida', result_gross_pct: 1.06, result_net_pct: 0.85, session_gross_pct: 1.06, session_net_pct: 0.85 })
  check('ao encerrar mostra "Meta bruta atingida" com os dois resultados', t2.includes('Meta bruta atingida') && t2.includes('+1.06%') && t2.includes('+0.85%') && !t2.includes('Ilusão'))
  const t5 = await goal({ ...g0, done: true, outcome: 'reached', outcome_label: 'Meta bruta atingida', illusory: true, result_gross_pct: 1.08, result_net_pct: -0.12 })
  check('meta bruta batida com líquido negativo avisa expressamente a ilusão', t5.includes('Ilusão') && t5.includes('taxas consumiram o ganho') && t5.includes('-0.12%'))
  const t3 = await goal({ ...g0, deadline: new Date(Date.now() - 3600e3).toISOString(), in_position: true, open_gross_pct: 0.2, open_net_pct: 0.0 })
  check('prazo esgotado com posição aberta explica que só sai por meta ou stop loss', t3.includes('só sai por meta ou stop loss'))
  const t4 = await goal({ ...g0, started_at: null, deadline: null, session_gross_pct: null, session_net_pct: null })
  check('antes de iniciar explica que a janela começa ao Iniciar', t4.includes('começa ao clicar em Iniciar'))
} catch (e) {
  failures++
  console.log('  FALHA exceção ao renderizar:', e.message)
} finally {
  fs.rmSync(tmpEntry, { force: true })
  fs.rmSync(outFile, { force: true })
}
console.log(failures ? `\n${failures} falha(s).` : '\nTodos os estados da Meta 1% Bruto (24 h) renderizam corretamente.')
process.exit(failures ? 1 : 0)
