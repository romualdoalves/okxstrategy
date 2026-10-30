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

function render(category, symbol) {
  const src = fs.readFileSync(pagePath, 'utf8')
    .replace("useState('TF')", `useState(${JSON.stringify(category)})`)
    .replace("const [symbol, setSymbol]     = useState('')", `const [symbol, setSymbol]     = useState(${JSON.stringify(symbol)})`)
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
  check('mostra "Todos os ativos (7)" (8 ativos menos o que já tem bot... DOT não está na lista: 8)', /Todos os ativos \(\d+\)/.test(t1))
  check('mostra a estimativa de tempo', t1.includes('Estimativa grosseira'))
  check('botão de executar fala em "Todos os Ativos"', t1.includes('Executar MR em Todos os Ativos'))
  check('sem mensagem de combinação proibida', !t1.includes('não é permitido'))
  check('botão "Todas" (categorias) fica desabilitado', /<button[^>]*disabled[^>]*>\s*Todas/.test(h1))

  console.log('Estado 2: Todas as categorias + Todos os ativos (proibido)')
  const h2 = await render('ALL', '__ALL_ASSETS__'); const t2 = text(h2)
  check('mostra o aviso de combinação não permitida', t2.includes('Todas as categorias + Todos os ativos não é permitido'))
  check('botão de executar fica desabilitado', /<button[^>]*disabled[^>]*class="btn[^"]*"|<button[^>]*class="btn[^"]*"[^>]*disabled/.test(h2))
  check('botão "Todos os ativos" fica desabilitado', /<button[^>]*disabled[^>]*>\s*Todos os ativos/.test(h2))

  console.log('Estado 3: categoria MR + um ativo (fluxo normal)')
  const h3 = await render('MR', 'BTC-USDT'); const t3 = text(h3)
  check('botão de executar normal', t3.includes('Executar Backtest da Categoria'))
  check('sem estimativa nem aviso', !t3.includes('Estimativa grosseira') && !t3.includes('não é permitido'))

  console.log('Estado 4: Todas as categorias + um ativo (fluxo normal)')
  const t4 = text(await render('ALL', 'ETH-USDT'))
  check('botão de executar "Todas as Categorias"', t4.includes('Executar Todas as Categorias'))

  console.log('Estado 5: tela inicial (TF, nenhum ativo)')
  const t5 = text(await render('TF', ''))
  check('renderiza a tela inicial', t5.includes('Scanner de Backtest'))
} catch (e) {
  failures++
  console.log('  FALHA exceção ao renderizar:', e.message)
} finally {
  fs.rmSync(tmpPage, { force: true })
  fs.rmSync(outFile, { force: true })
}
console.log(failures ? `\n${failures} falha(s).` : '\nTodos os estados renderizam corretamente.')
process.exit(failures ? 1 : 0)
