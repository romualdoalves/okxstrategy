"""
Rotas de análise por bot: backtest, otimização, IA, grafo e signal logs.
"""

from __future__ import annotations
from fastapi import APIRouter
import logging

import os
from typing import Optional

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import AiAnalysisLogModel, BotModel, SignalLogModel, TradeModel, get_db
from ..backtest_service import BacktestUnavailable, run_for_symbol
from ..exchanges.factory import build_exchange, map_timeframe_for_history
from ..bot_manager import manager
from ..common import FIXED_STAKE_USD

router = APIRouter()
log = logging.getLogger("main")


@router.post("/api/bots/{bot_id}/backtest")
async def run_bot_backtest(bot_id: int, db: Session = Depends(get_db)):
    """Backtest do bot: mesmo serviço do Scanner (backtest_service), com o símbolo, timeframe e parâmetros do bot."""
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
    try:
        return await run_for_symbol(bot.strategy_id, bot.symbol, bot.timeframe, bot.strategy_params or None)
    except BacktestUnavailable as exc:
        raise HTTPException(422, str(exc)) from exc

@router.get("/api/bots/{bot_id}/ai-analysis")
async def bot_ai_analysis(bot_id: int, db: Session = Depends(get_db)):
    import aiohttp as _aio
    
    bot = db.get(BotModel, bot_id)
    if not bot:
        raise HTTPException(404, "Bot não encontrado")
        
    status = manager.get_status(bot_id) or {}
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    
    if not api_key:
        return {"analysis": "A chave DEEPSEEK_API_KEY não está configurada no servidor."}

    direction = status.get("direction", 0)
    dir_str = "Comprado (Long)" if direction == 1 else "Vendido (Short)" if direction == -1 else "Aguardando entrada (Neutro)"
    
    active_trade = db.query(TradeModel).filter(
        TradeModel.bot_id == bot_id, 
        TradeModel.exit_price.is_(None), 
        TradeModel.type == "entry"
    ).order_by(TradeModel.id.desc()).first()
    
    trade_context = ""
    if direction != 0 and active_trade:
        import datetime
        now = datetime.datetime.utcnow()
        elapsed = now - active_trade.timestamp
        hours, rem = divmod(elapsed.total_seconds(), 3600)
        minutes, _ = divmod(rem, 60)
        
        last_price = status.get('last_price', 0)
        entry_price = active_trade.entry_price or 1
        pnl_pct = ((last_price - entry_price) / entry_price) * 100
        if direction == -1: pnl_pct = -pnl_pct
        
        trade_context = (
            f"Tempo em Operação: {int(hours)}h {int(minutes)}m\n"
            f"PnL Aberto (Evolução desde a entrada): {pnl_pct:+.2f}%\n"
        )
    
    # Prepara o prompt para a IA
    prompt = (
        "Você é um módulo de IA de diagnóstico algorítmico da plataforma CryptoIntelligence.\n"
        "Com base nos dados abaixo, forneça um relatório técnico, direto e estritamente profissional "
        "sobre o estado atual do bot em 1 parágrafo (máximo 4 frases).\n"
        "REGRAS:\n"
        "1. Vá direto ao ponto. NUNCA inicie com saudações (como 'Olá', 'Beleza', 'Aqui está').\n"
        "2. Seja estritamente descritivo e quantitativo. Não use tom conversacional nem adjetivos desnecessários.\n"
        "3. Apenas reporte o que o algoritmo está fazendo, o tempo de operação, a evolução (PnL) e as distâncias matemáticas.\n"
        "4. NUNCA dê conselhos humanos, nem mande o usuário 'ficar atento' ou 'ficar de olho'.\n\n"
        f"Robô: {bot.name} ({bot.symbol})\n"
        f"Estratégia: {bot.strategy_id}\n"
        f"Estado Atual: {dir_str}\n"
        f"Último Preço: {status.get('last_price', 'N/A')}\n"
        f"Preço de Entrada: {status.get('entry_price', 'N/A')}\n"
        f"Take Profit: {status.get('tp1_price', 'N/A')}\n"
        f"Stop Loss: {status.get('sl_price', 'N/A')}\n"
        f"{trade_context}"
    )

    try:
        async with _aio.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       "deepseek-chat",
                    "messages":    [{"role": "user", "content": prompt}],
                    "max_tokens":  350,
                    "temperature": 0.7,
                },
                timeout=_aio.ClientTimeout(total=20),
            ) as r:
                data = await r.json()
                text = data["choices"][0]["message"]["content"].strip()
                
                # Save to database
                log_entry = AiAnalysisLogModel(
                    bot_id=bot_id,
                    prompt=prompt,
                    response=text
                )
                db.add(log_entry)
                db.commit()
                
                return {"analysis": text}
    except Exception as exc:
        return {"analysis": f"Erro de conexão com a IA: {exc}"}

@router.get("/api/bots/{bot_id}/graph")
def bot_graph(bot_id: int):
    """Retorna o estado atual do grafo de correlação do bot."""
    inst = manager._instances.get(bot_id)
    if inst is None:
        return {"regime": None, "nodes": [], "edges": [], "metrics": {}}
    state = inst._graph_state
    if state is None:
        return {"regime": None, "nodes": [], "edges": [], "metrics": {}}
    return state

@router.get("/api/bots/{bot_id}/graph/interpret")
async def graph_interpret(bot_id: int, db: Session = Depends(get_db)):
    """Gera interpretação em linguagem natural do grafo via DeepSeek (com fallback por regras)."""
    import aiohttp as _aio

    inst = manager._instances.get(bot_id)
    if inst is None or inst._graph_state is None:
        return {"interpretation": None, "source": "unavailable"}

    state     = inst._graph_state
    regime    = state.get("regime",  {})
    metrics   = state.get("metrics", {})
    sentiment = state.get("sentiment")

    regime_name = regime.get("name", "neutral")
    regime_conf = regime.get("confidence", 0)
    bars        = regime.get("bars_in_regime", 0)
    density     = metrics.get("graph_density", 0)
    c_btc       = metrics.get("centrality_btc", 0)
    c_eth       = metrics.get("centrality_eth", 0)
    eth_leads   = metrics.get("eth_leads", False)
    n_comm      = metrics.get("n_communities", 0)
    n_edges     = metrics.get("n_edges", 0)

    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()

    if not api_key:
        text = _rule_based_interpretation(
            regime_name, regime_conf, bars,
            density, c_btc, c_eth, eth_leads, n_comm, n_edges, sentiment,
        )
        return {"interpretation": text, "source": "rules"}

    eth_lead_str = (
        "ETH está liderando BTC (sinal antecipado)"
        if eth_leads else
        "BTC está em posição dominante na rede"
    )
    sent_str = (
        f"Sentiment FinGPT: {sentiment:+.2f}"
        if sentiment is not None else
        "Sentiment externo: não disponível"
    )

    prompt = (
        "Você é um especialista em análise técnica e estrutura de mercado de criptoativos. "
        "Analise os dados do grafo de correlação abaixo e escreva UM parágrafo sucinto (3-5 frases) "
        "em português, explicando o que esse estado de rede significa para o BTC agora e qual o "
        "comportamento esperado do mercado. Seja objetivo, técnico mas acessível. "
        "Não use bullets nem títulos, apenas prosa corrida.\n\n"
        f"Estado do grafo:\n"
        f"- Regime: {regime_name} (confiança: {regime_conf * 100:.0f}%, há {bars} barra(s) neste regime)\n"
        f"- Densidade da rede: {density:.4f} ({n_edges} de 10 arestas possíveis ativas)\n"
        f"- Centralidade BTC: {c_btc:.4f}\n"
        f"- Centralidade ETH: {c_eth:.4f}\n"
        f"- {eth_lead_str}\n"
        f"- Comunidades detectadas: {n_comm}\n"
        f"- {sent_str}\n"
    )

    try:
        async with _aio.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       "deepseek-chat",
                    "messages":    [{"role": "user", "content": prompt}],
                    "max_tokens":  300,
                    "temperature": 0.65,
                },
                timeout=_aio.ClientTimeout(total=20),
            ) as r:
                data = await r.json()
                text = data["choices"][0]["message"]["content"].strip()
                
                # Save to database
                # Save to database — importação correta para o pacote
                from ..database import AiAnalysisLogModel as AiLog
                log_entry = AiLog(
                    bot_id=bot_id,
                    prompt=prompt,
                    response=text
                )
                db.add(log_entry)
                db.commit()
                
                return {"interpretation": text, "source": "deepseek"}
    except Exception as exc:
        log.warning("DeepSeek interpret falhou: %s", exc)
        text = _rule_based_interpretation(
            regime_name, regime_conf, bars,
            density, c_btc, c_eth, eth_leads, n_comm, n_edges, sentiment,
        )
        return {"interpretation": text, "source": "rules"}

def _rule_based_interpretation(
    regime_name: str, regime_conf: float, bars: int,
    density: float, c_btc: float, c_eth: float,
    eth_leads: bool, n_comm: int, n_edges: int,
    sentiment,
) -> str:
    """Interpretação baseada em regras — fallback sem DeepSeek."""
    regime_desc = {
        "trending": (
            f"O mercado encontra-se em regime trending com {regime_conf * 100:.0f}% de confiança "
            f"({bars} barra(s)), indicando que o BTC lidera a rede de correlação e há momentum "
            "direcional consistente — condição favorável para seguir o sinal das EMAs."
        ),
        "lagging": (
            f"O regime atual é lagging ({regime_conf * 100:.0f}% de confiança, {bars} barra(s)): "
            "o ETH apresenta maior centralidade de eigenvector que o BTC neste cluster, "
            "sugerindo que o Ethereum está antecipando o movimento e o Bitcoin tende a segui-lo "
            "em 1 a 3 barras — a estratégia opera com sensibilidade aumentada."
        ),
        "neutral": (
            f"O mercado está em regime neutral ({regime_conf * 100:.0f}% de confiança, "
            f"{bars} barra(s)), sem liderança clara entre os ativos da rede. "
            "A estratégia exige crossover de EMA muito limpo antes de abrir posição."
        ),
        "transition": (
            f"O regime acaba de mudar (transição detectada há {bars} barra(s)), "
            "indicando instabilidade estrutural recente na rede de correlação. "
            "O bot mantém hold por segurança até o novo regime se consolidar."
        ),
        "chaos": (
            f"O mercado está em caos — densidade de rede {density:.2f} abaixo do limiar mínimo, "
            f"com apenas {n_edges} de 10 arestas ativas. "
            "Não há estrutura de correlação confiável; bot em modo de espera total, sem sinais emitidos."
        ),
    }.get(regime_name, "Regime indefinido — aguardando dados suficientes.")

    parts = [regime_desc]

    if density >= 0.8:
        parts.append(
            f"A rede está altamente sincronizada ({n_edges}/10 arestas ativas), "
            "refletindo correlação forte entre todos os principais ativos — "
            "típico de mercados dominados por sentimento macro global."
        )
    elif density >= 0.5:
        parts.append(
            f"A rede apresenta correlação moderada ({n_edges}/10 arestas), "
            "com alguma diversificação de comportamento entre os ativos."
        )

    if eth_leads:
        parts.append(
            "ETH lidera BTC neste cluster: historicamente, isso antecipa "
            "movimentos do Bitcoin em 1 a 3 barras — sinal antecipado ativo."
        )

    if sentiment is not None:
        if sentiment > 0.6:
            parts.append(
                f"O sentiment externo (FinGPT: {sentiment:+.2f}) confirma viés bullish, "
                "alinhado com o sinal do grafo."
            )
        elif sentiment < -0.6:
            parts.append(
                f"O sentiment externo (FinGPT: {sentiment:+.2f}) sinaliza forte pressão vendedora, "
                "potencialmente contrário ao sinal do grafo — posição bloqueada automaticamente."
            )

    return " ".join(parts)


# ── Rotas: Signal Logs / Optimização ─────────────────────────────────────────
_SIGNAL_MIN = 50  # sinais mínimos para habilitar análise

@router.get("/api/bots/{bot_id}/signal-logs/readiness")
def signal_logs_readiness(bot_id: int, db: Session = Depends(get_db)):
    """Retorna se há dados suficientes para análise de optimização."""
    from sqlalchemy import func
    signal_count = db.query(func.count(SignalLogModel.id)).filter(
        SignalLogModel.bot_id == bot_id).scalar() or 0
    linked_count = db.query(func.count(SignalLogModel.id)).filter(
        SignalLogModel.bot_id == bot_id,
        SignalLogModel.resulted_in_trade_id.isnot(None),
    ).scalar() or 0
    return {
        "ready":    signal_count >= _SIGNAL_MIN,
        "signal_count":          signal_count,
        "signal_count_required": _SIGNAL_MIN,
        "linked_trades":         linked_count,
    }

@router.get("/api/bots/{bot_id}/signal-logs/analysis")
def signal_logs_analysis(bot_id: int, db: Session = Depends(get_db)):
    """Análise estatística dos sinais para sugestões de optimização."""
    import statistics as _stats
    from sqlalchemy import func

    signal_count = db.query(func.count(SignalLogModel.id)).filter(
        SignalLogModel.bot_id == bot_id).scalar() or 0
    if signal_count < _SIGNAL_MIN:
        raise HTTPException(400, f"Dados insuficientes: {signal_count}/{_SIGNAL_MIN} sinais")

    # Distribuição de sinais
    dist_rows = db.query(SignalLogModel.signal, func.count(SignalLogModel.id)).filter(
        SignalLogModel.bot_id == bot_id
    ).group_by(SignalLogModel.signal).all()
    signal_dist = {s: c for s, c in dist_rows}

    # Top motivos de HOLD
    hold_rows = db.query(SignalLogModel.hold_reason, func.count(SignalLogModel.id)).filter(
        SignalLogModel.bot_id == bot_id,
        SignalLogModel.signal == "hold",
        SignalLogModel.hold_reason.isnot(None),
    ).group_by(SignalLogModel.hold_reason).order_by(
        func.count(SignalLogModel.id).desc()
    ).limit(5).all()
    top_hold_reasons = [{"reason": r, "count": c} for r, c in hold_rows]

    # Análise de trades ligados a sinais
    linked = db.query(SignalLogModel).filter(
        SignalLogModel.bot_id == bot_id,
        SignalLogModel.resulted_in_trade_id.isnot(None),
    ).all()

    trade_analysis = None
    if linked:
        trade_ids = [s.resulted_in_trade_id for s in linked]
        trades_by_id = {t.id: t for t in db.query(TradeModel).filter(
            TradeModel.id.in_(trade_ids),
            TradeModel.pnl.isnot(None),
        ).all()}

        wins, losses = [], []
        for sig in linked:
            trade = trades_by_id.get(sig.resulted_in_trade_id)
            if trade and trade.pnl is not None:
                (wins if trade.pnl > 0 else losses).append(sig)

        # Comparação de indicadores: vencedores vs perdedores
        all_inds = set()
        for s in wins + losses:
            if s.indicators:
                all_inds.update(s.indicators.keys())

        indicator_comparison = {}
        for ind in sorted(all_inds):
            win_vals  = [s.indicators[ind] for s in wins  if s.indicators and ind in s.indicators]
            lose_vals = [s.indicators[ind] for s in losses if s.indicators and ind in s.indicators]
            if win_vals or lose_vals:
                indicator_comparison[ind] = {
                    "win_avg":   round(_stats.mean(win_vals),  4) if win_vals  else None,
                    "lose_avg":  round(_stats.mean(lose_vals), 4) if lose_vals else None,
                    "win_count": len(win_vals),
                    "lose_count": len(lose_vals),
                }

        # Win rate por regime (se meta tiver regime_state)
        regime_stats: dict = {}
        for sig in linked:
            trade = trades_by_id.get(sig.resulted_in_trade_id)
            if not (trade and sig.meta and "regime_state" in sig.meta):
                continue
            regime = sig.meta["regime_state"]
            bucket = regime_stats.setdefault(regime, {"wins": 0, "losses": 0})
            if trade.pnl and trade.pnl > 0:
                bucket["wins"] += 1
            elif trade.pnl is not None:
                bucket["losses"] += 1
        for bucket in regime_stats.values():
            total = bucket["wins"] + bucket["losses"]
            bucket["win_rate"] = round(bucket["wins"] / total * 100, 1) if total else 0
            bucket["total"] = total

        total_linked = len(wins) + len(losses)
        trade_analysis = {
            "total_linked": total_linked,
            "wins":         len(wins),
            "losses":       len(losses),
            "win_rate":     round(len(wins) / total_linked * 100, 1) if total_linked else 0,
            "indicator_comparison": indicator_comparison,
            "regime_stats": regime_stats,
        }

    suggestions = _optimization_suggestions(signal_dist, top_hold_reasons, trade_analysis, signal_count)

    return {
        "signal_count":       signal_count,
        "signal_distribution": signal_dist,
        "top_hold_reasons":   top_hold_reasons,
        "trade_analysis":     trade_analysis,
        "suggestions":        suggestions,
    }

def _optimization_suggestions(signal_dist: dict, hold_reasons: list,
                               trade_analysis: Optional[dict], signal_count: int) -> list[str]:
    tips = []
    total = sum(signal_dist.values()) or 1
    hold_pct  = signal_dist.get("hold", 0) / total * 100
    entry_pct = 100 - hold_pct

    if hold_pct > 85:
        tips.append(
            f"O bot está em HOLD {hold_pct:.0f}% do tempo — critérios de entrada muito restritivos. "
            "Considere relaxar os thresholds dos indicadores."
        )
    elif hold_pct < 30:
        tips.append(
            f"O bot entra em posição {entry_pct:.0f}% do tempo — parâmetros muito permissivos, "
            "aumentando o risco de sinais de baixa qualidade."
        )

    if hold_reasons:
        top_reason = hold_reasons[0]["reason"]
        top_count  = hold_reasons[0]["count"]
        tips.append(
            f"Motivo de HOLD mais frequente: '{top_reason}' ({top_count}×). "
            "Ajustar o parâmetro correspondente pode aumentar o número de entradas."
        )

    if trade_analysis:
        wr = trade_analysis["win_rate"]
        tl = trade_analysis["total_linked"]
        if tl < 5:
            tips.append(
                f"Apenas {tl} trade(s) vinculados a sinais — acumule mais dados antes de ajustar parâmetros."
            )
        elif wr >= 60:
            tips.append(
                f"Win rate de {wr}% é sólido. Mantenha o stake fixo de US$100 e ajuste apenas critérios/ativos."
            )
        elif wr < 40:
            tips.append(
                f"Win rate de {wr}% está abaixo de 40%. Revise os critérios de entrada ou pause o bot temporariamente."
            )

        regime_stats = trade_analysis.get("regime_stats", {})
        if regime_stats:
            best  = max(regime_stats.items(), key=lambda x: x[1].get("win_rate", 0))
            worst = min(regime_stats.items(), key=lambda x: x[1].get("win_rate", 0))
            if best[1]["total"] >= 3:
                tips.append(
                    f"Regime '{best[0]}' teve win rate de {best[1]['win_rate']}% "
                    f"({best[1]['total']} trades) — melhor contexto para operar."
                )
            if worst[1]["total"] >= 3 and worst[1]["win_rate"] < 35:
                tips.append(
                    f"Evite entradas em regime '{worst[0]}' "
                    f"(win rate {worst[1]['win_rate']}% em {worst[1]['total']} trades)."
                )

    if not tips:
        tips.append(
            f"Com {signal_count} sinais registrados os padrões ainda são inconclusivos. "
            "Continue acumulando dados para recomendações mais robustas."
        )
    return tips
