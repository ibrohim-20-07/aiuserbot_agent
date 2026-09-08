"""Xabarlarni maxfiy kanalga arxivlash.

Nega kerak: mijoz o'z xabarini o'chirsa, suhbatni tozalasa yoki akkaunt
bloklansa — yozishmalar yo'qoladi. Bu servis har bir almashinuvni kanalga
nusxalab boradi (baza bilan bir qatorda, ikki qavatli zaxira).

Kanalni **userbot akkauntning o'zi** yaratadi va o'zi yozadi — hech qanday
qo'lda sozlash kerak emas: kanal ochish, botni admin qilish, ID izlash yo'q.
Kanal ID va access_hash bazaga saqlanadi, keyingi ishga tushishda o'sha
kanal ishlatiladi.

`.env` da `LOG_CHANNEL_ID` ko'rsatilgan bo'lsa, avtomatik yaratish
o'tkazib yuboriladi va o'sha tayyor kanal ishlatiladi.

Yuborish alohida navbat (queue) orqali: Telegram tezlikni cheklaydi,
userbot esa javob berishni kutib qolmasligi kerak.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import html
import logging

from telethon import TelegramClient, utils
from telethon.errors import FloodWaitError
from telethon.tl.functions.channels import CreateChannelRequest
from telethon.tl.functions.messages import ExportChatInviteRequest
from telethon.tl.types import InputPeerChannel

from ..config import config
from ..database import get_setting, set_setting

logger = logging.getLogger(__name__)

# Telegramga sekundiga ~1 xabar bemalol o'tadi.
SEND_INTERVAL = 1.1
# Navbat to'lib ketsa eski yozuvlar tashlanadi (baza baribir saqlaydi).
QUEUE_MAX = 500
# Telegram xabar chegarasi 4096; sarlavhaga joy qoldiramiz.
TEXT_LIMIT = 1200

CHANNEL_TITLE = "🗂 Userbot arxivi"
CHANNEL_ABOUT = (
    "Mijozlar bilan yozishmalarning avtomatik nusxasi. "
    "Xabar o'chirilsa ham bu yerda saqlanib qoladi."
)

LAYER_LABELS = {
    "ai": "🤖 AI",
    "regex": "⚡️ Regex",
    "error": "⚠️ Xato",
    "skipped": "🚫 Javob berilmadi",
    "paused": "⏸ Avto-pauza (siz faol edingiz)",
}


def _clip(text: str, limit: int = TEXT_LIMIT) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + " …"


class ArchiveService:
    """Kanalni yaratuvchi va unga fon rejimida yozuvchi arxivchi."""

    def __init__(self) -> None:
        self._client: TelegramClient | None = None
        self._peer: InputPeerChannel | int | None = None
        self._channel_id: int | None = None
        self._invite_link: str | None = None
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_MAX)
        self._worker: asyncio.Task[None] | None = None
        self._dropped = 0
        self._sent = 0

    # ------------------------------------------------------------------ #
    # Holat
    # ------------------------------------------------------------------ #
    @property
    def enabled(self) -> bool:
        return self._client is not None and self._peer is not None

    @property
    def channel_id(self) -> int | None:
        return self._channel_id

    @property
    def invite_link(self) -> str | None:
        return self._invite_link

    def stats(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "sent": self._sent,
            "dropped": self._dropped,
            "queued": self._queue.qsize(),
            "channel_id": self._channel_id,
            "link": self._invite_link,
        }

    # ------------------------------------------------------------------ #
    # Kanalni tayyorlash
    # ------------------------------------------------------------------ #
    async def setup(self, client: TelegramClient) -> None:
        """Kanalni topadi yoki yaratadi va fon vazifasini ishga tushiradi.

        Tartib:
            1. `.env` dagi `LOG_CHANNEL_ID` (agar berilgan bo'lsa)
            2. Bazada saqlangan kanal (oldingi ishga tushishdan)
            3. Yangi kanal yaratish
        """
        self._client = client
        try:
            if config.log_channel_id:
                await self._use_existing(config.log_channel_id)
            elif not await self._restore_saved():
                await self.create_channel(notify=True)
        except Exception:
            logger.exception("Arxiv kanalini tayyorlab bo'lmadi — faqat baza ishlaydi.")
            self._peer = None
            return

        if self._peer is not None and self._worker is None:
            self._worker = asyncio.create_task(self._run(), name="archive_worker")
            logger.info("Kanal arxivi yoqildi: %s", self._channel_id)

    async def _use_existing(self, channel_id: int) -> None:
        """`.env` da ko'rsatilgan tayyor kanalni ishlatadi."""
        entity = await self._client.get_entity(channel_id)
        self._peer = await self._client.get_input_entity(entity)
        self._channel_id = utils.get_peer_id(entity)
        logger.info(".env dagi kanal ishlatilmoqda: %s", self._channel_id)

    async def _restore_saved(self) -> bool:
        """Bazadagi kanalni tiklaydi. Topilmasa `False`."""
        saved_id = await get_setting("log_channel_id", "")
        saved_hash = await get_setting("log_channel_hash", "")
        if not saved_id or not saved_hash:
            return False
        try:
            raw_id, _ = utils.resolve_id(int(saved_id))
            self._peer = InputPeerChannel(channel_id=raw_id, access_hash=int(saved_hash))
            self._channel_id = int(saved_id)
            self._invite_link = await get_setting("log_channel_link", "") or None
            logger.info("Saqlangan arxiv kanali tiklandi: %s", self._channel_id)
            return True
        except (ValueError, TypeError):
            logger.warning("Saqlangan kanal ma'lumoti buzilgan, yangisi yaratiladi.")
            return False

    async def create_channel(self, notify: bool = False) -> int:
        """Userbot nomidan yangi maxfiy kanal yaratadi va bazaga saqlaydi."""
        assert self._client is not None, "Avval setup() chaqirilishi kerak"

        result = await self._client(
            CreateChannelRequest(
                title=CHANNEL_TITLE,
                about=CHANNEL_ABOUT,
                # broadcast=True — aynan KANAL (guruh emas). Telegram bu ikki
                # bayroqdan birini talab qiladi, faqat megagroup=False yetarli emas.
                broadcast=True,
                megagroup=False,
            )
        )
        channel = result.chats[0]
        self._peer = InputPeerChannel(
            channel_id=channel.id, access_hash=channel.access_hash
        )
        self._channel_id = utils.get_peer_id(channel)

        await set_setting("log_channel_id", str(self._channel_id))
        await set_setting("log_channel_hash", str(channel.access_hash))

        try:
            invite = await self._client(ExportChatInviteRequest(self._peer))
            self._invite_link = invite.link
            await set_setting("log_channel_link", invite.link)
        except Exception:
            logger.debug("Taklif havolasini olib bo'lmadi.", exc_info=True)

        logger.info("Yangi arxiv kanali yaratildi: %s", self._channel_id)
        if notify:
            self.log_note(
                "Arxiv kanali yaratildi. Bundan buyon barcha yozishmalar shu yerga "
                "nusxalanadi — mijoz xabarini o'chirsa ham saqlanib qoladi."
            )
        return self._channel_id

    async def close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    # ------------------------------------------------------------------ #
    # Yuborish
    # ------------------------------------------------------------------ #
    def _enqueue(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            self._dropped += 1
            logger.warning("Arxiv navbati to'lgan, xabar tashlandi (jami %s)", self._dropped)

    async def _run(self) -> None:
        """Navbatdagi xabarlarni birma-bir kanalga yuboradi."""
        while True:
            text = await self._queue.get()
            try:
                await self._client.send_message(
                    self._peer, text, parse_mode="html", link_preview=False
                )
                self._sent += 1
            except FloodWaitError as exc:
                logger.warning("Kanal limiti: %s soniya kutamiz", exc.seconds)
                await asyncio.sleep(exc.seconds)
                self._enqueue(text)  # qayta urinamiz
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Kanalga yozib bo'lmadi (baza saqlab qoldi).")
            finally:
                self._queue.task_done()
            await asyncio.sleep(SEND_INTERVAL)

    # ------------------------------------------------------------------ #
    # Formatlash
    # ------------------------------------------------------------------ #
    @staticmethod
    def _header(
        user_id: int,
        full_name: str | None,
        username: str | None,
        is_new: bool = False,
    ) -> str:
        name = html.escape(full_name or "Noma'lum")
        handle = f" (@{html.escape(username)})" if username else ""
        badge = " 🆕" if is_new else ""
        stamp = dt.datetime.now().strftime("%d.%m %H:%M")
        return (
            f"👤 <b>{name}</b>{handle}{badge}\n"
            f"<code>{user_id}</code> · <a href=\"tg://user?id={user_id}\">ochish</a> · {stamp}"
        )

    def log_exchange(
        self,
        user_id: int,
        full_name: str | None,
        username: str | None,
        incoming: str,
        reply: str | None,
        layer: str,
        is_new: bool = False,
    ) -> None:
        """Bitta almashinuvni (kiruvchi + javob) kanalga navbatga qo'yadi."""
        parts = [self._header(user_id, full_name, username, is_new)]
        parts.append(f"\n💬 <b>Mijoz:</b>\n{html.escape(_clip(incoming))}")
        label = LAYER_LABELS.get(layer, layer)
        if reply:
            parts.append(f"\n<b>{label}:</b>\n{html.escape(_clip(reply))}")
        else:
            parts.append(f"\n{label}")
        self._enqueue("\n".join(parts))

    def log_note(self, text: str) -> None:
        """Xizmat xabari (ishga tushish, kanal yaratilishi va h.k.)."""
        self._enqueue(f"ℹ️ {html.escape(text)}")


# Butun loyiha uchun yagona nusxa.
archive_service = ArchiveService()
