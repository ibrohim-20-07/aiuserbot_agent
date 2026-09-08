"""Userbot menejeri: TelegramClient ning to'liq hayotiy sikli,
avtorizatsiya (telefon, kod, 2FA), kodni filtrlash va holat nazorati.
Terminal orqali input talab qilinmaydi — barchasi bot orqali boshqariladi.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.tl.types import User

from ..config import config
from ..services.archive import archive_service

logger = logging.getLogger(__name__)


def filter_login_code(raw: str) -> str:
    """Telegramdan kelgan tasdiqlash kodini tozalaydi.

    Foydalanuvchi xavfsizlik cheklovlari tufayli kodni `1.2.3.4.5`, `1 2 3 4 5`,
    `1-2-3-4-5` kabi formatlarda yuborganda, barcha no-raqam belgilarni olib tashlaydi.
    """
    if not raw:
        return ""
    return "".join(re.findall(r"\d", raw))


def normalize_phone(raw: str) -> str:
    """Telefon raqamini xalqaro formatga keltiradi (+998...)."""
    if not raw:
        return ""
    digits = "".join(re.findall(r"\d", raw))
    if not digits:
        return ""
    return f"+{digits}"


class UserbotManager:
    """Telethon mijozini xavfsiz boshqarish va bot orqali login qilish xizmati."""

    def __init__(self) -> None:
        self.client: TelegramClient | None = None
        self.is_active: bool = False
        self._stopped: bool = False
        self._phone: str | None = None
        self._phone_code_hash: str | None = None
        self.cached_user: dict[str, Any] | None = None
        self._setup_handlers_fn: Any = None
        self._init_client()

    def set_handlers_setup(self, fn: Any) -> None:
        """Client handlerlarini ulash funksiyasini biriktiradi."""
        self._setup_handlers_fn = fn
        if self.client is not None:
            fn(self.client)

    def _init_client(self) -> None:
        """Telethon client obyektini yaratadi."""
        self.client = TelegramClient(config.session_name, config.api_id, config.api_hash)
        if self._setup_handlers_fn is not None:
            self._setup_handlers_fn(self.client)

    @property
    def phone(self) -> str | None:
        return self._phone

    @property
    def phone_code_hash(self) -> str | None:
        return self._phone_code_hash

    @staticmethod
    def _format_user(user: User) -> dict[str, Any]:
        first = user.first_name or ""
        last = user.last_name or ""
        full = f"{first} {last}".strip() or "Noma'lum"
        return {
            "id": user.id,
            "first_name": first,
            "last_name": last,
            "full_name": full,
            "username": user.username or "",
            "phone": user.phone or "",
        }

    async def connect_and_check(self) -> bool:
        """Telegramga ulanadi va sessiya avtorizatsiyadan o'tganligini tekshiradi."""
        assert self.client is not None
        if not self.client.is_connected():
            await self.client.connect()

        is_auth = await self.client.is_user_authorized()
        if is_auth:
            await self.activate()
            return True

        self.is_active = False
        self.cached_user = None
        return False

    async def activate(self) -> None:
        """Akkaunt ulanganda arxivalash va ichki holatni yoqadi."""
        assert self.client is not None
        if not await self.client.is_user_authorized():
            return
        me = await self.client.get_me()
        if isinstance(me, User):
            self.cached_user = self._format_user(me)
            self.is_active = True
            logger.info("Userbot muvaffaqiyatli faollashdi: @%s (id=%s)", me.username, me.id)
            try:
                await archive_service.setup(self.client)
                archive_service.log_note(f"Userbot ishga tushdi: @{me.username or me.id}")
            except Exception:
                logger.exception("Arxiv servisini ishga tushirishda xatolik:")

    async def get_account_info(self) -> dict[str, Any] | None:
        """Joriy ulangan akkaunt ma'lumotlarini qaytaradi. Ulanmagan bo'lsa None."""
        assert self.client is not None
        if not self.client.is_connected():
            try:
                await self.client.connect()
            except Exception:
                return None

        if not await self.client.is_user_authorized():
            self.cached_user = None
            self.is_active = False
            return None

        me = await self.client.get_me()
        if isinstance(me, User):
            self.cached_user = self._format_user(me)
            return self.cached_user
        return None

    async def is_authorized(self) -> bool:
        """Akkaunt avtorizatsiyadan o'tganligini tekshiradi."""
        assert self.client is not None
        if not self.client.is_connected():
            try:
                await self.client.connect()
            except Exception:
                return False
        return bool(await self.client.is_user_authorized())

    async def send_code(self, raw_phone: str) -> str:
        """Telefon raqamga login kodini so'raydi. phone_code_hash ni qaytaradi."""
        phone = normalize_phone(raw_phone)
        if not phone or len(phone) < 8:
            raise ValueError(
                "Telefon raqami noto'g'ri. Xalqaro formatda kiriting (masalan: +998901234567)."
            )

        assert self.client is not None
        if not self.client.is_connected():
            await self.client.connect()

        try:
            sent_code = await self.client.send_code_request(phone)
            self._phone = phone
            self._phone_code_hash = sent_code.phone_code_hash
            logger.info("Tasdiqlash kodi yuborildi: %s", phone)
            return sent_code.phone_code_hash
        except (PhoneNumberInvalidError, PhoneNumberBannedError, FloodWaitError):
            raise
        except Exception as exc:
            logger.exception("Kod so'rashda kutilmagan xato: %s", exc)
            raise

    async def sign_in_with_code(
        self,
        code: str,
        phone: str | None = None,
        phone_code_hash: str | None = None,
    ) -> dict[str, Any]:
        """Kodni filtrlash va akkauntga kirish.

        Agar 2FA yoqilgan bo'lsa, SessionPasswordNeededError ko'tariladi.
        """
        clean_code = filter_login_code(code)
        if len(clean_code) < 5:
            raise ValueError(
                "Tasdiqlash kodi kamida 5 ta raqamdan iborat bo'lishi kerak (masalan: 1.2.3.4.5)."
            )

        target_phone = phone or self._phone
        target_hash = phone_code_hash or self._phone_code_hash
        if not target_phone or not target_hash:
            raise ValueError("Avval telefon raqam kiritilishi va kod so'ralishi kerak.")

        assert self.client is not None
        if not self.client.is_connected():
            await self.client.connect()

        try:
            await self.client.sign_in(
                phone=target_phone,
                code=clean_code,
                phone_code_hash=target_hash,
            )
            await self.activate()
            return self.cached_user or {}
        except (
            SessionPasswordNeededError,
            PhoneCodeInvalidError,
            PhoneCodeExpiredError,
            FloodWaitError,
        ):
            raise
        except Exception as exc:
            logger.exception("Kod bilan kirishda xato: %s", exc)
            raise

    async def sign_in_with_password(self, password: str) -> dict[str, Any]:
        """2FA (bulutli parol) bilan kirish."""
        assert self.client is not None
        if not self.client.is_connected():
            await self.client.connect()

        try:
            await self.client.sign_in(password=password)
            await self.activate()
            return self.cached_user or {}
        except (PasswordHashInvalidError, FloodWaitError):
            raise
        except Exception as exc:
            logger.exception("2FA paroli bilan kirishda xato: %s", exc)
            raise

    async def logout(self) -> bool:
        """Akkauntdan chiqadi va yangi login uchun clientni tayyorlaydi."""
        self.is_active = False
        self.cached_user = None
        self._phone = None
        self._phone_code_hash = None

        try:
            await archive_service.close()
        except Exception:
            logger.warning("Arxiv servisini yopishda xatolik:", exc_info=True)

        try:
            assert self.client is not None
            if self.client.is_connected():
                if await self.client.is_user_authorized():
                    await self.client.log_out()
                else:
                    await self.client.disconnect()
        except Exception:
            logger.warning("Telethon log_out xatoligi:", exc_info=True)

        try:
            self._init_client()
            assert self.client is not None
            await self.client.connect()
            logger.info("Akkaunt uzildi va yangi sessiya tayyorlandi.")
            return True
        except Exception:
            logger.exception("Yangi sessiya yaratishda xato:")
            return False

    async def run(self) -> None:
        """Fon rejimida userbot ulanishini saqlab turadi."""
        self._stopped = False
        while not self._stopped:
            try:
                assert self.client is not None
                if not self.client.is_connected():
                    await self.client.connect()

                if await self.client.is_user_authorized():
                    if not self.is_active:
                        await self.activate()
                else:
                    logger.info("Userbot avtorizatsiyadan o'tmagan. Admin bot orqali ulanish kutilmoqda.")

                await self.client.run_until_disconnected()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Userbot ulanishida uzilish (%s). 5 soniyadan keyin qayta ulanadi...", exc)

            if not self._stopped:
                await asyncio.sleep(5)

    async def close(self) -> None:
        """Barcha ulanishlarni tozalaydi va yopadi."""
        self._stopped = True
        self.is_active = False
        if self.client is not None and self.client.is_connected():
            await self.client.disconnect()


# Butun tizim uchun yagona nusxa
userbot_manager = UserbotManager()
