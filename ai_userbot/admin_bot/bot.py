"""Aiogram 3 admin bot: bot, dispatcher va polling."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from ..config import config
from .handlers import router

logger = logging.getLogger(__name__)


def create_bot() -> Bot:
    return Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    return dp


from ..userbot.manager import userbot_manager


async def run_admin_bot(bot: Bot, dp: Dispatcher) -> None:
    """Adminni xabardor qilib, pollingni boshlaydi."""
    await bot.set_my_commands(
        [
            BotCommand(command="menu", description="Boshqaruv paneli"),
            BotCommand(command="account", description="Akkaunt holati va boshqaruvi"),
            BotCommand(command="login", description="Akkauntni ulash"),
            BotCommand(command="logout", description="Akkauntni uzish"),
            BotCommand(command="start", description="Botni ishga tushirish"),
        ]
    )
    await bot.delete_webhook(drop_pending_updates=True)

    me = await bot.get_me()
    logger.info("Admin bot ishga tushdi: @%s", me.username)

    try:
        is_auth = await userbot_manager.is_authorized()
        if is_auth:
            acc = await userbot_manager.get_account_info()
            handle = f"@{acc.get('username')}" if acc and acc.get("username") else (acc.get("full_name") if acc else "ulangan")
            status_note = f"📱 Akkaunt: <b>{handle}</b> (faol ✅)"
        else:
            status_note = "⚠️ <b>Userbot akkaunti ulanmagan!</b>\nAkkauntni ulash uchun /login yoki /account bosing."

        await bot.send_message(
            config.admin_id,
            f"♻️ <b>Tizim ishga tushdi</b>\n\n{status_note}\n\n/menu",
        )
    except Exception:
        logger.warning("Adminga xabar yuborib bo'lmadi (u botga /start bosmagan bo'lishi mumkin).")

    await dp.start_polling(bot, handle_signals=False)

