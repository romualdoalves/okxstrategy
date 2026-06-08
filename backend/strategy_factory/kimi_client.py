"""
strategy_factory/llm_client.py (importado como kimi_client para manter compatibilidade)

Cliente HTTP para geração de código via LLM OpenAI-compatible.
Suporta DeepSeek (padrão), OpenAI, Moonshot e qualquer API compatível.

Configuração via variáveis de ambiente:
  FACTORY_LLM_KEY      — chave da API (obrigatória)
                         Fallback: usa DEEPSEEK_API_KEY se disponível
  FACTORY_LLM_BASE_URL — endpoint (default: https://api.deepseek.com)
  FACTORY_LLM_MODEL    — modelo  (default: deepseek-chat)

Nota sobre KIMI Code (sk-kimi-*):
  A API KIMI Code é restrita a Coding Agents (Claude Code, Kimi CLI, etc.)
  e retorna 403 quando chamada diretamente por aplicações.
  Use DeepSeek ou a Moonshot Platform (api.moonshot.cn/v1) no lugar.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp

log = logging.getLogger("strategy_factory.llm")


def _api_key() -> str:
    # Tenta FACTORY_LLM_KEY primeiro, depois DEEPSEEK_API_KEY como fallback
    key = (os.getenv("FACTORY_LLM_KEY", "")
           or os.getenv("DEEPSEEK_API_KEY", "")).strip()
    if not key:
        raise RuntimeError(
            "Nenhuma chave LLM configurada para a Fábrica de Estratégias. "
            "Adicione FACTORY_LLM_KEY (ou DEEPSEEK_API_KEY) ao .env do VPS."
        )
    return key


def _base_url() -> str:
    return os.getenv("FACTORY_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")


def _model() -> str:
    return os.getenv("FACTORY_LLM_MODEL", "deepseek-chat")


def get_model() -> str:
    return _model()


def get_base_url() -> str:
    return _base_url()


def get_provider() -> str:
    """Retorna o nome amigável do provedor para exibição na UI."""
    url = _base_url()
    if "deepseek" in url:
        return "DeepSeek"
    if "moonshot" in url or "kimi" in url:
        return "Moonshot/Kimi"
    if "openai" in url:
        return "OpenAI"
    return "LLM"


async def chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    session: aiohttp.ClientSession | None = None,
    response_format: str | None = None,
) -> str:
    """
    Envia uma lista de mensagens para o LLM configurado e devolve o texto da resposta.
    Cria uma sessão temporária se nenhuma for passada.
    """
    base  = _base_url()
    model = _model()
    key   = _api_key()

    payload: dict[str, Any] = {
        "model":       model,
        "messages":    messages,
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }
    if response_format == "json":
        payload["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
    }

    url = f"{base}/chat/completions"
    log.debug("LLM → POST %s  model=%s", url, model)

    async def _do(sess: aiohttp.ClientSession) -> str:
        async with sess.post(
            url,
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(
                    f"LLM API erro {resp.status} "
                    f"(endpoint={base}, model={model}): {body[:400]}"
                )
            data = await resp.json()
            return data["choices"][0]["message"]["content"]

    if session:
        return await _do(session)
    async with aiohttp.ClientSession() as s:
        return await _do(s)


def is_configured() -> bool:
    return bool(
        os.getenv("FACTORY_LLM_KEY", "").strip()
        or os.getenv("DEEPSEEK_API_KEY", "").strip()
    )
