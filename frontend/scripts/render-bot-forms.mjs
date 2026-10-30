// Renderiza "Novo bot" e "Reconfigurar" (SSR) e confere a regra de configuração FIXA: nenhum campo para o usuário editar
// timeframe, parâmetros da estratégia ou stop; o que será usado aparece somente leitura.
// Uso: node scripts/render-bot-forms.mjs
import fs from 'node:fs'
import path from 'node:path'
import { build } from 'esbuild'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const root = process.cwd()
const outFile = path.join(root, '.tmp_bot_forms.bundle.mjs')

const STRATEGIES = [{
  id: 'TF009', name: 'TF009 - Teste', recommended_timeframe: '1D', study_verdict: 'candidate', study_label: 'Candidata',
  params: { ema_period: { type: 'int', default: 21, min: 5, max: 50, description: 'Período da EMA' },
            sl_mult: { type: 'float', default: 3, description: 'Stop em ATR' } },
}]
const BOT = { id: 1, name: 'TF009 bot', strategy_id: 'TF009', symbol: 'SOL-USDT', timeframe: '15m', demo: true, active: false,
  stop_loss_usd: -50, strategy_params: { ema_period: 3 } }

const stubs = {
  'react-router-dom': `
    export const useNavigate = () => () => {}
    export const useParams = () => ({ id: '1' })
    export const useSearchParams = () => [new URLSearchParams('strategy=TF009&symbol=SOL-USDT')]`,
  '@tanstack/react-query': `
    const DATA = globalThis.__DATA
    export const useQuery = ({ queryKey }) => ({ data: DATA[queryKey[0]], isLoading: false })
    export const useQueryClient = () => ({ invalidateQueries: () => {}, setQueryData: () => {} })`,
  '../api': `export const getStrategies = () => {}; export const createBot = () => {}; export const rankAssets = () => {}
    export const getBots = () => {}; export const getMarketClock = () => {}; export const getSymbolTradability = () => {}
    export const getBot = () => {}; export const updateBot = () => {}; export const stopBot = () => {}; export const getTopAssets = () => {}
    export const getAiUsageStatus = () => {}; export const getStrategyStudy = () => {}`,
  '../i18n/LanguageContext': `export const useLanguage = () => ({ t: (k) => k, lang: 'pt-BR' })`,
}

async function render(page, replacements = []) {
  let src = fs.readFileSync(path.join(root, 'src/pages', page), 'utf8')
  for (const [a, b] of replacements) {
    if (!src.includes(a)) throw new Error(`trecho não encontrado em ${page}: ${a.slice(0, 50)}`)
    src = src.replace(a, b)
  }
  const tmp = path.join(root, 'src/pages', `.tmp_${page}`)
  fs.writeFileSync(tmp, src)
  try {
    await build({
      entryPoints: [tmp], bundle: true, platform: 'node', format: 'esm', outfile: outFile, write: true,
      external: ['react', 'react/jsx-runtime', 'react-dom', 'lucide-react'], jsx: 'automatic', loader: { '.jsx': 'jsx' },
      plugins: [{
        name: 'stubs',
        setup(b) {
          b.onResolve({ filter: /^(react-router-dom|@tanstack\/react-query|\.\.\/api|\.\.\/i18n\/LanguageContext)$/ },
            a => ({ path: a.path, namespace: 'stub' }))
          b.onLoad({ filter: /.*/, namespace: 'stub' }, a => ({ contents: stubs[a.path], loader: 'js' }))
        },
      }],
    })
    globalThis.__DATA = {
      strategies: STRATEGIES, bots: [], bot: BOT, 'top-assets': { assets: [{ symbol: 'SOL-USDT' }, { symbol: 'XRP-USDT' }] },
      'symbol-tradability': { symbols: [] }, 'market-clock': { is_open: true },
    }
    const mod = await import('file:///' + outFile.split(String.fromCharCode(92)).join('/') + '?v=' + Math.random())
    return renderToStaticMarkup(React.createElement(mod.default))
  } finally {
    fs.rmSync(tmp, { force: true })
  }
}

const text = h => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
let failures = 0
const check = (name, cond) => { console.log((cond ? '  ok   ' : '  FALHA') + ' ' + name); if (!cond) failures++ }

function fixedConfigChecks(h, t, label) {
  check(`${label}: mostra a configuração fixa`, t.includes('Configuração fixa') && t.includes('não são configuráveis'))
  check(`${label}: nenhum campo numérico editável (stop/parâmetros)`, !/type="number"/.test(h))
  check(`${label}: nenhum seletor de timeframe`, !/<option[^>]*>(1m|5m|15m|1h|4h|1D)<\/option>/.test(h))
  check(`${label}: nenhum parâmetro da estratégia listado`, !t.includes('ema_period') && !t.includes('sl_mult') && !t.includes('Período da EMA'))
  check(`${label}: stake, stop e alavancagem aparecem como valores fixos`, t.includes('US$ 100') && t.includes('US$ −50') && t.includes('1x (spot)'))
}

try {
  console.log('Novo bot')
  const nb = await render('NewBot.jsx', [["timeframe:       '',", "timeframe:       '1D',"]])
  const nbt = text(nb)
  check('renderiza sem exceção', true)
  fixedConfigChecks(nb, nbt, 'Novo bot')
  check('Novo bot: mostra o timeframe validado (1D) somente leitura', /Timeframe.{0,80}1D/.test(nbt))
  check('Novo bot: tem o campo de nome e o botão de criar', nb.includes('nb.bot_name') && /type="submit"/.test(nb))

  console.log('Reconfigurar')
  const eb = await render('EditBot.jsx', [["useState(null)", "useState({ name: 'TF009 bot', symbol: 'SOL-USDT', demo: true })"]])
  const ebt = text(eb)
  fixedConfigChecks(eb, ebt, 'Reconfigurar')
  check('Reconfigurar: só nome e ativo são editáveis', ebt.includes('Nome do Robô') && ebt.includes('Escolha do Ativo'))
  check('Reconfigurar: avisa que o bot usa timeframe diferente do validado (15m x 1D)', ebt.includes('usa o timeframe 15m') && ebt.includes('1D'))
} catch (e) {
  failures++
  console.log('  FALHA exceção ao renderizar:', e.message)
} finally {
  fs.rmSync(outFile, { force: true })
}
console.log(failures ? `\n${failures} falha(s).` : '\nNovo bot e Reconfigurar: configuração fixa confirmada.')
process.exit(failures ? 1 : 0)
