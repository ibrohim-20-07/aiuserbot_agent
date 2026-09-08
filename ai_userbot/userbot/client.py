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


def setup_handlers(client: TelegramClient) -> None:
    """Telethon client hodisa tinglovchilarini ro'yxatdan o'tkazadi."""

    @client.on(events.NewMessage(incoming=True))
    async def on_private_message(event: events.NewMessage.Event) -> None:
        # --- 1. Filtr ---
        if not event.is_private or event.out:
            return

        sender = await event.get_sender()
        if not isinstance(sender, User) or sender.bot or sender.is_self:
            return

        # Media turini aniqlaymiz
        media_type = None
        if event.photo:
            media_type = "rasm"
        elif event.voice:
            media_type = "ovozli xabar"
        elif event.video or getattr(event, "video_note", None):
            media_type = "video"
        elif event.document:
            media_type = "hujjat"
        elif getattr(event, "sticker", None):
            media_type = "stiker"

        raw_text = event.raw_text or ""
        text = normalize(raw_text)
        if not text and media_type:
            text = f"[{media_type.capitalize()}]"

        if is_service_message(text):
            return

        user_id = sender.id
        username = sender.username
        full_name = _full_name(sender)
        phone = sender.phone
        is_premium = 1 if getattr(sender, "premium", False) else 0

        # --- 2. Arxiv (javob berilmasa ham yoziladi) ---
        try:
            is_new = await touch_contact(
                user_id, username, full_name, phone=phone, is_premium=is_premium
            )
            await log_message(
                user_id,
                text,
                "in",
                username=username,
                msg_id=event.id,
                media_type=media_type,
            )
        except Exception:
            is_new = False
            logger.exception("Xabarni arxivlashda xato (user=%s)", user_id)

        async def archive(reply: str | None, layer: str) -> None:
            if reply:
                await log_message(user_id, reply, "out", username=username, layer=layer)
            archive_service.log_exchange(
                user_id,
                full_name,
                username,
                text,
                reply,
                layer,
                is_new=is_new,
                phone=phone,
                is_premium=bool(is_premium),
                msg_id=event.id,
                media_type=media_type,
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
                # Kontekst uzilib qolmasligi uchun bu xabarni ham Gemini xotirasiga yozib qo'yamiz!
                gemini_service.remember_user(user_id, text)
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
        """Egasining faolligini qayd etadi (avto-pauza uchun) va egasining
        qo'lda yozgan javoblarini Gemini kontekstiga qo'shadi.
        """
        if (
            archive_service.channel_id is not None
            and event.chat_id == archive_service.channel_id
        ):
            return

        raw_text = event.raw_text or ""
        is_owner = presence_tracker.note_outgoing(event.id, event.chat_id, raw_text)

        # Agar bu botning o'zi yuborgan avtomatik xabar bo'lmasa va shaxsiy chat bo'lsa:
        # demak bu egasining shaxsan o'zi yozgan xabari!
        if is_owner and event.is_private:
            user_id = event.chat_id
            # Kontekstni Gemini ga eslatib qo'yamiz (keyinchalik AI bu javobni biladi)
            gemini_service.remember_reply(user_id, raw_text)
            # Bazaga ham saqlaymiz
            try:
                await log_message(
                    user_id,
                    raw_text,
                    "out",
                    layer="owner",
                    msg_id=event.id,
                )
            except Exception:
                logger.debug("Egasi javobini bazaga yozishda xato:", exc_info=True)

    @client.on(events.MessageDeleted)
    async def on_message_deleted(event: events.MessageDeleted.Event) -> None:
        """O'chirilgan xabarlarni tutib, arxivga xabar beradi (Anti-Delete)."""
        from ..database import mark_message_deleted

        for deleted_id in event.deleted_ids:
            try:
                record = await mark_message_deleted(deleted_id)
                if record:
                    logger.info(
                        "O'chirilgan xabar tutildi: msg_id=%s, user=%s",
                        deleted_id,
                        record["user_id"],
                    )
                    archive_service.log_deleted_message(
                        user_id=record["user_id"],
                        full_name=record["full_name"],
                        username=record["username"],
                        deleted_text=record["text"],
                        msg_id=deleted_id,
                        phone=record.get("phone"),
                    )
            except Exception:
                logger.exception("O'chirilgan xabarni qayta ishlashda xato (id=%s):", deleted_id)


from .manager import userbot_manager  # noqa: E402

# Menejerga handlerlarni ulash funksiyasini taqdim etamiz
userbot_manager.set_handlers_setup(setup_handlers)


def create_client() -> TelegramClient:
    """Mavjud userbot clientini qaytaradi (orqaga moslik uchun)."""
    assert userbot_manager.client is not None
    return userbot_manager.client


async def run_userbot(client: TelegramClient | None = None) -> None:
    """Userbotni terminalda input so'ramaydigan xavfsiz holda ishga tushiradi."""
    await userbot_manager.run()

