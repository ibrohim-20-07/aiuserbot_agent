"""Telethon userbot: shaxsiy xabarlarga ikki qatrlamli javob beradi.

Xabar quyidagi bosqichlardan o'tadi:
    1. Filtr      — faqat PM, faqat odamlardan, servis xabarlari o'tkaziladi
    2. Arxiv      — bazaga va maxfiy kanalga yoziladi (javob berilmasa ham)
    3. Ruxsat     — oq/qora ro'yxat tekshiriladi
    4. Regex      — karta so'ralgan bo'lsa, 0 token bilan javob
    5. Gemini     — qolgan hamma narsa
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections import defaultdict

from telethon import TelegramClient, events
from telethon.tl.types import User

from ..config import config
from ..database import (
    check_access,
    get_auto_pause_minutes,
    get_setting,
    increment_stat,
    is_ai_active,
    is_auto_pause_active,
    is_card_regex_active,
    log_message,
    touch_contact,
)
from ..services.archive import archive_service
from ..services.gemini_service import gemini_service
from ..services.presence import presence_tracker
from .filters import is_card_request, is_service_message, normalize

logger = logging.getLogger(__name__)

# Har bir suhbat uchun alohida qulf — bir vaqtda bitta javob yoziladi.
_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def _typing_delay(text: str) -> float:
    """Javob uzunligiga qarab tabiiy "yozish" kechikishi."""
    base = random.uniform(config.reply_delay_min, config.reply_delay_max)
    return min(base + len(text) / 200, 5.0)


async def _reply_with_typing(event: events.NewMessage.Event, text: str) -> None:
    """Chatda `typing` ko'rsatib, kichik kechikish bilan javob yuboradi."""
    async with event.client.action(event.chat_id, "typing"):
        await asyncio.sleep(_typing_delay(text))
        # Bot yuborgan xabar egasining faolligi deb sanalmasin, aks holda AI
        # o'z javobi bilan o'zini jim qilib qo'yardi. Chiquvchi hodisa `reply()`
        # tugashidan oldin kelishi mumkin — shuning uchun OLDIN belgilaymiz.
        presence_tracker.expect_own_message(event.chat_id, text)
        sent = await event.reply(text)
    presence_tracker.remember_own_message(getattr(sent, "id", None))


def _full_name(user: User) -> str:
    parts = [user.first_name or "", user.last_name or ""]
    return " ".join(p for p in parts if p).strip() or "Noma'lum"


def create_client() -> TelegramClient:
    """Telethon mijozini yaratadi va hodisa tinglovchilarini ulaydi."""
    client = TelegramClient(config.session_name, config.api_id, config.api_hash)

    @client.on(events.NewMessage(incoming=True))
    async def on_private_message(event: events.NewMessage.Event) -> None:
        # --- 1. Filtr ---
        if not event.is_private or event.out:
            return

        sender = await event.get_sender()
        if not isinstance(sender, User) or sender.bot or sender.is_self:
            return

        text = normalize(event.raw_text or "")
        if is_service_message(text):
            return

        user_id = sender.id
        username = sender.username
        full_name = _full_name(sender)

        # --- 2. Arxiv (javob berilmasa ham yoziladi) ---
        try:
            is_new = await touch_contact(user_id, username, full_name)
            await log_message(user_id, text, "in", username=username)
        except Exception:
            is_new = False
            logger.exception("Xabarni arxivlashda xato (user=%s)", user_id)

        async def archive(reply: str | None, layer: str) -> None:
            if reply:
                await log_message(user_id, reply, "out", username=username, layer=layer)
            archive_service.log_exchange(
                user_id, full_name, username, text, reply, layer, is_new
            )

        # --- 3. Ruxsat ---
        allowed, reason = await check_access(user_id)
        if not allowed:
            await increment_stat("skipped")
            await archive(None, "skipped")
            logger.info("Javob berilmadi (user=%s): %s", user_id, reason)
            return

        if not await is_ai_active():
            await increment_stat("skipped")
            await archive(None, "skipped")
            logger.debug("AI o'chirilgan, xabar e'tiborsiz qoldirildi.")
            return

        # --- 3b. Avto-pauza: egasi hozir o'zi yozayotgan bo'lsa aralashmaymiz ---
        if await is_auto_pause_active():
            window = await get_auto_pause_minutes() * 60
            if presence_tracker.is_owner_active(window):
                await increment_stat("paused")
                await archive(None, "paused")
                logger.info(
                    "Avto-pauza: egasi faol, javob berilmadi (user=%s)", user_id
                )
                return

        lock = _locks[user_id]
        if lock.locked():
            logger.debug("Oldingi javob hali yozilmoqda (user=%s), o'tkazib yuborildi.", user_id)
            return

        async with lock:
            try:
                # --- 4. Regex qatlami (0 token) ---
                if await is_card_regex_active() and is_card_request(text):
                    card = await get_setting("card_details")
                    await _reply_with_typing(event, card)
                    # AI bu almashinuvni ko'rmaydi — uni qo'lda kontekstga
                    # yozamiz, aks holda keyingi savolga («bu qaysi bank?»)
                    # javob bera olmaydi. Token sarflanmaydi.
                    gemini_service.remember(user_id, text, card)
                    await increment_stat("regex")
                    await archive(card, "regex")
                    logger.info("Karta ma'lumoti yuborildi (user=%s)", user_id)
                    return

                # --- 5. Gemini qatlami ---
                system_prompt = await get_setting("system_prompt")
                answer = await gemini_service.generate(user_id, text, system_prompt)

                if not answer:
                    await increment_stat("error")
                    await archive(None, "error")
                    logger.warning("AI javob bermadi (user=%s)", user_id)
                    return

                await _reply_with_typing(event, answer)
                await increment_stat("ai")
                await archive(answer, "ai")
                logger.info("AI javobi yuborildi (user=%s, %s belgi)", user_id, len(answer))

            except Exception:
                await increment_stat("error")
                logger.exception("Xabarni qayta ishlashda xato (user=%s)", user_id)

    @client.on(events.NewMessage(outgoing=True))
    async def on_own_message(event: events.NewMessage.Event) -> None:
        """Egasining faolligini qayd etadi (avto-pauza uchun).

        Istalgan qurilmadan yozilgan xabar hisobga olinadi. Bot o'zi
        yuborgan xabarlar chetlatiladi (chat + matn yoki xabar ID bo'yicha).

        Arxiv kanali ham shu akkauntdan to'ldiriladi — undagi yozuvlar
        faollik deb sanalsa, AI o'z arxivi tufayli abadiy jim qolardi.
        """
        if (
            archive_service.channel_id is not None
            and event.chat_id == archive_service.channel_id
        ):
            return
        presence_tracker.note_outgoing(event.id, event.chat_id, event.raw_text or "")

    return client


async def run_userbot(client: TelegramClient) -> None:
    """Userbotni ishga tushiradi va ulanish uzilgunicha kutadi."""
    await client.start()
    me = await client.get_me()
    logger.info("Userbot ishga tushdi: @%s (id=%s)", me.username, me.id)

    # Arxiv kanalini akkauntning o'zi yaratadi/tiklaydi — qo'lda sozlash shart emas.
    await archive_service.setup(client)
    archive_service.log_note(f"Userbot ishga tushdi: @{me.username}")

    await client.run_until_disconnected()
