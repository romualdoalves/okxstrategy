// Renderiza o quadro "Taxa Real x Taxa Demo" do card do bot (SSR) e confere os números do cálculo, à mão.
// Uso: node scripts/render-fee-compare.mjs
import path from 'node:path'
import { build } from 'esbuild'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

const root = process.cwd()
const outFile = path.join(root, '.tmp_fee_compare.bundle.mjs')

await build({
  entryPoints: [path.join(root, 'src/components/FeeCompare.jsx')], bundle: true, platform: 'node', format: 'esm', outfile: outFile,
  external: ['react', 'react/jsx-runtime'], jsx: 'automatic', loader: { '.jsx': 'jsx' },
})
const mod = await import('file:///' + outFile.split(String.fromCharCode(92)).join('/') + '?v=' + Math.random())
const FeeCompare = mod.default
const text = h => h.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
let failures = 0
const check = (name, cond) => { console.log((cond ? '  ok   ' : '  FALHA') + ' ' + name); if (!cond) failures++ }
const near = (a, b) => Math.abs(a - b) < 1e-9

// Cálculo à mão: LONG entrada 100, preço 100,5 → bruto +0,50%.
//   Real 0,10%: taxas = (100 + 100,5) × 0,001 / 100 × 100 = 0,2005% → líquido +0,2995%
//   Demo 0,40%: taxas = (100 + 100,5) × 0,004 / 100 × 100 = 0,8020% → líquido −0,3020%
const c1 = mod.netFor(1, 100, 100.5, 0.001)
const c4 = mod.netFor(1, 100, 100.5, 0.004)
check('cálculo da taxa real: bruto +0,50%, taxas 0,2005%, líquido +0,2995%', near(c1.gross, 0.5) && near(c1.fees, 0.2005) && near(c1.net, 0.2995))
check('cálculo da taxa demo: taxas 0,8020%, líquido −0,3020%', near(c4.fees, 0.802) && near(c4.net, -0.302))
const s1 = mod.netFor(-1, 100, 99, 0.001)
check('short: preço cai 1% = bruto +1,00%', near(s1.gross, 1) && near(s1.fees, 0.199))

const open = text(renderToStaticMarkup(React.createElement(FeeCompare, { direction: 1, entry: 100, last: 100.5, demoRate: 0.004, realRate: 0.001 })))
check('cabeçalho mostra as duas taxas', open.includes('Real 0,10%') && open.includes('Demo 0,40%'))
check('linha da taxa real com o cálculo', open.includes('Taxa Real 0,10%') && open.includes('+0,50%') && open.includes('0,20%') && open.includes('+0,30%'))
check('linha da taxa demo com o cálculo e o líquido negativo', open.includes('Taxa Demo 0,40%') && open.includes('0,80%') && open.includes('-0,30%'))

const flat = text(renderToStaticMarkup(React.createElement(FeeCompare, { direction: 0, entry: 0, last: 100, demoRate: 0.004, realRate: 0.001 })))
check('sem posição: custo de ida e volta das duas taxas', flat.includes('0,20%') && flat.includes('0,80%') && flat.includes('ida e volta'))

const same = text(renderToStaticMarkup(React.createElement(FeeCompare, { direction: 0, entry: 0, last: 0, demoRate: 0.001, realRate: 0.001 })))
check('taxas iguais: avisa que são iguais', same.includes('(iguais)'))
check('sem taxas conhecidas: não mostra nada', renderToStaticMarkup(React.createElement(FeeCompare, { direction: 0, demoRate: null, realRate: null })) === '')

import fs from 'node:fs'
fs.rmSync(outFile, { force: true })
if (failures) { console.log(`\n${failures} falha(s).`); process.exit(1) }
console.log('Quadro Taxa Real x Taxa Demo confere com o cálculo à mão.')
