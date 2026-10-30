"""Regras da "Meta 1% Bruto (24 h)": métricas da busca e decisões do bot. Funções puras, compartilhadas pela busca
(goal_service.py), pelo bot (bot/goal.py) e pelos testes, para a tela e a execução nunca divergirem.

META BRUTA: +TARGET_PCT % de variação de preço acumulada na sessão (antes das taxas), em % do stake, dentro de
WINDOW_HOURS horas corridas a partir do início do bot ("hoje" = 24 h, não o dia do calendário). O dinheiro que sobra é o
LÍQUIDO (taxas descontadas), que a tela mostra sempre ao lado do bruto.

CRITÉRIO PARA SAIR DA ILUSÃO: bater a meta bruta só conta como acerto REAL se, naquele instante, o líquido da sessão for
positivo. Acertos em que as taxas consomem o ganho (típico de quem faz muitas operações pequenas) são "ilusórios": ficam
fora da chance e aparecem à parte. E a estratégia só entra no Top 3 com resultado LÍQUIDO positivo no histórico.

Regras do bot em modo meta:
  * para ao atingir a meta bruta (a posição é vendida a mercado quando o ganho bruto aberto cobre o que falta);
  * para se não emitir nenhuma ordem em 24 h;
  * com posição aberta NÃO para por tempo: só por meta ou por saída com prejuízo (stop loss);
  * o trailing stop continua valendo na posição, até a meta ser atingida;
  * sem posição e com as 24 h esgotadas sem a meta, para.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

TARGET_PCT = 1.0                 # meta BRUTA, % do stake (US$ 100 -> US$ 1,00 de variação de preço, antes das taxas)
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
    "reached":   "Meta bruta atingida",
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


def net_equivalent_pct(target_gross: float = TARGET_PCT, fee: float | None = None) -> float:
    """Líquido aproximado de UMA operação que fecha exatamente na meta bruta (taxa por lado nas duas pernas).
    Com taxa de 0,1% por lado, +1% bruto ≈ +0,80% líquido."""
    if fee is None:
        from .profit_lock import fee_rate
        fee = fee_rate()
    return target_gross - fee * (200.0 + target_gross)


def window_hits(closed_trades: list[dict], candles: list, target_pct: float = TARGET_PCT,
                hours: float = WINDOW_HOURS, step_hours: float = 1.0, warmup: int = 100) -> dict:
    """Simula a SESSÃO do bot em janelas móveis de `hours` h (uma a cada `step_hours` h), com as regras do bot: começa zerado,
    só valem operações INTEIRAS dentro da janela, e a sessão termina no primeiro destes eventos:

      * META: o ganho BRUTO acumulado chega a `target_pct`. O bot vende quando o PICO da operação cobre o que falta
        (`max_gross_pct`), não só no fechamento. É acerto REAL se o líquido naquele ponto for > 0 (o líquido é o que falta
        menos as taxas da operação) e ILUSÓRIO se as taxas o consumirem;
      * STOP LOSS: uma operação fecha abaixo da entrada (ganho bruto < 0): a sessão encerra com o líquido acumulado;
      * senão a janela acaba: SEM ORDEM (nenhuma operação) ou EXPIRADA (operou, mas não chegou à meta).

    Devolve as contagens de cada desfecho, o resultado LÍQUIDO médio por sessão (`expected_net_pct`: o que esperar de
    uma sessão de 24 h depois das taxas, contando também as que perdem) e a amostra efetiva (`independent`: as janelas de
    hora em hora se sobrepõem, então vale a duração total dividida por `hours`)."""
    empty = {"real": 0, "illusory": 0, "stop_loss": 0, "expired": 0, "no_order": 0, "windows": 0,
             "independent": 0.0, "expected_net_pct": 0.0}
    if len(candles) <= warmup + 1:
        return empty
    width, step = int(hours * 3_600_000), int(step_hours * 3_600_000)
    items = sorted((candles[t["entry_time"]].epoch, candles[t["exit_time"]].epoch, t["gross_profit_pct"],
                    t["profit_pct"], t.get("max_gross_pct", t["gross_profit_pct"])) for t in closed_trades)
    t_start, t_end = candles[warmup].epoch, candles[-1].epoch
    out = dict(empty)
    net_sum = 0.0
    t0 = t_start
    while t0 + width <= t_end:
        gross = net = 0.0
        traded = False
        outcome = None
        for entry, exit_, g_pct, n_pct, peak in items:
            if entry < t0:
                continue
            if exit_ > t0 + width:
                break
            traded = True
            need = target_pct - gross
            if max(peak, g_pct) >= need:                       # o pico cobre o que falta: o bot vende na meta
                net_at_hit = net + need - (g_pct - n_pct)      # líquido = o que falta menos as taxas desta operação
                outcome = "real" if net_at_hit > 0 else "illusory"
                net = net_at_hit
                break
            gross += g_pct
            net += n_pct
            if g_pct < 0:
                outcome = "stop_loss"
                break
        if outcome is None:
            outcome = "expired" if traded else "no_order"
        out[outcome] += 1
        net_sum += net if traded else 0.0
        out["windows"] += 1
        t0 += step
    out["independent"] = (t_end - t_start) / width
    out["expected_net_pct"] = net_sum / out["windows"] if out["windows"] else 0.0
    return out


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
    if (stats.get("expected_net_pct") if stats.get("expected_net_pct") is not None else 0.0) <= 0:
        unmet.append(f"Resultado líquido médio por sessão de 24 h negativo "
                     f"({(stats.get('expected_net_pct') or 0.0):+.2f}%): contando as sessões que terminam em stop loss, perde.")
    if (stats.get("chance") or 0.0) < MIN_CHANCE:
        unmet.append(f"Chance REAL de +{TARGET_PCT:g}% bruto em 24 h (com líquido positivo) de "
                     f"{100 * (stats.get('chance') or 0.0):.1f}% (mínimo {100 * MIN_CHANCE:.0f}%).")
    return not unmet, unmet


def goal_deadline(started_at: datetime, window_hours: float = WINDOW_HOURS) -> datetime:
    return started_at + timedelta(hours=window_hours)


def decide_tick(*, now: datetime, started_at: datetime, window_hours: float, goal_pct: float,
                realized_gross_pct: float, in_position: bool, open_gross_pct: float,
                had_order: bool) -> tuple[str, str]:
    """A cada preço (tudo em % BRUTO do stake). -> ("continue"|"take_profit"|"stop", motivo).
    Com posição aberta nunca para por tempo."""
    if in_position:
        remaining = goal_pct - realized_gross_pct
        if open_gross_pct >= remaining + EXIT_MARGIN_PCT:
            return "take_profit", "reached"
        return "continue", ""
    if realized_gross_pct >= goal_pct:
        return "stop", "reached"
    if now >= goal_deadline(started_at, window_hours):
        return "stop", ("expired" if had_order else "no_order")
    return "continue", ""


def decide_after_close(*, now: datetime, started_at: datetime, window_hours: float, goal_pct: float,
                       realized_gross_pct: float, closed_gross_pct: float) -> tuple[str, str]:
    """Logo depois de uma posição fechar (em % BRUTO). Meta atingida; ou saída com prejuízo de preço (stop loss: o preço
    de saída ficou abaixo do de entrada); ou prazo esgotado."""
    if realized_gross_pct >= goal_pct:
        return "stop", "reached"
    if closed_gross_pct < 0:
        return "stop", "stop_loss"
    if now >= goal_deadline(started_at, window_hours):
        return "stop", "expired"
    return "continue", ""


def view_from_row(*, pct, window_hours, started_at, outcome, now: datetime | None = None,
                  result_gross_pct: float | None = None, result_net_pct: float | None = None) -> dict | None:
    """Visão da meta de um bot a partir dos campos do banco (vale para bot parado ou encerrado; o runtime acrescenta o
    P&L da sessão). None = bot sem meta. Horários em UTC com sufixo Z. `illusory` = a meta bruta foi atingida mas o
    líquido final ficou <= 0 (as taxas consumiram o ganho)."""
    if not pct:
        return None
    window = float(window_hours or WINDOW_HOURS)
    now = now or datetime.utcnow()
    deadline = goal_deadline(started_at, window) if started_at else None
    return {
        "enabled": True, "target_pct": float(pct), "window_hours": window,
        "net_equivalent_pct": round(net_equivalent_pct(float(pct)), 2),
        "started_at": started_at.isoformat() + "Z" if started_at else None,
        "deadline": deadline.isoformat() + "Z" if deadline else None,
        "seconds_left": max(0, int((deadline - now).total_seconds())) if deadline else None,
        "realized_gross_pct": None, "realized_net_pct": None, "open_gross_pct": 0.0, "open_net_pct": 0.0,
        "session_gross_pct": None, "session_net_pct": None, "in_position": False,
        "done": bool(outcome), "outcome": outcome, "outcome_label": OUTCOME_LABELS.get(outcome) if outcome else None,
        "result_gross_pct": result_gross_pct, "result_net_pct": result_net_pct,
        "illusory": bool(outcome == "reached" and result_net_pct is not None and result_net_pct <= 0),
    }
