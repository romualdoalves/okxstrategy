import logging

from .strategies.base import Signal
from .strategies.registry import get_strategy

log = logging.getLogger("backtest_engine")

FIXED_STAKE_USD = 100.0
INITIAL_BALANCE = 1000.0


class BacktestEngine:
    def __init__(self, strategy_id: str, params: dict | None = None):
        try:
            self.strategy = get_strategy(strategy_id)
            if params:
                self.strategy.set_params(params)
            log.info("Engine inicializado para estratégia: %s", strategy_id)
        except Exception as exc:
            log.error("Erro ao inicializar estratégia no engine: %s", exc)
            raise

        self.balance = INITIAL_BALANCE
        self.events: list[dict] = []
        self.closed_trades: list[dict] = []

    async def run(self, candles: list, stake_usd: float = FIXED_STAKE_USD):
        """
        Executa backtest coerente com o app operacional: OKX spot, stake fixo
        de US$100 e somente entradas compradas. Sinal SELL fecha posição LONG;
        SELL com bot flat é contado como sinal ignorado, não como short.
        """
        stake_usd = FIXED_STAKE_USD
        self.balance = INITIAL_BALANCE
        self.events = []
        self.closed_trades = []
        equity_peak = INITIAL_BALANCE
        max_drawdown = 0.0
        ignored_sell_signals = 0

        position: dict | None = None

        if not candles or len(candles) < 150:
            return {
                "total_profit": 0.0,
                "trades_count": 0,
                "events_count": 0,
                "final_balance": self.balance,
                "trades": [],
                "closed_trades": [],
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "ignored_sell_signals": 0,
                "error": "Dados insuficientes para backtest (mínimo 150 candles)",
            }

        warmup = 100
        for i in range(warmup, len(candles)):
            current_slice = candles[: i + 1]
            current_price = float(candles[i].close or 0.0)
            if current_price <= 0:
                continue

            try:
                result = self.strategy.compute(current_slice)
                if not result:
                    continue

                exit_requested = result.signal == Signal.SELL or (
                    bool(result.hold_reason) and "saída" in result.hold_reason.lower()
                )

                if position is None:
                    if result.signal == Signal.BUY:
                        qty = stake_usd / current_price
                        position = {
                            "entry_price": current_price,
                            "entry_time": i,
                            "qty": qty,
                            "reason": result.hold_reason,
                        }
                        self.events.append({
                            "type": "ENTRY LONG",
                            "side": "buy",
                            "price": current_price,
                            "time": i,
                            "reason": result.hold_reason,
                        })
                    elif result.signal == Signal.SELL:
                        ignored_sell_signals += 1
                    continue

                unrealized = position["qty"] * (current_price - position["entry_price"])
                equity = self.balance + unrealized
                equity_peak = max(equity_peak, equity)
                max_drawdown = max(max_drawdown, equity_peak - equity)

                if exit_requested:
                    profit = position["qty"] * (current_price - position["entry_price"])
                    self.balance += profit
                    closed = {
                        "type": "TRADE LONG",
                        "entry_price": position["entry_price"],
                        "exit_price": current_price,
                        "price": current_price,
                        "profit": profit,
                        "profit_pct": (current_price - position["entry_price"]) / position["entry_price"] * 100,
                        "balance": self.balance,
                        "entry_time": position["entry_time"],
                        "exit_time": i,
                        "bars_held": i - position["entry_time"],
                        "reason": result.hold_reason or "Sinal SELL",
                    }
                    self.closed_trades.append(closed)
                    self.events.append({
                        "type": "EXIT LONG",
                        "side": "sell",
                        "price": current_price,
                        "profit": profit,
                        "balance": self.balance,
                        "time": i,
                        "reason": closed["reason"],
                    })
                    position = None
            except Exception as exc:
                log.error("Erro no passo %d do backtest: %s", i, exc)
                continue

        open_position = None
        if position is not None:
            last_price = float(candles[-1].close or position["entry_price"])
            open_position = {
                "entry_price": position["entry_price"],
                "last_price": last_price,
                "unrealized": position["qty"] * (last_price - position["entry_price"]),
                "profit_pct": (last_price - position["entry_price"]) / position["entry_price"] * 100,
                "bars_held": len(candles) - 1 - position["entry_time"],
            }

        wins = [t for t in self.closed_trades if t["profit"] > 0]
        losses = [t for t in self.closed_trades if t["profit"] <= 0]
        gross_profit = sum(t["profit"] for t in wins)
        gross_loss = abs(sum(t["profit"] for t in losses))
        trades_count = len(self.closed_trades)
        win_rate = (len(wins) / trades_count * 100) if trades_count else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

        return {
            "total_profit": round(self.balance - INITIAL_BALANCE, 2),
            "trades_count": trades_count,
            "events_count": len(self.events),
            "final_balance": round(self.balance, 2),
            "trades": self.events,
            "closed_trades": self.closed_trades,
            "win_rate": round(win_rate, 2),
            "wins": len(wins),
            "losses": len(losses),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown": round(max_drawdown, 2),
            "avg_trade": round((self.balance - INITIAL_BALANCE) / trades_count, 2) if trades_count else 0.0,
            "best_trade": round(max((t["profit"] for t in self.closed_trades), default=0.0), 2),
            "worst_trade": round(min((t["profit"] for t in self.closed_trades), default=0.0), 2),
            "ignored_sell_signals": ignored_sell_signals,
            "open_position": open_position,
            "assumptions": [
                "OKX spot-only: sinais SELL com posição flat são ignorados, não viram short.",
                "Stake fixo de US$100 por entrada.",
                "Resultado ainda não inclui taxas, slippage nem latência de execução.",
            ],
        }


def backtest_recommendation(r: dict) -> dict:
    """
    Deriva o veredicto INICIAR / CUIDADO / NÃO INICIAR / N/A a partir dos
    resultados brutos de BacktestEngine.run(). Espelha a lógica de
    getBacktestRecommendation() no frontend (BotDetail.jsx).
    """
    pf = r.get("profit_factor") or 0.0
    tc = r.get("trades_count") or 0
    tp = r.get("total_profit") or 0.0
    dd = r.get("max_drawdown") or 0.0

    if tc == 0:
        return {"verdict": "NÃO INICIAR", "level": "danger",
                "reasons": ["Nenhum trade fechado no período."]}
    if tp <= 0:
        return {"verdict": "NÃO INICIAR", "level": "danger",
                "reasons": [f"PnL negativo ({tp:.2f} USD) — estratégia perdedora neste período."]}
    if pf < 1.0:
        return {"verdict": "NÃO INICIAR", "level": "danger",
                "reasons": [f"Profit Factor {pf:.2f}: perdas superam ganhos brutos."]}

    issues: list[str] = []
    highlights: list[str] = [f"PnL positivo: +{tp:.2f} USD."]

    if pf >= 1.5:
        highlights.append(f"Profit Factor excelente: {pf:.2f}.")
    elif pf >= 1.2:
        highlights.append(f"Profit Factor adequado: {pf:.2f} (idealmente ≥ 1.5).")
    else:
        issues.append(f"Profit Factor {pf:.2f} abaixo do mínimo recomendado (1.2).")

    if tc >= 5:
        highlights.append(f"{tc} trades fechados — amostra razoável.")
    elif tc >= 3:
        highlights.append(f"{tc} trades fechados (mínimo aceitável).")
    else:
        issues.append(f"Apenas {tc} trade(s) fechado(s) — amostra insuficiente.")

    dd_ratio = dd / tp if tp > 0 else 999.0
    if dd_ratio <= 0.5:
        highlights.append(f"Drawdown controlado: ${dd:.2f} ({dd_ratio * 100:.0f}% do lucro).")
    elif dd_ratio <= 1.0:
        issues.append(f"Drawdown ${dd:.2f} representa {dd_ratio * 100:.0f}% do lucro — elevado.")
    else:
        issues.append(f"Drawdown ${dd:.2f} supera o lucro total — risco muito elevado.")

    if issues:
        return {"verdict": "CUIDADO", "level": "warning", "reasons": highlights + issues}
    return {"verdict": "INICIAR", "level": "success", "reasons": highlights}
