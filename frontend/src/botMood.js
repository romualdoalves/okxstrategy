// Humor do bot para leitura imediata no card. Usa SEMPRE o resultado LÍQUIDO de taxas (o bruto enganaria:
// +0,2% bruto é prejuízo depois das taxas de entrada e saída).
export const MOOD_LIMITS = {
  sadBelow: -0.10,     // % líquido abaixo do qual o bot está claramente no prejuízo
  happyAbove: 0.10,    // % líquido acima do qual há lucro real (a faixa ±0,10 é "neutro")
  exultantAt: 1.0,     // % líquido a partir do qual o lucro é expressivo
}

const fmt = n => `${n > 0 ? '+' : ''}${Number(n).toFixed(2)}%`

/**
 * @param {boolean} isPositioned  há posição aberta
 * @param {number|null} netPct    P&L aberto líquido (%), ou null se indisponível
 * @param {boolean} trailingLocked trailing stop armado com lucro garantido (> 0 líquido)
 */
export function botMood({ isPositioned, netPct, trailingLocked = false }) {
  if (!isPositioned) {
    return { key: 'neutral', emoji: '😐', label: 'Neutro', reason: 'Sem posição aberta — aguardando sinal' }
  }
  if (netPct == null || Number.isNaN(netPct)) {
    return { key: 'neutral', emoji: '😐', label: 'Neutro', reason: 'Posição aberta — resultado líquido ainda indisponível' }
  }
  if (trailingLocked || netPct >= MOOD_LIMITS.exultantAt) {
    return {
      key: 'exultant', emoji: '🤩', label: 'Exultante',
      reason: trailingLocked
        ? `Trailing stop ativo com lucro garantido · agora ${fmt(netPct)} líquido`
        : `Lucro líquido expressivo: ${fmt(netPct)}`,
    }
  }
  if (netPct > MOOD_LIMITS.happyAbove) {
    return { key: 'happy', emoji: '😀', label: 'Alegre', reason: `Lucro líquido: ${fmt(netPct)}` }
  }
  if (netPct < MOOD_LIMITS.sadBelow) {
    return { key: 'sad', emoji: '😞', label: 'Triste', reason: `Prejuízo líquido: ${fmt(netPct)}` }
  }
  return { key: 'neutral', emoji: '😐', label: 'Neutro', reason: `Resultado líquido próximo de zero: ${fmt(netPct)}` }
}
