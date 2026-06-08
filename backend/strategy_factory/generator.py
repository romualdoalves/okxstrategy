"""
strategy_factory/generator.py — Etapa 2 da fábrica: Plano JSON → código Python.

Recebe o plano aprovado e pede para a KIMI gerar o código da estratégia
seguindo o template exato do framework OKXStrategy.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from . import kimi_client

log = logging.getLogger("strategy_factory.generator")

# Exemplos reais enviados ao modelo como referência de estilo
_EXAMPLE_SIMPLE = """\
# EXEMPLO DE ESTRATÉGIA SIMPLES (B002 - RSI + MA):
# OBSERVE: atr OBRIGATÓRIO em indicators; ts_pct OBRIGATÓRIO em metadata.
# O bot_manager usa indicators["atr"] para calcular o callback do trailing stop (SW-TS).
class RsiMaStrategy(BaseStrategy):
    @classmethod
    def info(cls) -> StrategyInfo:
        return StrategyInfo(
            id="B002", name="B002 - RSI + Moving Average",
            description="...",
            tags=["momentum", "reversal"],
            recommended_timeframe="1h",
            criteria=[
                {"id": "c1_rsi", "label": "RSI na zona", "description": "RSI na zona de sobrevenda ou sobrecompra"},
                {"id": "c2_ma", "label": "MA confirma", "description": "Preço alinhado com a direção da MA"},
            ],
            params={
                "rsi_period":  ParamDef(type="int",   default=14,  min=5,   max=30,  step=1,   description="Período do RSI"),
                "rsi_os":      ParamDef(type="float", default=32.0, min=20.0, max=45.0, step=1.0, description="Zona de sobrevenda"),
                "rsi_ob":      ParamDef(type="float", default=68.0, min=55.0, max=80.0, step=1.0, description="Zona de sobrecompra"),
                "ma_period":   ParamDef(type="int",   default=200, min=50,  max=500, step=10,  description="Período da MA de tendência"),
                "atr_period":  ParamDef(type="int",   default=14,  min=5,   max=30,  step=1,   description="Período do ATR para gestão de risco"),
                "sl_mult":     ParamDef(type="float", default=1.5, min=0.5, max=4.0, step=0.1, description="Multiplicador ATR para Stop Loss"),
                "tp1_rr":      ParamDef(type="float", default=2.0, min=1.0, max=5.0, step=0.5, description="R:R do TP1"),
                "ts_mult":     ParamDef(type="float", default=3.0, min=1.5, max=6.0, step=0.5, description="Multiplicador ATR para Trailing Stop (após TP1)"),
            },
        )

    def __init__(self):
        p = self.info().params
        self.rsi_period = p["rsi_period"].default
        self.rsi_os     = p["rsi_os"].default
        self.rsi_ob     = p["rsi_ob"].default
        self.ma_period  = p["ma_period"].default
        self.atr_period = p["atr_period"].default
        self.sl_mult    = p["sl_mult"].default
        self.tp1_rr     = p["tp1_rr"].default
        self.ts_mult    = p["ts_mult"].default

    def set_params(self, params: dict) -> None:
        for k, v in params.items():
            if hasattr(self, k): setattr(self, k, v)

    def compute(self, candles: list) -> Optional[StrategyResult]:
        return self.compute_with_context(candles, None)

    def compute_with_context(self, candles, context=None):
        min_c = self.ma_period + 5
        if len(candles) < min_c:
            return StrategyResult(signal=Signal.HOLD,
                hold_reason=f"Aquecendo — aguardando {min_c} velas ({len(candles)} recebidas)",
                indicators={"close": float(candles[-1].close), "rsi": 50.0, "ma": 0.0, "atr": 0.0})

        df = pd.DataFrame([{"high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles])
        rsi_s = ta.rsi(df["close"], length=self.rsi_period)
        ma_s  = ta.sma(df["close"], length=self.ma_period)
        atr_s = ta.atr(df["high"], df["low"], df["close"], length=self.atr_period)
        if rsi_s is None or ma_s is None or atr_s is None: return None

        rsi = float(rsi_s.iloc[-1]); ma = float(ma_s.iloc[-1])
        atr = float(atr_s.iloc[-1]); close = float(candles[-1].close)

        criteria_met = 0
        criteria_total = 2

        if rsi <= self.rsi_os or rsi >= self.rsi_ob:
            criteria_met += 1
        if (rsi <= self.rsi_os and close > ma) or (rsi >= self.rsi_ob and close < ma):
            criteria_met += 1

        hold_reason = ""
        if rsi <= self.rsi_os and close > ma:   signal = Signal.BUY
        elif rsi >= self.rsi_ob and close < ma: signal = Signal.SELL
        else:
            signal = Signal.HOLD
            hold_reason = "RSI neutro" if criteria_met == 0 else "MA não confirma direção"

        sl_dist = atr * self.sl_mult
        if signal == Signal.BUY:
            sl_price = round(close - sl_dist, 4); tp1_price = round(close + sl_dist * self.tp1_rr, 4)
        else:
            sl_price = round(close + sl_dist, 4); tp1_price = round(close - sl_dist * self.tp1_rr, 4)

        return StrategyResult(
            signal=signal, hold_reason=hold_reason,
            # "atr" é OBRIGATÓRIO — o bot_manager usa para calcular o callback do trailing stop
            indicators={"rsi": round(rsi, 2), "ma": round(ma, 2), "atr": round(atr, 6), "close": round(close, 4)},
            metadata={
                "sl_price":  sl_price,
                "tp1_price": tp1_price,
                "sl_pct":    round(sl_dist / close, 5),
                "tp1_pct":   round(sl_dist * self.tp1_rr / close, 5),
                # ts_pct é OBRIGATÓRIO — padrão de todas as estratégias nativas
                "ts_pct":    round(atr * self.ts_mult / close, 5),
            },
            criteria_met=criteria_met, criteria_total=criteria_total,
        )
"""

_SYSTEM_PROMPT = f"""\
Você é um expert Python developer implementando estratégias de trading algorítmico.

Dado um plano de implementação JSON, gere o código Python COMPLETO e FUNCIONAL da estratégia.

## IMPORTS OBRIGATÓRIOS (use exatamente estes):
```python
from __future__ import annotations
from typing import Optional
import math
import pandas as pd
import pandas_ta as ta
from backend.strategies.base import BaseStrategy, ParamDef, Signal, StrategyInfo, StrategyResult
```

## REGRAS ESTRITAS:
1. Imports PERMITIDOS: apenas os acima (pandas, pandas_ta, math, typing, ..base)
2. PROIBIDO: subprocess, os, sys, socket, eval, exec, open(), __import__, requests, aiohttp
3. Nome da classe: {{ClassName}}Strategy (ClassName = CamelCase do nome sem prefixo ID)
4. SEMPRE checagem de aquecimento com hold_reason descritivo em português
5. criteria_met começa em 0, incrementa a cada condição de entrada satisfeita
6. criteria_total DEVE ser igual ao número EXATO de critérios de entrada da estratégia.
   Exemplo: se a estratégia verifica (1) tendência de alta, (2) pullback na EMA, (3) candle de força,
   então criteria_total = 3. NUNCA deixe criteria_total = 1 se há múltiplos critérios.
7. Todos os valores em result.indicators DEVEM ser float (cast explícito)
8. result.indicators DEVE conter "atr" (float) — o bot_manager lê indicators["atr"] a cada candle
   para alimentar _current_atr, que é usado para calcular o callback do Trailing Stop (SW-TS).
   Sem "atr" o trailing stop não funciona corretamente.
9. result.metadata DEVE conter TODOS: sl_price, tp1_price, sl_pct, tp1_pct, ts_pct
   ts_pct = round(atr * self.ts_mult / close, 5)  — padrão de todas as estratégias nativas
10. Parâmetros obrigatórios em TODA estratégia: atr_period (int, default=14), sl_mult (float),
    tp1_rr (float), ts_mult (float, default=3.0)
11. Docstring do módulo com nome, data de criação "Gerado pela Fábrica de Estratégias"
12. NÃO use valores hardcoded — todos os parâmetros devem vir de self.PARAM_NAME
13. StrategyInfo DEVE incluir `criteria` com a lista de critérios do plano. Exemplo:
    criteria=[
        {{"id": "c1_bias", "label": "Viés de tendência", "description": "EMA macro define direção"}},
        {{"id": "c2_pullback", "label": "Pullback", "description": "Preço retorna à EMA"}},
        {{"id": "c3_trigger", "label": "Gatilho", "description": "Candle de força confirma"}},
    ]

## EXEMPLO DE REFERÊNCIA:
{_EXAMPLE_SIMPLE}

## REGRA FINAL:
Gere APENAS o código Python. Sem markdown, sem ``` delimiters, sem texto explicativo.
O código deve começar com o docstring do módulo e terminar com a última linha da classe.
"""


async def generate_code(plan: dict) -> str:
    """
    Recebe o plano aprovado e retorna o código Python gerado pela KIMI.
    """
    plan = _normalize_plan_identity(plan)
    class_name = _plan_to_classname(plan)
    strategy_id = plan.get("id", "TF001")

    user_msg = f"""\
Implemente a estratégia com base no plano abaixo.
Nome da classe: {class_name}Strategy
ID da estratégia: {strategy_id}

REGRA CRÍTICA: O plano define {len(plan.get('criteria', []))} critérios. 
criteria_total DEVE ser EXATAMENTE {len(plan.get('criteria', []))}.
Para cada critério satisfeito, incremente criteria_met em 1.

PLANO:
{json.dumps(plan, ensure_ascii=False, indent=2)}
"""
    log.info("Gerando código para %s (%s)", strategy_id, class_name)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": user_msg},
    ]

    code = await kimi_client.chat(
        messages,
        temperature=0.05,
        max_tokens=8192,
    )

    # Remove delimitadores markdown se presentes
    code = _strip_markdown(code)
    _basic_code_check(code, class_name)

    # Corrige criteria_total e criteria automaticamente — a IA frequentemente ignora a regra
    plan_criteria = plan.get("criteria", [])
    plan_criteria_count = len(plan_criteria)
    if plan_criteria_count > 0:
        import re as _re
        # Corrige criteria_total
        code = _re.sub(
            r'criteria_total\s*=\s*\d+',
            f'criteria_total={plan_criteria_count}',
            code
        )
        # Injeta criteria no StrategyInfo se não existir
        if 'criteria=' not in code and plan_criteria:
            criteria_json = json.dumps(plan_criteria, ensure_ascii=False)
            # Procura o fechamento do params={...} no StrategyInfo
            code = _re.sub(
                r'(params=\{[^}]+\},?\s*)',
                r'\1\n            criteria=' + criteria_json + ',',
                code
            )
            log.info("criteria injetado automaticamente no StrategyInfo")
        log.info("criteria_total corrigido automaticamente para %d", plan_criteria_count)

    log.info("Código gerado: %.0f chars", len(code))
    return code


def _plan_to_classname(plan: dict) -> str:
    """Converte o nome do plano para CamelCase."""
    name = plan.get("name", "Factory001")
    strategy_id = str(plan.get("id", "")).upper()
    if strategy_id:
        name = re.sub(rf"^{re.escape(strategy_id)}\s*[-–]\s*", "", name, flags=re.IGNORECASE)
    # Remove qualquer prefixo ID legado ou divergente (ex: "TF001 - ", "RG003 - ").
    name = re.sub(r"^[A-Z]{1,2}\d{3}\s*[-–]\s*", "", name)
    # Remove caracteres especiais, capitaliza palavras
    words = re.sub(r"[^a-zA-ZÀ-ú0-9\s]", "", name).split()
    return "".join(w.capitalize() for w in words if w) or "Factory"


def _normalize_plan_identity(plan: dict) -> dict:
    """Mantém ID e prefixo do nome alinhados antes de chamar o LLM."""
    normalized = dict(plan)
    strategy_id = str(normalized.get("id", "")).upper().strip()
    name = str(normalized.get("name", "")).strip()
    if strategy_id and name:
        if re.match(r"^[A-Z]{1,2}\d{3}\s*[-–]\s*", name):
            normalized["name"] = re.sub(
                r"^[A-Z]{1,2}\d{3}\s*([-–]\s*)",
                rf"{strategy_id} \1",
                name,
                count=1,
            )
        elif not name.upper().startswith(strategy_id):
            normalized["name"] = f"{strategy_id} - {name}"
    return normalized


async def fix_code(code: str, errors: list[str], plan: dict) -> str:
    """
    Corrige cirurgicamente o código existente sem reescrever do zero.
    Recebe o código atual + lista de erros de validação específicos.
    """
    plan = _normalize_plan_identity(plan)
    class_name = _plan_to_classname(plan)
    errors_text = "\n".join(f"- {e}" for e in errors)

    user_msg = f"""\
O código Python abaixo passou em quase todas as verificações de validação, mas FALHOU nos erros listados.
Faça APENAS as correções necessárias para resolver esses erros específicos.
NÃO reescreva o código do zero. NÃO altere a lógica, parâmetros ou estrutura que já funciona.
Modifique SOMENTE o que é necessário para corrigir os erros listados.

## ERROS A CORRIGIR:
{errors_text}

## LEMBRETES CRÍTICOS (verifique se já estão corretos antes de modificar):
- indicators DEVE conter "atr" como float (obrigatório para Trailing Stop do bot_manager)
- metadata DEVE conter: sl_price, tp1_price, sl_pct, tp1_pct, ts_pct
- ts_pct = round(atr * self.ts_mult / close, 5)
- criteria_total DEVE ser igual ao número EXATO de critérios da estratégia. Se há 3 critérios (ex: tendência, pullback, gatilho), criteria_total DEVE ser 3, NUNCA 1.
- Se a estratégia precisar de timeframe superior (ex: EMA macro), use extra_timeframes() e context["extra_candles"]
- Sem dados de contexto extra, retorne HOLD com hold_reason descritivo (não gere sinal sem dados)

## CÓDIGO ATUAL (modifique somente o necessário):
{code}

Retorne APENAS o código Python corrigido. Sem markdown, sem explicações.
"""

    log.info("Corrigindo %d erro(s) no código existente", len(errors))

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user",   "content": user_msg},
    ]

    fixed = await kimi_client.chat(
        messages,
        temperature=0.05,
        max_tokens=8192,
    )

    fixed = _strip_markdown(fixed)
    _basic_code_check(fixed, class_name)

    # Corrige criteria_total automaticamente — a IA frequentemente ignora a regra
    plan_criteria_count = len(plan.get("criteria", []))
    if plan_criteria_count > 1:
        import re as _re
        fixed = _re.sub(
            r'criteria_total\s*=\s*\d+',
            f'criteria_total={plan_criteria_count}',
            fixed
        )
        log.info("criteria_total corrigido automaticamente para %d", plan_criteria_count)

    log.info("Código corrigido: %.0f chars", len(fixed))
    return fixed


def _strip_markdown(code: str) -> str:
    """Remove delimitadores ```python ... ``` do código."""
    code = re.sub(r"^```(?:python)?\n?", "", code.strip())
    code = re.sub(r"\n?```$", "", code)
    return code.strip()


def _basic_code_check(code: str, class_name: str) -> None:
    """Verificações básicas antes de prosseguir para o validador completo."""
    forbidden = ["subprocess", "os.system", "os.popen", "eval(", "exec(",
                 "__import__", "socket.", "requests.", "aiohttp."]
    for f in forbidden:
        if f in code:
            raise RuntimeError(f"Código gerado contém padrão proibido: '{f}'")

    if f"class {class_name}Strategy" not in code:
        raise RuntimeError(
            f"Código não contém a classe esperada '{class_name}Strategy'. "
            f"Verifique se o modelo gerou o nome correto."
        )
    if "def compute_with_context" not in code and "def compute(" not in code:
        raise RuntimeError("Código não contém método compute() ou compute_with_context()")
