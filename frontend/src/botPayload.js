// Payload de criação de bot a partir de uma linha de resultado (Scanner BT, Meta 1% Hoje!). Um só lugar: as duas telas
// criam bots do mesmo jeito (demo, spot sem alavancagem, stake fixo de US$ 100, stop diário padrão).
export const FIXED_STAKE_USD = 100

export function buildBotPayload(row, symbol, extra = {}) {
  return {
    name: `${row.strategy_id} - ${row.strategy_name || 'Scanner BT'} (${symbol})`,
    strategy_id: row.strategy_id,
    symbol,
    timeframe: row.timeframe || '15m',
    demo: true,
    stake_usd: FIXED_STAKE_USD,
    leverage: 1,
    stop_loss_usd: -50,
    strategy_params: {},
    ...extra,
  }
}
