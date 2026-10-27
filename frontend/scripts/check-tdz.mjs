// Verifica "uso antes da declaração" (TDZ) em componentes React: um `const` usado no corpo da função
// antes da linha em que é declarado dá "Cannot access 'X' before initialization" só em tempo de execução,
// e o build não pega. Uso: node scripts/check-tdz.mjs src   (aceita arquivos .jsx ou pastas)
import fs from 'node:fs'
import { transformSync } from 'esbuild'
import { parseAst } from 'rollup/parseAst'

const FUNCS = new Set(['FunctionDeclaration', 'FunctionExpression', 'ArrowFunctionExpression'])

function patternNames(node, out = []) {
  if (!node) return out
  switch (node.type) {
    case 'Identifier': out.push(node.name); break
    case 'ObjectPattern': node.properties.forEach(p => patternNames(p.value ?? p.argument, out)); break
    case 'ArrayPattern': node.elements.forEach(e => patternNames(e, out)); break
    case 'AssignmentPattern': patternNames(node.left, out); break
    case 'RestElement': patternNames(node.argument, out); break
  }
  return out
}

// identificadores LIDOS durante a execução imediata (sem entrar em funções aninhadas, que rodam depois)
function immediateReads(node, reads = []) {
  if (!node || typeof node.type !== 'string') return reads
  if (FUNCS.has(node.type)) return reads
  if (node.type === 'Identifier') { reads.push(node); return reads }
  for (const [key, value] of Object.entries(node)) {
    if (key === 'type' || key === 'start' || key === 'end' || key === 'loc') continue
    if (node.type === 'MemberExpression' && key === 'property' && !node.computed) continue
    if (node.type === 'Property' && key === 'key' && !node.computed) continue
    if (Array.isArray(value)) value.forEach(v => immediateReads(v, reads))
    else if (value && typeof value === 'object') immediateReads(value, reads)
  }
  return reads
}

function listFiles(target) {
  if (fs.statSync(target).isDirectory()) {
    return fs.readdirSync(target).flatMap(name => listFiles(`${target}/${name}`))
  }
  return /\.jsx$/.test(target) ? [target] : []
}

let problems = 0
const files = process.argv.slice(2).flatMap(listFiles)
for (const file of files) {
  const source = fs.readFileSync(file, 'utf8')
  const js = transformSync(source, { loader: 'jsx', jsx: 'transform' }).code
  const ast = parseAst(js)
  const lineOf = (pos) => js.slice(0, pos).split('\n').length
  const visit = (fn, label) => {
    const body = fn.body?.type === 'BlockStatement' ? fn.body.body : []
    const declared = new Set(fn.params.flatMap(p => patternNames(p)))
    const later = new Map()                      // nome -> posição da declaração (const/let/class)
    for (const st of body) {
      if (st.type === 'VariableDeclaration' && st.kind !== 'var') {
        st.declarations.forEach(d => patternNames(d.id).forEach(n => later.set(n, st.start)))
      }
    }
    for (const st of body) {
      let reads = []
      if (st.type === 'VariableDeclaration') {
        st.declarations.forEach(d => { if (d.init) reads = reads.concat(immediateReads(d.init)) })
      } else if (st.type !== 'FunctionDeclaration') {
        reads = immediateReads(st)
      }
      for (const id of reads) {
        if (later.has(id.name) && !declared.has(id.name) && id.start < later.get(id.name)) {
          problems++
          console.log(`${file} (${label}): '${id.name}' é usado (~linha ${lineOf(id.start)} do JS gerado) antes de ser declarado (~linha ${lineOf(later.get(id.name))})`)
        }
      }
      if (st.type === 'VariableDeclaration') st.declarations.forEach(d => patternNames(d.id).forEach(n => declared.add(n)))
    }
    // componentes/hooks aninhados
    for (const st of body) {
      if (st.type === 'FunctionDeclaration') visit(st, st.id?.name ?? 'anon')
    }
  }
  for (const node of ast.body) {
    const decl = node.type === 'ExportDefaultDeclaration' ? node.declaration : node
    if (decl.type === 'FunctionDeclaration') visit(decl, decl.id?.name ?? 'default')
  }
}
console.log(problems ? `${problems} problema(s) de uso antes da declaração.` : `OK: ${files.length} arquivo(s) .jsx sem uso antes da declaração.`)
process.exit(problems ? 1 : 0)
