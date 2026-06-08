"""
Formata mensagens de eventos de trading para envio via Telegram.
Cada função recebe os dados disponíveis no momento do evento e retorna
uma string em Markdown (MarkdownV1 do Telegram).
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_price(v: float | None) -> str:
    if v is None:
        return "—"
    return f"${v:,.2f}"


def _fmt_pnl(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}${v:.2f}"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _indicator_lines(indicators: dict[str, Any]) -> str:
    """Formata o dicionário de indicadores em linhas legíveis."""
    SKIP = set()  # chaves de baixo valor para exibição
    lines = []

    # Ordem preferida de exibição
    ORDER = [
        ("regime_state", "Regime"),
        ("spread_pct",       "Spread %"),
        ("net_spread_pct",   "Spread Líquido %"),
        ("best_dex_price",   "Melhor Preço DEX"),
        ("okx_price",        "Preço OKX"),
        ("opportunities",    "Oportunidades"),
        ("atr",          "ATR"),
        ("ema_fast",     "EMA rápida"),
        ("ema_slow",     "EMA lenta"),
        ("ema_21",       "EMA 21"),
        ("ema_55",       "EMA 55"),
        ("rsi",          "RSI"),
        ("macd",         "MACD"),
        ("signal",       "Sinal MACD"),
        ("histogram",    "Histograma"),
        ("bbands_upper", "BB Superior"),
        ("bbands_lower", "BB Inferior"),
        ("supertrend",   "SuperTrend"),
        ("vwap",         "VWAP"),
        ("graph_density",     "Grafo Densidade"),
        ("centrality_btc",    "Centralidade BTC"),
        ("n_communities",     "Comunidades"),
    ]

    shown = set()
    for key, label in ORDER:
        if key in indicators and key not in SKIP:
            val = indicators[key]
            if isinstance(val, float):
                lines.append(f"`{label}: {val:,.4g}`")
            else:
                lines.append(f"`{label}: {val}`")
            shown.add(key)

    # Restante não contemplado na ordem preferida
    for key, val in indicators.items():
        if key in shown or key in SKIP:
            continue
        label = key.replace("_", " ").title()
        if isinstance(val, float):
            lines.append(f"`{label}: {val:,.4g}`")
        elif val is not None:
            lines.append(f"`{label}: {val}`")

    return "\n".join(lines) if lines else "`—`"


def _account_lines(daily_pnl: float, stop_loss_usd: float, wins: int, losses: int) -> str:
    total = wins + losses
    win_rate = f"{wins/total*100:.0f}%" if total > 0 else "—"
    drawdown_pct = abs(daily_pnl / stop_loss_usd * 100) if stop_loss_usd else 0
    return (
        f"`P&L Diário : {_fmt_pnl(daily_pnl)} / Limite: ${stop_loss_usd:.0f}`\n"
        f"`Drawdown   : {drawdown_pct:.1f}% do limite`\n"
        f"`Win Rate   : {win_rate}  (W {wins} / L {losses})`"
    )


def _header(bot_name: str, demo: bool) -> str:
    tag = "  `[DEMO]`" if demo else ""
    return f"🤖 *{bot_name}*{tag}"


# ── Mensagens de evento ───────────────────────────────────────────────────────

def build_entry_msg(
    *,
    bot_name:      str,
    strategy_id:   str,
    symbol:        str,
    timeframe:     str,
    leverage:      int,
    stake_usd:     float,
    demo:          bool,
    stop_loss_usd: float,
    direction:     str,       # "long" | "short"
    entry_price:   float,
    sl_price:      float,
    tp1_price:     float,
    sz:            int,
    indicators:    dict,
    daily_pnl:     float,
    wins:          int,
    losses:        int,
) -> str:
    emoji = "🟢" if direction == "long" else "🔴"
    dir_label = "LONG" if direction == "long" else "SHORT"

    sl_dist  = abs(entry_price - sl_price)
    tp1_dist = abs(tp1_price  - entry_price)
    rr = f"{tp1_dist / sl_dist:.1f}" if sl_dist else "—"

    return (
        f"{_header(bot_name, demo)}\n"
        f"{emoji} *ENTRADA {dir_label}* — {_now_utc()}\n"
        f"\n"
        f"🪙 `{symbol}`  ⏱ `{timeframe}`  💵 `Spot stake: ${stake_usd:.0f}`\n"
        f"\n"
        f"📍 *Entrada :* {_fmt_price(entry_price)}\n"
        f"🛑 *Stop Loss:* {_fmt_price(sl_price)}  `(-{_fmt_price(sl_dist)[1:]})`\n"
        f"🎯 *TP1      :* {_fmt_price(tp1_price)}  `(+{_fmt_price(tp1_dist)[1:]})`\n"
        f"📐 *R:R      :* `1:{rr}`  |  Quantidade: `{sz}`\n"
        f"\n"
        f"📊 *Indicadores*\n"
        f"{_indicator_lines(indicators)}\n"
        f"\n"
        f"💼 *Conta*\n"
        f"{_account_lines(daily_pnl, stop_loss_usd, wins, losses)}"
    )


def build_order_confirmed_msg(
    *,
    bot_name:    str,
    demo:        bool,
    symbol:      str,
    direction:   str,
    order_id:    str,
    filled_price: float,
    sz:          float,
    sl_price:    float,
    tp1_price:   float,
) -> str:
    """Disparado quando a OKX confirma o fill real da ordem de entrada."""
    dir_label = "LONG" if direction == "long" else "SHORT"
    return (
        f"{_header(bot_name, demo)}\n"
        f"✅ *ORDEM CONFIRMADA* — {_now_utc()}\n"
        f"\n"
        f"🪙 `{symbol}`  |  {dir_label}\n"
        f"\n"
        f"🆔 *Order ID:* `{order_id}`\n"
        f"💵 *Fill real:* {_fmt_price(filled_price)}  (qty: `{sz}`\n"
        f"🛑 *Stop Loss:* {_fmt_price(sl_price)}\n"
        f"🎯 *TP1      :* {_fmt_price(tp1_price)}\n"
        f"\n"
        f"_A posição está ativa e confirmada na OKX._"
    )


def build_order_failed_msg(
    *,
    bot_name:  str,
    demo:      bool,
    symbol:    str,
    direction: str,
    error_msg: str,
    sz:        float,
    price:     float,
) -> str:
    """Disparado quando a OKX rejeita a ordem de entrada."""
    dir_label = "LONG" if direction == "long" else "SHORT"
    return (
        f"{_header(bot_name, demo)}\n"
        f"❌ *ORDEM REJEITADA* — {_now_utc()}\n"
        f"\n"
        f"🪙 `{symbol}`  |  {dir_label}  |  Qty: `{sz}`  |  ~{_fmt_price(price)}\n"
        f"\n"
        f"⚠️ *Erro da exchange:*\n"
        f"`{error_msg}`\n"
        f"\n"
        f"_Nenhuma posição foi aberta. O bot voltou ao estado FLAT._"
    )


def build_balance_snapshot_msg(
    *,
    account: dict,
    positions: list,
    provider: str = "OKX",
) -> str:
    equity = float(account.get("equity") or 0)
    cash = float(account.get("cash") or 0)
    unrealized = float(account.get("unrealized_pl") or 0)
    currency = account.get("currency") or "USDT"

    pos_lines = []
    for pos in positions[:8]:
        symbol = pos.get("instId") or pos.get("symbol") or "?"
        size = pos.get("pos") or pos.get("size") or "?"
        upl = pos.get("upl") or pos.get("unrealized_pnl") or "0"
        pos_lines.append(f"`{symbol}: {size} | UPL {upl}`")
    positions_text = "\n".join(pos_lines) if pos_lines else "`Sem posições abertas`"

    return (
        f"💼 *{provider} — SALDOS OFICIAIS* — {_now_utc()}\n"
        f"\n"
        f"`Equity : {equity:,.2f} {currency}`\n"
        f"`Cash   : {cash:,.2f} {currency}`\n"
        f"`UPL    : {_fmt_pnl(unrealized)}`\n"
        f"\n"
        f"📌 *Posições*\n"
        f"{positions_text}"
    )


def build_tp1_msg(
    *,
    bot_name:      str,
    demo:          bool,
    symbol:        str,
    direction:     str,
    tp1_price:     float,
    entry_price:   float,
    pnl:           float,
    sz_half:       int,
    stop_loss_usd: float,
    daily_pnl:     float,
    wins:          int,
    losses:        int,
) -> str:
    dir_label = "LONG" if direction == "long" else "SHORT"

    return (
        f"{_header(bot_name, demo)}\n"
        f"🟡 *TAKE PROFIT 1* — {_now_utc()}\n"
        f"\n"
        f"🪙 `{symbol}`  |  {dir_label}\n"
        f"\n"
        f"💰 *Preço TP1 :* {_fmt_price(tp1_price)}\n"
        f"📍 *Entrada   :* {_fmt_price(entry_price)}\n"
        f"📊 *P&L parcial:* `{_fmt_pnl(pnl)}`  ({sz_half} contratos fechados)\n"
        f"🔄 *Trailing stop ativado* para o restante\n"
        f"\n"
        f"💼 *Conta*\n"
        f"{_account_lines(daily_pnl, stop_loss_usd, wins, losses)}"
    )


def build_stop_msg(
    *,
    bot_name:    str,
    demo:        bool,
    symbol:      str,
    daily_pnl:   float,
    wins:        int,
    losses:      int,
    reason:      str = "Servidor desligado",
) -> str:
    total    = wins + losses
    win_rate = f"{wins/total*100:.0f}%" if total > 0 else "—"
    return (
        f"{_header(bot_name, demo)}\n"
        f"⏹️ *PARADO* — {_now_utc()}\n"
        f"\n"
        f"🪙 `{symbol}`\n"
        f"📋 Motivo: `{reason}`\n"
        f"\n"
        f"💼 *Resumo da sessão*\n"
        f"`P&L    : {_fmt_pnl(daily_pnl)}`\n"
        f"`Trades : W {wins} / L {losses}  ({win_rate} win rate)`"
    )


def build_start_msg(
    *,
    bot_name:        str,
    strategy_id:     str,
    symbol:          str,
    timeframe:       str,
    leverage:        int,
    stake_usd:       float,
    demo:            bool,
    stop_loss_usd:   float,
    strategy_params: dict,
    indicators:      dict,
    restarted:       bool = False,
) -> str:
    action = "🔄 *REINICIADO*" if restarted else "▶️ *INICIADO*"

    params_lines = "\n".join(
        f"`{k}: {v}`" for k, v in strategy_params.items()
    ) if strategy_params else "`(padrão)`"

    indic_section = (
        f"\n📊 *Indicadores iniciais*\n{_indicator_lines(indicators)}\n"
        if indicators else ""
    )

    return (
        f"{_header(bot_name, demo)}\n"
        f"{action} — {_now_utc()}\n"
        f"\n"
        f"🪙 `{symbol}`  ⏱ `{timeframe}`  `SPOT sem alavancagem`\n"
        f"💵 `Stake: ${stake_usd:.0f}`  🛑 `Stop diário: ${stop_loss_usd:.0f}`\n"
        f"\n"
        f"⚙️ *Estratégia:* `{strategy_id}`\n"
        f"🔩 *Parâmetros*\n"
        f"{params_lines}"
        f"{indic_section}"
    )


def build_exit_msg(
    *,
    bot_name:      str,
    demo:          bool,
    symbol:        str,
    direction:     str,       # "long" | "short"
    exit_reason:   str,       # "SL" | "TRAILING" | "MANUAL" | "WS_EXECUTION" etc.
    entry_price:   float,
    exit_price:    float,
    pnl:           float,
    daily_pnl:     float,
    stop_loss_usd: float,
    wins:          int,
    losses:        int,
) -> str:
    dir_label = "LONG" if direction == "long" else "SHORT"
    emoji = "🔴" if pnl < 0 else "🟢"
    pct = abs(exit_price - entry_price) / entry_price * 100 if entry_price else 0

    reason_labels = {
        "SL": "Stop Loss atingido",
        "TRAILING": "Trailing Stop",
        "MANUAL": "Fechamento manual",
        "WS_EXECUTION": "Exchange (execução)",
        "RESET_FORCE_CLOSE": "Reset forçado",
        "DYNAMIC_SHADOW_TRIGGER": "Shadow Trailing Stop",
    }
    reason_label = reason_labels.get(exit_reason, exit_reason)

    return (
        f"{_header(bot_name, demo)}\n"
        f"{emoji} *POSIÇÃO FECHADA* — {_now_utc()}\n"
        f"\n"
        f"🪙 `{symbol}`  |  {dir_label}\n"
        f"\n"
        f"📋 *Motivo  :* `{reason_label}`\n"
        f"📍 *Entrada :* {_fmt_price(entry_price)}\n"
        f"🚪 *Saída   :* {_fmt_price(exit_price)}  `({pct:.2f}%)`\n"
        f"💰 *P&L     :* `{_fmt_pnl(pnl)}`\n"
        f"\n"
        f"💼 *Conta*\n"
        f"{_account_lines(daily_pnl, stop_loss_usd, wins, losses)}"
    )


def build_circuit_breaker_msg(
    *,
    bot_name:      str,
    demo:          bool,
    symbol:        str,
    daily_pnl:     float,
    stop_loss_usd: float,
    wins:          int,
    losses:        int,
) -> str:
    return (
        f"{_header(bot_name, demo)}\n"
        f"🚨 *CIRCUIT BREAKER — BOT PAUSADO* — {_now_utc()}\n"
        f"\n"
        f"🪙 `{symbol}`\n"
        f"\n"
        f"Stop Loss diário atingido!\n"
        f"`P&L Diário : {_fmt_pnl(daily_pnl)}`\n"
        f"`Limite     : ${stop_loss_usd:.0f}`\n"
        f"\n"
        f"💼 *Resumo do dia*\n"
        f"{_account_lines(daily_pnl, stop_loss_usd, wins, losses)}\n"
        f"\n"
        f"⛔ Bot pausado. Reinicie manualmente amanhã."
    )
