"""
strategy_factory/planner.py — Etapa 1 da fábrica: descrição → Plano JSON.

Envia o texto do usuário para a KIMI com um prompt de sistema detalhado
que instrui o modelo a produzir um plano de implementação estruturado.
"""
from __future__ import annotations

import json
import logging

from . import kimi_client

log = logging.getLogger("strategy_factory.planner")

_SYSTEM_PROMPT = """Você é um especialista em trading quantitativo e desenvolvimento Python especializado na construção de estratégias algorítmicas para o framework OKXStrategy.

Sua tarefa é analisar a descrição de uma estratégia de trading e produzir um PLANO DE IMPLEMENTAÇÃO em JSON estruturado.

## Contrato do Framework OKXStrategy

### BaseStrategy — interface obrigatória:
- `info()` → StrategyInfo(id, name, description, tags, recommended_timeframe, params)
- `compute_with_context(candles, context)` → StrategyResult
- `set_params(params: dict)` → None
- Opcional: `extra_timeframes()` → list[str] para multi-TF

### StrategyResult — campos:
- signal: "buy" | "sell" | "hold"
- indicators: dict[str, float] — todos os valores numéricos a transmitir
- metadata: dict — deve conter sl_price, tp1_price, sl_pct, tp1_pct
- hold_reason: str — motivo do HOLD (vazio se BUY/SELL)
- criteria_met: int — quantos critérios de entrada estão atendidos
- criteria_total: int — total de critérios independentes de entrada

### Regras de implementação:
1. Usar SOMENTE pandas e pandas_ta para indicadores técnicos
2. SEMPRE validar aquecimento: retornar HOLD se len(candles) < min_candles
3. criteria_met deve incrementar a cada condição satisfeita (não apenas ao gerar o sinal)
4. criteria_total deve corresponder ao número de condições independentes de entrada
5. Indicadores disponíveis em pandas_ta: ema, sma, rsi, macd, atr, bbands, adx, stoch, vwap, obv, chop, donchian
6. CandleBar possui: open, high, low, close, volume (todos float)
7. PARÂMETROS OBRIGATÓRIOS em toda estratégia (inclua sempre no plano):
   - atr_period (int, default=14): período do ATR para gestão de risco
   - sl_mult (float, default=2.0): multiplicador ATR para Stop Loss
   - tp1_rr (float, default=2.0): relação risco-retorno do TP1
   - ts_mult (float, default=3.0): multiplicador ATR para Trailing Stop após TP1
8. INDICADORES OBRIGATÓRIOS no plano (inclua sempre em "indicators"):
   - atr: o bot_manager usa indicators["atr"] para calcular o callback do Trailing Stop dinâmico

## IDs e Categorias Semânticas
## A Fábrica IA analisa a descrição e classifica a estratégia na categoria correta.
## O sistema atribuirá o número sequencial — use o prefixo da categoria no plano.
##
## CATEGORIAS SEMÂNTICAS (único sistema de IDs):
##   TF = Trend Following     — seguir a tendência (EMA cross, MACD, momentum)
##   MR = Mean Reversion      — reversão à média (Bollinger fade, RSI extreme)
##   PA = Price Action        — ação do preço (pivots, CHoCH, padrões)
##   SC = Scalping            — execução rápida (5m-15m, microstructure)
##   RG = Regime              — estado de mercado (Markov, CHOP, classificação)
##   IF = Information         — dados externos (on-chain, DEX, order flow)
##   NW = Network             — relações entre ativos (correlação, lead-lag)
##   T  = Test                — estratégia de teste da plataforma
##
## NÃO EXISTE mais prefixo F ou FX. Todas as estratégias usam categorias semânticas.
## Exemplo: uma estratégia de EMA crossover → category "TF", id "TF014"

## Formato de Saída — JSON OBRIGATÓRIO

Responda APENAS com JSON válido. Sem markdown, sem explicações, sem comentários fora do JSON.

{
  "id": "TF001",
  "name": "TF001 - Nome Curto (máx 40 chars)",
  "description": "Descrição completa da estratégia (2-4 frases)",
  "category": "TF",
  "tags": ["trend"],
  "recommended_timeframe": "15m",
  "extra_timeframes": [],
  "min_candles": 60,
  "params": [
    {
      "name": "ema_period",
      "type": "int",
      "default": 10,
      "min": 5,
      "max": 50,
      "step": 1,
      "description": "Período da EMA principal"
    }
  ],
  "indicators": [
    {"key": "bias", "description": "1=alta, -1=baixa, 0=neutro"},
    {"key": "close", "description": "Preço de fechamento"},
    {"key": "atr", "description": "ATR — OBRIGATÓRIO para o Trailing Stop dinâmico do bot_manager"}
  ],
  "criteria": [
    {"id": "c1_bias", "label": "C1 Viés", "description": "EMA macro define direção"}
  ],
  "entry_buy": "pseudocódigo da condição de compra",
  "entry_sell": "pseudocódigo da condição de venda",
  "hold_reasons": ["Mercado sem direção clara", "Aguardando gatilho de entrada"],
  "sl_method": "candle_extreme",
  "sl_mult": 2.0,
  "tp_method": "fixed_rr",
  "tp_rr": 2.0,
  "criteria_total": 1,
  "interpretation_notes": "Observações sobre a interpretação da estratégia descrita"
}

## Valores válidos:
- recommended_timeframe: "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "1d", "1w"
- extra_timeframes: subconjunto de ["5m","15m","30m","1H","2H","4H","6H","1D","1W"]
- tags (máx 4): "trend", "momentum", "reversal", "multi-tf", "swing", "scalp", "pattern", "volume", "statistical", "intraday", "breakout", "mean-reversion"
- category: "TF" | "MR" | "PA" | "SC" | "RG" | "IF" | "NW" | "T"
  A Fábrica IA DEVE escolher a categoria correta com base na descrição:
  - TF: se a estratégia segue tendência (EMA, MACD, momentum, breakout)
  - MR: se aposta em reversão à média (Bollinger, RSI extreme, desvio)
  - PA: se baseia em padrões de preço (pivots, CHoCH, suporte/resistência)
  - SC: se é de curto prazo rápido (scalp, 5m, opening range)
  - RG: se classifica estado de mercado antes de entrar (Markov, filtros)
  - IF: se usa dados externos (on-chain, DEX, whale flow, eventos)
  - NW: se analisa relações entre múltiplos ativos (correlação, grafos)
  - T:  se é uma estratégia de teste/demonstração
  NUNCA use F, FX, B, A, S, I, M ou qualquer outro prefixo.
- sl_method: "candle_extreme" | "atr_multiple" | "fixed_pct"
- tp_method: "fixed_rr" | "atr_multiple"
- criteria_total DEVE ser igual ao len(criteria)
- min_candles: número inteiro entre 20 e 300
"""


async def generate_plan(description: str) -> dict:
    """
    Recebe texto livre descrevendo a estratégia e retorna um dict com o plano.
    Lança RuntimeError se a KIMI não estiver configurada ou retornar JSON inválido.
    """
    log.info("Gerando plano para estratégia (%.0f chars)", len(description))

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": f"Analise e gere o plano JSON para esta estratégia:\n\n{description}"},
    ]

    try:
        raw = await kimi_client.chat(
            messages,
            temperature=0.15,
            max_tokens=2048,
            response_format="json",
        )
    except Exception as e:
        raise RuntimeError(f"Erro ao conectar com a API da IA: {e}")

    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as e:
        # Tenta extrair JSON do bloco caso o modelo tenha adicionado markdown
        import re
        match = re.search(r"\{[\s\S]+\}", raw)
        if match:
            try:
                plan = json.loads(match.group())
            except json.JSONDecodeError as e2:
                raise RuntimeError(f"KIMI retornou JSON inválido mesmo após extração: {e2}\n\nResposta: {raw[:500]}")
        else:
            raise RuntimeError(f"KIMI retornou JSON inválido: {e}\n\nResposta: {raw[:500]}")

    _validate_plan_schema(plan)
    log.info("Plano gerado: id=%s, name=%s, criteria=%d", plan.get("id"), plan.get("name"), plan.get("criteria_total", 0))
    return plan


_REQUIRED_PARAMS = {
    "atr_period": {"type": "int",   "default": 14,  "min": 5,   "max": 30,  "step": 1,   "description": "Período do ATR para gestão de risco"},
    "sl_mult":    {"type": "float", "default": 2.0, "min": 0.5, "max": 4.0, "step": 0.1, "description": "Multiplicador ATR para Stop Loss"},
    "tp1_rr":     {"type": "float", "default": 2.0, "min": 1.0, "max": 5.0, "step": 0.5, "description": "Relação risco-retorno do TP1"},
    "ts_mult":    {"type": "float", "default": 3.0, "min": 1.5, "max": 6.0, "step": 0.5, "description": "Multiplicador ATR para Trailing Stop (após TP1)"},
}

_REQUIRED_INDICATORS = {
    "atr":   "ATR — OBRIGATÓRIO: bot_manager usa indicators['atr'] para Trailing Stop dinâmico",
    "close": "Preço de fechamento atual",
}


def _validate_plan_schema(plan: dict) -> None:
    """Valida campos obrigatórios do plano e injeta parâmetros/indicadores faltantes."""
    required = ["name", "description", "recommended_timeframe", "params",
                "indicators", "criteria", "entry_buy", "entry_sell",
                "sl_method", "tp_method", "tp_rr", "criteria_total", "min_candles"]
    missing = [f for f in required if f not in plan]
    if missing:
        raise RuntimeError(f"Plano incompleto — campos ausentes: {missing}")
    if not isinstance(plan.get("params"), list):
        raise RuntimeError("plan.params deve ser uma lista")
    if not isinstance(plan.get("criteria"), list):
        raise RuntimeError("plan.criteria deve ser uma lista")
    if plan.get("criteria_total", 0) != len(plan.get("criteria", [])):
        plan["criteria_total"] = len(plan.get("criteria", []))

    # Injeta parâmetros obrigatórios que o modelo possa ter omitido
    existing_param_names = {p["name"] for p in plan["params"]}
    for pname, pdef in _REQUIRED_PARAMS.items():
        if pname not in existing_param_names:
            plan["params"].append({"name": pname, **pdef})

    # Injeta indicadores obrigatórios que o modelo possa ter omitido
    existing_ind_keys = {i["key"] for i in plan.get("indicators", [])}
    for key, desc in _REQUIRED_INDICATORS.items():
        if key not in existing_ind_keys:
            plan.setdefault("indicators", []).append({"key": key, "description": desc})
