// Renderiza a tela do Scanner (SSR) em vários estados e confere se não há erro de execução (como
// "Cannot access 'X' before initialization") e se os textos/botões esperados aparecem.
// Uso: node scripts/render-scanner-states.mjs
import fs from 'node:fs'
import path from 'node:path'
import { build } from 'esbuild'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const root = process.cwd()
const pagePath = path.join(root, 'src/pages/BatchBacktest.jsx')
const tmpPage = path.join(root, 'src/pages/.tmp_scanner_state.jsx')
const outFile = path.join(root, '.tmp_scanner_state.bundle.mjs')

const STRATEGIES = ['TF001', 'TF002', 'TF003', 'MR001', 'MR002', 'MR003', 'MR004', 'MR005', 'PA001', 'PA005', 'IF001'].map(id => ({ id, name: id + ' nome' }))
const BOTS = [{ id: 186, symbol: 'DOT-USDT', strategy_id: 'TF001' }]
const TOP_ASSETS = { assets: ['BTC-USDT', 'ETH-USDT', 'SOL-USDT', 'HYPE-USDT', 'ZEC-USDT'].map(symbol => ({ symbol })) }

const stubs = {
  'react-router-dom': `export const useNavigate = () => () => {}`,
  '@tanstack/react-query': `
    const DATA = { strategies: globalThis.__STRATEGIES, bots: globalThis.__BOTS, 'top-assets': globalThis.__TOP_ASSETS }
    export const useQuery = ({ queryKey }) => ({ data: DATA[queryKey[0]], isLoading: false })
    export const useQueryClient = () => ({ invalidateQueries: () => {}, setQueryData: () => {} })`,
  '../api': `export const getStrategies = () => {}; export const getBots = () => {}; export const createBot = () => {}; export const getTopAssets = () => {}`,
  '../auth': `export const authFetch = () => {}`,
}

function render(category, symbol, { nonePicked = false } = {}) {
  let src = fs.readFileSync(pagePath, 'utf8')
    .replace("useState('TF')", `useState(${JSON.stringify(category)})`)
    .replace("const [symbol, setSymbol]     = useState('')", `const [symbol, setSymbol]     = useState(${JSON.stringify(symbol)})`)
  if (nonePicked) src = src.replace('useState(() => new Set(categoryStrategies.map(st => st.id)))', 'useState(() => new Set())')
  fs.writeFileSync(tmpPage, src)
  return build({
    entryPoints: [tmpPage], bundle: true, platform: 'node', format: 'esm', outfile: outFile, write: true,
    external: ['react', 'react/jsx-runtime', 'react-dom', 'lucide-react'], jsx: 'automatic', loader: { '.jsx': 'jsx' },
    plugins: [{
      name: 'stubs',
      setup(b) {
        b.onResolve({ filter: /^(react-router-dom|@tanstack\/react-query|\.\.\/api|\.\.\/auth)$/ }, a => ({ path: a.path, namespace: 'stub' }))
        b.onLoad({ filter: /.*/, namespace: 'stub' }, a => ({ contents: stubs[a.path], loader: 'js' }))
      },
    }],
  }).then(async () => {
    globalThis.__STRATEGIES = STRATEGIES
    globalThis.__BOTS = BOTS
    globalThis.__TOP_ASSETS = TOP_ASSETS
    const mod = await import('file:///' + outFile.split(String.fromCharCode(92)).join('/') + '?v=' + Math.random())
    return renderToStaticMarkup(React.createElement(mod.default))
  })
}

const text = h => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
let failures = 0
const check = (name, cond) => { console.log((cond ? '  ok   ' : '  FALHA') + ' ' + name); if (!cond) failures++ }

try {
  console.log('Estado 1: categoria MR + Todos os ativos (o caso que quebrou)')
  const h1 = await render('MR', '__ALL_ASSETS__'); const t1 = text(h1)
  check('renderiza sem exceção', true)
  check('mostra "Todos os ativos (N)"', /Todos os ativos \(\d+\)/.test(t1))
  check('mostra a estimativa de tempo', t1.includes('Estimativa grosseira'))
  check('botão de executar fala em "Todos os Ativos" com a contagem de estratégias', t1.includes('Executar 5 estratégia(s) em Todos os Ativos'))

  console.log('Estado 2: categoria MR + um ativo (fluxo normal)')
  const h2 = await render('MR', 'BTC-USDT'); const t2 = text(h2)
  check('botão de executar com a contagem de estratégias marcadas', t2.includes('Executar 5 estratégia(s) selecionada(s)'))
  check('sem estimativa nem aviso', !t2.includes('Estimativa grosseira') && !t2.includes('não é permitido'))
  check('lista as estratégias da categoria com checkbox', t2.includes('MR001') && t2.includes('MR005') && (h2.match(/type="checkbox"/g) || []).length >= 6)
  check('mostra "5 de 5 selecionadas" e os atalhos', t2.includes('5 de 5 selecionadas') && t2.includes('Selecionar todas') && t2.includes('Limpar'))

  console.log('Estado 3: o que foi removido não aparece')
  check('o título do bloco de ativos é "Top 5 Ativos"', t2.includes('Top 5 Ativos'))
  check('explica que são os 5 de maior volume em 24h', t2.includes('maior volume em 24h'))
  check('sem a categoria "Todas"', !t2.includes('Todas as categorias') && !/>\s*Todas\s*</.test(h2))
  check('sem a opção "Outro"', !t2.includes('Outro') && !h2.includes('placeholder='))
  check('mostra os 5 ativos do ranking numerados', ['#1', '#5', 'BTC-USDT', 'ZEC-USDT'].every(x => t2.includes(x)))

  console.log('Estado 4: nenhuma estratégia marcada')
  const h4 = await render('MR', 'BTC-USDT', { nonePicked: true }); const t4 = text(h4)
  check('mostra "0 de 5 selecionadas"', t4.includes('0 de 5 selecionadas'))
  check('botão de executar fica desabilitado', /<button[^>]*disabled[^>]*class="btn[^"]*"|<button[^>]*class="btn[^"]*"[^>]*disabled/.test(h4))

  console.log('Estado 5: tela inicial (TF, nenhum ativo)')
  const t5 = text(await render('TF', ''))
  check('renderiza a tela inicial', t5.includes('Scanner de Backtest'))
  check('TF mostra as 3 estratégias marcadas', t5.includes('3 de 3 selecionadas'))
} catch (e) {
  failures++
  console.log('  FALHA exceção ao renderizar:', e.message)
} finally {
  fs.rmSync(tmpPage, { force: true })
  fs.rmSync(outFile, { force: true })
}
console.log(failures ? `\n${failures} falha(s).` : '\nTodos os estados renderizam corretamente.')
process.exit(failures ? 1 : 0)
