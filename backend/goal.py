"""Regras da "Meta 1% Hoje": métricas da busca e decisões do bot. Funções puras, compartilhadas pela busca
(goal_service.py), pelo bot (bot/goal.py) e pelos testes, para a tela e a execução nunca divergirem.

Meta: +TARGET_PCT % LÍQUIDO (taxas e slippage descontados) sobre o stake, acumulado numa sessão de WINDOW_HOURS horas
("hoje" = 24 horas corridas a partir do início do bot, não o dia do calendário).

Regras do bot em modo meta:
  * para ao atingir a meta (a posição é vendida a mercado quando o lucro líquido aberto cobre o que falta);
  * para se não emitir nenhuma ordem em 24 h;
  * com posição aberta NÃO para por tempo: só por meta ou por saída com prejuízo (stop loss);
  * o trailing stop continua valendo na posição, até a meta ser atingida;
  * sem posição e com as 24 h esgotadas sem a meta, para.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

TARGET_PCT = 1.0                 # meta líquida, % do stake (US$ 100 -> US$ 1,00)
WINDOW_HOURS = 24.0
EXIT_MARGIN_PCT = 0.05           # folga no take-profit da meta: a venda é a mercado e tem slippage
INTRADAY_TIMEFRAMES = ("1m", "5m", "15m", "1h")   # timeframes (minúsculos) que a busca considera para "24 h"

# Critérios para uma estratégia entrar no Top 3
MIN_TRADES = 10
MIN_PROFIT_FACTOR = 1.2
MIN_CHANCE = 0.20                # ao menos 1 em cada 5 janelas de 24 h bate a meta
MIN_INDEPENDENT_WINDOWS = 10     # histórico mínimo: 10 janelas de 24 h independentes (~10 dias); com menos, a chance não significa nada
TOP_N = 3

OUTCOME_LABELS = {
    "reached":   "Meta atingida",
    "stop_loss": "Encerrado: saída com prejuízo (stop loss)",
    "no_order":  "Encerrado: nenhuma ordem em 24 h",
    "expired":   "Encerrado: 24 h sem atingir a meta",
}


def wilson_lower_bound(p: float, n: float, z: float = 1.96) -> float:
    """Limite inferior (IC 95%) de uma proporção p observada em n amostras independentes: penaliza amostras curtas."""
    if n <= 0:
        return 0.0
    p = min(1.0, max(0.0, p))
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - spread) / denom)


def window_hits(closed_trades: list[dict], candles: list, target_pct: float = TARGET_PCT,
                hours: float = WINDOW_HOURS, step_hours: float = 1.0, warmup: int = 100) -> tuple[int, int, float]:
    """Janelas móveis de `hours` h (uma a cada `step_hours` h). Em cada uma o bot começa zerado e só valem as operações
    INTEIRAS dentro da janela (entrada e saída nela). Acerto = o lucro líquido acumulado chegou a `target_pct` em algum
    ponto. Devolve (acertos, janelas, janelas_independentes): as de hora em hora se sobrepõem, então a amostra efetiva
    é a duração total dividida por `hours`."""
    if len(candles) <= warmup + 1:
        return 0, 0, 0.0
    width, step = int(hours * 3_600_000), int(step_hours * 3_600_000)
    items = sorted((candles[t["entry_time"]].epoch, candles[t["exit_time"]].epoch, t["profit_pct"])
                   for t in closed_trades)
    t_start, t_end = candles[warmup].epoch, candles[-1].epoch
    hits = windows = 0
    t0 = t_start
    while t0 + width <= t_end:
        cumulative, hit = 0.0, False
        for entry, exit_, pct in items:
            if entry < t0:
                continue
            if exit_ > t0 + width:
                break
            cumulative += pct
            if cumulative >= target_pct:
                hit = True
                break
        hits += hit
        windows += 1
        t0 += step
    return hits, windows, (t_end - t_start) / width


def qualification(stats: dict) -> tuple[bool, list[str]]:
    """Critérios para o Top 3. Devolve (atende, motivos pelos quais não atende) — a tela mostra os motivos."""
    unmet = []
    if (stats.get("trades_count") or 0) < MIN_TRADES:
        unmet.append(f"Poucas operações no histórico ({stats.get('trades_count', 0)} < {MIN_TRADES}).")
    if (stats.get("total_profit") or 0.0) <= 0:
        unmet.append(f"Lucro líquido total negativo no período (US$ {stats.get('total_profit', 0.0):.2f}).")
    if (stats.get("profit_factor") or 0.0) < MIN_PROFIT_FACTOR:
        unmet.append(f"Profit Factor {stats.get('profit_factor', 0.0):.2f} abaixo de {MIN_PROFIT_FACTOR}.")
    if (stats.get("independent_windows") or 0.0) < MIN_INDEPENDENT_WINDOWS:
        unmet.append(f"Histórico curto demais: {(stats.get('independent_windows') or 0.0):.0f} janelas de 24 h independentes "
                     f"(mínimo {MIN_INDEPENDENT_WINDOWS}); a chance medida não é confiável.")
    if (stats.get("chance") or 0.0) < MIN_CHANCE:
        unmet.append(f"Chance de bater a meta em 24 h de {100 * (stats.get('chance') or 0.0):.1f}% "
                     f"(mínimo {100 * MIN_CHANCE:.0f}%).")
    return not unmet, unmet


def goal_deadline(started_at: datetime, window_hours: float = WINDOW_HOURS) -> datetime:
    return started_at + timedelta(hours=window_hours)


def decide_tick(*, now: datetime, started_at: datetime, window_hours: float, goal_pct: float,
                realized_net_pct: float, in_position: bool, open_net_pct: float,
                had_order: bool) -> tuple[str, str]:
    """A cada preço. -> ("continue"|"take_profit"|"stop", motivo). Com posição aberta nunca para por tempo."""
    if in_position:
        remaining = goal_pct - realized_net_pct
        if open_net_pct >= remaining + EXIT_MARGIN_PCT:
            return "take_profit", "reached"
        return "continue", ""
    if realized_net_pct >= goal_pct:
        return "stop", "reached"
    if now >= goal_deadline(started_at, window_hours):
        return "stop", ("expired" if had_order else "no_order")
    return "continue", ""


def decide_after_close(*, now: datetime, started_at: datetime, window_hours: float, goal_pct: float,
                       realized_net_pct: float, closed_net_pct: float) -> tuple[str, str]:
    """Logo depois de uma posição fechar. Meta atingida; ou saída com prejuízo (stop loss); ou prazo esgotado."""
    if realized_net_pct >= goal_pct:
        return "stop", "reached"
    if closed_net_pct < 0:
        return "stop", "stop_loss"
    if now >= goal_deadline(started_at, window_hours):
        return "stop", "expired"
    return "continue", ""


def view_from_row(*, pct, window_hours, started_at, outcome, now: datetime | None = None) -> dict | None:
    """Visão da meta de um bot a partir dos campos do banco (vale para bot parado ou encerrado; o runtime acrescenta o
    P&L da sessão). None = bot sem meta. Horários em UTC com sufixo Z."""
    if not pct:
        return None
    window = float(window_hours or WINDOW_HOURS)
    now = now or datetime.utcnow()
    deadline = goal_deadline(started_at, window) if started_at else None
    return {
        "enabled": True, "target_pct": float(pct), "window_hours": window,
        "started_at": started_at.isoformat() + "Z" if started_at else None,
        "deadline": deadline.isoformat() + "Z" if deadline else None,
        "seconds_left": max(0, int((deadline - now).total_seconds())) if deadline else None,
        "realized_net_pct": None, "open_net_pct": 0.0, "session_net_pct": None, "in_position": False,
        "done": bool(outcome), "outcome": outcome, "outcome_label": OUTCOME_LABELS.get(outcome) if outcome else None,
    }
