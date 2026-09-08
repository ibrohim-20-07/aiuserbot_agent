"""SQLite (aiosqlite) asosidagi baza: sozlamalar, ro'yxatlar, arxiv, statistika.

Sozlamalar `settings` jadvalida kalit-qiymat ko'rinishida saqlanadi va
keshlanmaydi — admin bot qiymatni o'zgartirsa, userbot keyingi xabardayoq
yangi qiymatni o'qiydi.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import aiosqlite

from .config import config

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = (
    "Sen O'zbek tilida muloqot qiluvchi professional savdo maslahatchisisan. "
    "Mijozlarga xizmatlar va narxlar haqida qisqa, aniq va samimiy javob ber. "
    "Javoblaring 2-3 jumladan oshmasin. Agar savolga javobni bilmasang, "
    "administrator tez orada bog'lanishini ayt."
)

DEFAULT_CARD_DETAILS = (
    "💳 To'lov uchun karta ma'lumotlari:\n"
    "8600 0000 0000 0000\n"
    "Ism Familiya\n\n"
    "To'lovdan so'ng chekni shu yerga yuboring."
)

# Kimga javob berish rejimi.
MODE_ALL = "all"              # hammaga
MODE_WHITELIST = "whitelist"  # faqat oq ro'yxatdagilarga
MODE_BLACKLIST = "blacklist"  # qora ro'yxatdagilardan tashqari hammaga

MODE_LABELS = {
    MODE_ALL: "Hammaga",
    MODE_WHITELIST: "Faqat oq ro'yxat",
    MODE_BLACKLIST: "Qora ro'yxatdan tashqari",
}

LIST_WHITE = "white"
LIST_BLACK = "black"
LIST_LABELS = {LIST_WHITE: "⚪️ Oq ro'yxat", LIST_BLACK: "⚫️ Qora ro'yxat"}

DEFAULTS: dict[str, str] = {
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "card_details": DEFAULT_CARD_DETAILS,
    "is_ai_active": "1",
    # Karta regex qatlami. Ba'zi biznes modellarda (mijozdan karta olib,
    # unga pul to'lashda) bu qatlam keraksiz — o'chirib qo'yiladi.
    "is_card_regex_active": "1",
    "access_mode": MODE_ALL,
    # Avto-pauza: egasi o'zi yozayotgan bo'lsa AI aralashmaydi.
    "auto_pause_active": "0",
    "auto_pause_minutes": "10",
    # Kontekst hajmi: har bir suhbat uchun xotirada saqlanadigan xabarlar soni
    "history_limit": "8",
}

# Avto-pauza uchun tanlanadigan oraliqlar (daqiqa).
PAUSE_CHOICES = (5, 10, 30, 60)

# Kontekst hajmi uchun tanlanadigan oraliqlar (xabarlar soni).
CONTEXT_CHOICES = (4, 8, 12, 16)

# Arxivdagi xabarlar shuncha kundan keyin tozalanadi.
MESSAGE_RETENTION_DAYS = 90

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stats (
    day   TEXT NOT NULL,
    kind  TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, kind)
);

-- Oq va qora ro'yxatlar
CREATE TABLE IF NOT EXISTS access_list (
    user_id   INTEGER NOT NULL,
    list_type TEXT    NOT NULL,
    username  TEXT,
    full_name TEXT,
    note      TEXT,
    added_at  TEXT    NOT NULL,
    PRIMARY KEY (user_id, list_type)
);

-- Har bir suhbatdosh
CREATE TABLE IF NOT EXISTS contacts (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT,
    full_name  TEXT,
    phone      TEXT,
    is_premium INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    messages   INTEGER NOT NULL DEFAULT 0
);

-- Barcha xabarlar arxivi (kanal o'chib ketsa ham baza saqlanadi)
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id     INTEGER,
    ts         TEXT    NOT NULL,
    day        TEXT    NOT NULL,
    user_id    INTEGER NOT NULL,
    username   TEXT,
    direction  TEXT    NOT NULL,
    layer      TEXT,
    media_type TEXT,
    text       TEXT    NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_messages_day     ON messages (day);
CREATE INDEX IF NOT EXISTS idx_messages_user    ON messages (user_id);

-- Token sarfi (kalitlar kesimida)
CREATE TABLE IF NOT EXISTS token_usage (
    day       TEXT    NOT NULL,
    key_tail  TEXT    NOT NULL,
    requests  INTEGER NOT NULL DEFAULT 0,
    prompt_t  INTEGER NOT NULL DEFAULT 0,
    output_t  INTEGER NOT NULL DEFAULT 0,
    think_t   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, key_tail)
);
"""


def _today() -> str:
    return dt.date.today().isoformat()


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------- #
# Ishga tushirish
# --------------------------------------------------------------------- #
async def init_db() -> None:
    """Jadvallarni yaratadi, standart qiymatlarni to'ldiradi, eskini tozalaydi."""
    async with aiosqlite.connect(config.db_path) as db:
        await db.executescript(_SCHEMA)

        # Migratsiyalar: mavjud jadvallarga yangi ustunlarni xavfsiz qo'shish
        for col, col_def in [
            ("phone", "TEXT"),
            ("is_premium", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                await db.execute(f"ALTER TABLE contacts ADD COLUMN {col} {col_def}")
            except Exception:
                pass

        for col, col_def in [
            ("msg_id", "INTEGER"),
            ("media_type", "TEXT"),
            ("is_deleted", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                await db.execute(f"ALTER TABLE messages ADD COLUMN {col} {col_def}")
            except Exception:
                pass

        try:
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_msg_id ON messages (msg_id)"
            )
        except Exception:
            pass

        await db.executemany(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            list(DEFAULTS.items()),
        )
        cutoff = (dt.date.today() - dt.timedelta(days=MESSAGE_RETENTION_DAYS)).isoformat()
        await db.execute("DELETE FROM messages WHERE day < ?", (cutoff,))
        await db.commit()
    logger.info("Baza tayyor: %s", config.db_path)


# --------------------------------------------------------------------- #
# Sozlamalar
# --------------------------------------------------------------------- #
async def get_setting(key: str, default: str | None = None) -> str:
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
    if row is not None:
        return row[0]
    return default if default is not None else DEFAULTS.get(key, "")


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()
    logger.info("Sozlama yangilandi: %s", key)


async def is_ai_active() -> bool:
    return (await get_setting("is_ai_active", "1")) == "1"


async def toggle_ai() -> bool:
    new_state = not await is_ai_active()
    await set_setting("is_ai_active", "1" if new_state else "0")
    return new_state


async def is_card_regex_active() -> bool:
    return (await get_setting("is_card_regex_active", "1")) == "1"


async def toggle_card_regex() -> bool:
    new_state = not await is_card_regex_active()
    await set_setting("is_card_regex_active", "1" if new_state else "0")
    return new_state


# --------------------------------------------------------------------- #
# Avto-pauza (egasi faol bo'lganda AI jim turadi)
# --------------------------------------------------------------------- #
async def is_auto_pause_active() -> bool:
    return (await get_setting("auto_pause_active", "0")) == "1"


async def toggle_auto_pause() -> bool:
    new_state = not await is_auto_pause_active()
    await set_setting("auto_pause_active", "1" if new_state else "0")
    return new_state


async def get_auto_pause_minutes() -> int:
    try:
        return max(1, int(await get_setting("auto_pause_minutes", "10")))
    except ValueError:
        return 10


async def set_auto_pause_minutes(minutes: int) -> None:
    await set_setting("auto_pause_minutes", str(max(1, minutes)))


# --------------------------------------------------------------------- #
# Kontekst hajmi (har bir suhbatdosh uchun oxirgi xabarlar soni)
# --------------------------------------------------------------------- #
async def get_history_limit() -> int:
    try:
        return max(2, int(await get_setting("history_limit", "8")))
    except ValueError:
        return 8


async def set_history_limit(limit: int) -> None:
    await set_setting("history_limit", str(max(2, limit)))


async def get_db_message_count() -> int:
    """Bazadagi jami xabarlar soni."""
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM messages") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


# --------------------------------------------------------------------- #
# Oq / qora ro'yxatlar
# --------------------------------------------------------------------- #
async def get_access_mode() -> str:
    mode = await get_setting("access_mode", MODE_ALL)
    return mode if mode in MODE_LABELS else MODE_ALL


async def set_access_mode(mode: str) -> None:
    if mode not in MODE_LABELS:
        raise ValueError(f"Noma'lum rejim: {mode}")
    await set_setting("access_mode", mode)


async def add_to_list(
    list_type: str,
    user_id: int,
    username: str | None = None,
    full_name: str | None = None,
    note: str | None = None,
) -> None:
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(
            "INSERT INTO access_list (user_id, list_type, username, full_name, note, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, list_type) DO UPDATE SET "
            "username = excluded.username, full_name = excluded.full_name, "
            "note = excluded.note",
            (user_id, list_type, username, full_name, note, _now()),
        )
        await db.commit()


async def remove_from_list(list_type: str, user_id: int) -> bool:
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute(
            "DELETE FROM access_list WHERE user_id = ? AND list_type = ?",
            (user_id, list_type),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_list(list_type: str) -> list[dict[str, object]]:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, username, full_name, note, added_at FROM access_list "
            "WHERE list_type = ? ORDER BY added_at DESC",
            (list_type,),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def is_in_list(list_type: str, user_id: int) -> bool:
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute(
            "SELECT 1 FROM access_list WHERE user_id = ? AND list_type = ?",
            (user_id, list_type),
        ) as cur:
            return await cur.fetchone() is not None


async def find_contact(query: str) -> dict[str, object] | None:
    """ID yoki @username bo'yicha oldin yozishgan odamni topadi."""
    clean = query.strip().lstrip("@")
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        if clean.isdigit():
            sql = "SELECT * FROM contacts WHERE user_id = ?"
            param: object = int(clean)
        else:
            sql = "SELECT * FROM contacts WHERE lower(username) = ?"
            param = clean.lower()
        async with db.execute(sql, (param,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def check_access(user_id: int) -> tuple[bool, str]:
    """Bu foydalanuvchiga javob berish mumkinmi? (ruxsat, sabab)"""
    mode = await get_access_mode()
    if mode == MODE_WHITELIST:
        if await is_in_list(LIST_WHITE, user_id):
            return True, ""
        return False, "oq ro'yxatda yo'q"
    if mode == MODE_BLACKLIST:
        if await is_in_list(LIST_BLACK, user_id):
            return False, "qora ro'yxatda"
        return True, ""
    return True, ""


# --------------------------------------------------------------------- #
# Kontaktlar
# --------------------------------------------------------------------- #
async def touch_contact(
    user_id: int,
    username: str | None,
    full_name: str | None,
    phone: str | None = None,
    is_premium: int = 0,
) -> bool:
    """Kontaktni yangilaydi. Birinchi marta yozayotgan bo'lsa `True` qaytaradi."""
    now = _now()
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute(
            "SELECT 1 FROM contacts WHERE user_id = ?", (user_id,)
        ) as cur:
            is_new = await cur.fetchone() is None
        await db.execute(
            "INSERT INTO contacts (user_id, username, full_name, phone, is_premium, first_seen, last_seen, messages) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "username = excluded.username, full_name = excluded.full_name, "
            "phone = COALESCE(excluded.phone, contacts.phone), "
            "is_premium = excluded.is_premium, "
            "last_seen = excluded.last_seen, messages = messages + 1",
            (user_id, username, full_name, phone, is_premium, now, now),
        )
        await db.commit()
    return is_new


# --------------------------------------------------------------------- #
# Xabarlar arxivi
# --------------------------------------------------------------------- #
async def log_message(
    user_id: int,
    text: str,
    direction: str,
    username: str | None = None,
    layer: str | None = None,
    msg_id: int | None = None,
    media_type: str | None = None,
) -> None:
    """Xabarni bazaga yozadi (`direction`: in | out)."""
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(
            "INSERT INTO messages (ts, day, user_id, username, direction, layer, text, msg_id, media_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), _today(), user_id, username, direction, layer, text, msg_id, media_type),
        )
        await db.commit()


async def mark_message_deleted(msg_id: int, user_id: int | None = None) -> dict[str, Any] | None:
    """O'chirilgan xabarni belgilaydi va uning ma'lumotlarini qaytaradi."""
    async with aiosqlite.connect(config.db_path) as db:
        query = (
            "SELECT m.id, m.user_id, m.username, m.text, m.ts, m.media_type, "
            "       c.full_name, c.phone "
            "FROM messages m "
            "LEFT JOIN contacts c ON c.user_id = m.user_id "
            "WHERE m.msg_id = ? "
        )
        params: list[Any] = [msg_id]
        if user_id is not None:
            query += "AND m.user_id = ? "
            params.append(user_id)
        query += "ORDER BY m.id DESC LIMIT 1"

        async with db.execute(query, params) as cur:
            row = await cur.fetchone()

        if row is None:
            return None

        # is_deleted bayrog'ini 1 qilamiz
        await db.execute("UPDATE messages SET is_deleted = 1 WHERE id = ?", (row[0],))
        await db.commit()

        return {
            "id": row[0],
            "user_id": row[1],
            "username": row[2],
            "text": row[3],
            "ts": row[4],
            "media_type": row[5],
            "full_name": row[6] or "Noma'lum",
            "phone": row[7],
        }


async def get_recent_messages(user_id: int, limit: int = 8) -> list[dict[str, str]]:
    """Gemini kontekstini to'ldirish uchun oxirgi xabarlar tarixini oladi."""
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute(
            "SELECT direction, text FROM messages "
            "WHERE user_id = ? AND text IS NOT NULL AND text != '' "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()

    results = []
    for direction, text in reversed(rows):
        role = "model" if direction == "out" else "user"
        results.append({"role": role, "text": text})
    return results


# --------------------------------------------------------------------- #
# Statistika
# --------------------------------------------------------------------- #
async def increment_stat(kind: str, day: str | None = None) -> None:
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(
            "INSERT INTO stats (day, kind, count) VALUES (?, ?, 1) "
            "ON CONFLICT(day, kind) DO UPDATE SET count = count + 1",
            (day or _today(), kind),
        )
        await db.commit()


async def record_usage(
    key_tail: str, prompt_t: int = 0, output_t: int = 0, think_t: int = 0
) -> None:
    """Bitta Gemini so'rovining token sarfini yozadi."""
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(
            "INSERT INTO token_usage (day, key_tail, requests, prompt_t, output_t, think_t) "
            "VALUES (?, ?, 1, ?, ?, ?) "
            "ON CONFLICT(day, key_tail) DO UPDATE SET "
            "requests = requests + 1, prompt_t = prompt_t + excluded.prompt_t, "
            "output_t = output_t + excluded.output_t, think_t = think_t + excluded.think_t",
            (_today(), key_tail, prompt_t, output_t, think_t),
        )
        await db.commit()


async def get_today_stats() -> dict[str, int]:
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute(
            "SELECT kind, count FROM stats WHERE day = ?", (_today(),)
        ) as cur:
            return {kind: count for kind, count in await cur.fetchall()}


async def get_total_stats() -> dict[str, int]:
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute("SELECT kind, SUM(count) FROM stats GROUP BY kind") as cur:
            return {kind: total for kind, total in await cur.fetchall()}


async def get_period_report(days: int = 1) -> dict[str, object]:
    """`days` kunlik to'liq hisobot (bugundan orqaga)."""
    since = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute(
            "SELECT kind, SUM(count) FROM stats WHERE day >= ? GROUP BY kind", (since,)
        ) as cur:
            replies = {kind: total for kind, total in await cur.fetchall()}

        async with db.execute(
            "SELECT COUNT(*), COUNT(DISTINCT user_id) FROM messages "
            "WHERE day >= ? AND direction = 'in'",
            (since,),
        ) as cur:
            incoming, unique_users = await cur.fetchone()

        async with db.execute(
            "SELECT COUNT(*) FROM contacts WHERE substr(first_seen, 1, 10) >= ?", (since,)
        ) as cur:
            (new_contacts,) = await cur.fetchone()

        async with db.execute(
            "SELECT SUM(requests), SUM(prompt_t), SUM(output_t), SUM(think_t) "
            "FROM token_usage WHERE day >= ?",
            (since,),
        ) as cur:
            requests, prompt_t, output_t, think_t = await cur.fetchone()

        async with db.execute(
            "SELECT key_tail, SUM(requests), SUM(prompt_t + output_t + think_t) "
            "FROM token_usage WHERE day >= ? GROUP BY key_tail ORDER BY 2 DESC",
            (since,),
        ) as cur:
            per_key = [
                {"tail": tail, "requests": req, "tokens": tok}
                for tail, req, tok in await cur.fetchall()
            ]

    return {
        "days": days,
        "since": since,
        "incoming": incoming or 0,
        "unique_users": unique_users or 0,
        "new_contacts": new_contacts or 0,
        "ai": replies.get("ai", 0),
        "regex": replies.get("regex", 0),
        "error": replies.get("error", 0),
        "skipped": replies.get("skipped", 0),
        "paused": replies.get("paused", 0),
        "requests": requests or 0,
        "prompt_t": prompt_t or 0,
        "output_t": output_t or 0,
        "think_t": think_t or 0,
        "per_key": per_key,
    }


async def get_top_questions(days: int = 7, limit: int = 8) -> list[tuple[str, int]]:
    """Eng ko'p takrorlangan kiruvchi xabarlar."""
    since = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute(
            "SELECT lower(trim(text)) AS q, COUNT(*) AS n FROM messages "
            "WHERE day >= ? AND direction = 'in' AND length(trim(text)) BETWEEN 3 AND 80 "
            "GROUP BY q HAVING n > 1 ORDER BY n DESC LIMIT ?",
            (since, limit),
        ) as cur:
            return [(q, n) for q, n in await cur.fetchall()]


async def get_top_contacts(days: int = 7, limit: int = 5) -> list[tuple[str, int, int]]:
    """Eng faol suhbatdoshlar: (nom, user_id, xabarlar soni)."""
    since = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute(
            "SELECT COALESCE(c.username, c.full_name, CAST(m.user_id AS TEXT)), "
            "       m.user_id, COUNT(*) AS n "
            "FROM messages m LEFT JOIN contacts c ON c.user_id = m.user_id "
            "WHERE m.day >= ? AND m.direction = 'in' "
            "GROUP BY m.user_id ORDER BY n DESC LIMIT ?",
            (since, limit),
        ) as cur:
            return [(name, uid, n) for name, uid, n in await cur.fetchall()]
