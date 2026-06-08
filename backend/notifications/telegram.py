"""
Envia mensagens via Telegram Bot API.
Configuração: TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env
"""

import logging
import os

import aiohttp

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:

    def __init__(self):
        self.token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.token and self.chat_id)
        self.last_error = ""
        self.last_status = None
        self.last_response_body = ""
        if not self.enabled:
            log.debug("Telegram desabilitado — defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env")

    def _token_preview(self) -> str:
        if not self.token:
            return ""
        return f"{self.token[:6]}...{self.token[-4:]}"

    def _friendly_error(self, status: int, body: str) -> str:
        lower_body = body.lower()
        if "chat not found" in lower_body:
            return (
                f"Telegram recusou o chat_id {self.chat_id}: chat not found. "
                "Confirme que esse mesmo usuário abriu o bot configurado em TELEGRAM_BOT_TOKEN "
                "e enviou /start antes de testar novamente."
            )
        if "unauthorized" in lower_body:
            return "Telegram recusou o token do bot. Atualize TELEGRAM_BOT_TOKEN no .env do servidor."
        return f"Telegram HTTP {status}: {body[:200]}"

    async def send(self, text: str) -> bool:
        if not self.enabled:
            self.last_error = "Telegram desabilitado — defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env."
            return False
        url     = _API.format(token=self.token)
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=6),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        self.last_status = resp.status
                        self.last_response_body = body[:1000]
                        self.last_error = self._friendly_error(resp.status, body)
                        log.warning(
                            "Telegram HTTP %d chat_id=%s token=%s: %s",
                            resp.status,
                            self.chat_id,
                            self._token_preview(),
                            body[:200],
                        )
                        return False
                    self.last_error = ""
                    self.last_status = resp.status
                    self.last_response_body = ""
                    return True
        except Exception as exc:
            self.last_error = f"Telegram send falhou: {exc}"
            log.warning("Telegram send falhou chat_id=%s token=%s: %s", self.chat_id, self._token_preview(), exc)
            return False
