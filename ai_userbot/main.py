"""Kirish nuqtasi: Userbot va Admin botni bitta event loopda ishga tushiradi.

Ishga tushirish:
    python -m ai_userbot.main
    (yoki to'g'ridan-to'g'ri: python ai_userbot/main.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

# `python ai_userbot/main.py` ko'rinishida ishga tushirilganda ham
# nisbiy importlar ishlashi uchun.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "ai_userbot"

import asyncio  # noqa: E402
import logging  # noqa: E402

from .admin_bot.bot import create_bot, create_dispatcher, run_admin_bot  # noqa: E402
from .config import config  # noqa: E402
from .database import init_db  # noqa: E402
from .services.archive import archive_service  # noqa: E402
from .userbot.client import create_client, run_userbot  # noqa: E402

logger = logging.getLogger("ai_userbot")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    for noisy in ("telethon", "aiogram.event", "httpx", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def main() -> None:
    setup_logging()
    logger.info("Ishga tushirilmoqda… (model=%s)", config.gemini_model)

    await init_db()

    userbot = create_client()
    bot = create_bot()
    dp = create_dispatcher()

    # Arxiv kanalini userbot o'zi yaratadi/tiklaydi — `run_userbot()` ichida,
    # akkaunt Telegramga ulangandan keyin.
    tasks = [
        asyncio.create_task(run_userbot(userbot), name="userbot"),
        asyncio.create_task(run_admin_bot(bot, dp), name="admin_bot"),
    ]

    try:
        # Biri to'xtasa (yoki xato bersa) — ikkinchisini ham to'xtatamiz.
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            if task.exception() is not None:
                logger.error("«%s» to'xtadi:", task.get_name(), exc_info=task.exception())
    except asyncio.CancelledError:
        logger.info("To'xtatish signali olindi.")
        raise
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        await archive_service.close()
        await dp.storage.close()
        await bot.session.close()
        if userbot.is_connected():
            await userbot.disconnect()
        logger.info("Barcha ulanishlar yopildi. Xayr! 👋")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nTo'xtatildi.")
