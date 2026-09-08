"""Loyiha sozlamalari: .env faylidan o'qiladi."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")


def _split_keys(raw: str) -> list[str]:
    """`KEY1,KEY2,KEY3` ko'rinishidagi satrni ro'yxatga aylantiradi."""
    return [key.strip() for key in raw.split(",") if key.strip()]


@dataclass(slots=True)
class Config:
    # --- Telethon (userbot) ---
    api_id: int
    api_hash: str
    session_name: str

    # --- Aiogram (admin bot) ---
    bot_token: str
    admin_id: int
    # Maxfiy arxiv kanali (ixtiyoriy). Bot unda admin bo'lishi kerak.
    log_channel_id: int | None = None

    # --- Gemini ---
    gemini_api_keys: list[str] = field(default_factory=list)
    gemini_model: str = "gemini-3.5-flash-lite"
    # Diqqat: fikrlash (thinking) tokenlari ham shu budjetdan yeyiladi,
    # shuning uchun 300 emas, kamida 800 kerak — aks holda javob kesiladi.
    max_output_tokens: int = 1000
    thinking_level: str = "low"
    history_limit: int = 8

    # --- Boshqa ---
    db_path: str = str(BASE_DIR / "settings.db")
    reply_delay_min: float = 1.0
    reply_delay_max: float = 2.0

    @classmethod
    def load(cls) -> "Config":
        missing = [
            name
            for name in ("API_ID", "API_HASH", "BOT_TOKEN", "ADMIN_ID", "GEMINI_API_KEYS")
            if not os.getenv(name)
        ]
        if missing:
            raise RuntimeError(
                f".env faylida quyidagi majburiy o'zgaruvchilar yo'q: {', '.join(missing)}"
            )

        return cls(
            api_id=int(os.environ["API_ID"]),
            api_hash=os.environ["API_HASH"],
            session_name=os.getenv("SESSION_NAME", "userbot"),
            bot_token=os.environ["BOT_TOKEN"],
            admin_id=int(os.environ["ADMIN_ID"]),
            log_channel_id=(
                int(os.environ["LOG_CHANNEL_ID"])
                if os.getenv("LOG_CHANNEL_ID")
                else None
            ),
            gemini_api_keys=_split_keys(os.environ["GEMINI_API_KEYS"]),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
            max_output_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "1000")),
            thinking_level=os.getenv("THINKING_LEVEL", "low"),
            history_limit=int(os.getenv("HISTORY_LIMIT", "8")),
            db_path=os.getenv("DB_PATH", str(BASE_DIR / "settings.db")),
            reply_delay_min=float(os.getenv("REPLY_DELAY_MIN", "1.0")),
            reply_delay_max=float(os.getenv("REPLY_DELAY_MAX", "2.0")),
        )


config = Config.load()
