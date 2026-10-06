import html
import logging
import httpx
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger("Notifier")


class TelegramNotifier:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    @property
    def configured(self) -> bool:
        return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

    async def send_alert(self, headline: str, body: str, link: str) -> bool:
        if not self.configured:
            print(f"\n[ALERT] {headline}\n{body}\n{link}\n")   # console fallback
            return True
        text = (
            f"🚨 <b>{html.escape(headline)}</b>\n\n{html.escape(body)}\n\n"
            f'🔗 <a href="{html.escape(link, quote=True)}">Open Tazkarti</a>'
        )
        try:
            r = await self.client.post(
                self.url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
                timeout=10.0,
            )
            if r.status_code == 200:
                return True
            logger.error("Telegram error %s: %s", r.status_code, r.text)
        except httpx.RequestError as exc:
            logger.error("Telegram network error: %s", type(exc).__name__)
        return False