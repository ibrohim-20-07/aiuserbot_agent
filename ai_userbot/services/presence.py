"""Akkaunt egasining faolligini kuzatish (avto-pauza uchun).

Mantiq: siz Telegramda o'zingiz xabar yozayotgan bo'lsangiz, AI aralashmasligi
kerak — mijoz bir vaqtning o'zida sizdan ham, botdan ham javob olmasin.

Faollik signali — akkauntdan **chiquvchi** xabar. Bu ishonchli signal, chunki:
  • siz istalgan qurilmadan yozsangiz ham qayd etiladi;
  • «online» statusidan farqli o'laroq, ilovani ochib qo'yib ketish
    faollik deb hisoblanmaydi.

Eng nozik joyi: userbot javoblari ham chiquvchi xabar hisoblanadi. Ularni
faollik deb sanasak, AI o'z javobi bilan o'zini abadiy jim qilib qo'yardi.
Shuning uchun ikki qavatli himoya:

  1. `expect_own_message()` — xabar YUBORILISHIDAN OLDIN chaqiriladi.
     Bu poyga (race) ni yopadi: Telethon chiquvchi hodisani `send`
     tugashidan oldin yetkazib berishi mumkin, ya'ni faqat ID bo'yicha
     tekshirish kech qolardi.
  2. `remember_own_message()` — yuborilgandan keyin ID bo'yicha.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from collections import deque

logger = logging.getLogger(__name__)

# Bot yuborgan oxirgi shuncha xabar ID si eslab qolinadi.
OWN_ID_MEMORY = 500
# Kutilayotgan yozuv shuncha soniyadan keyin eskirgan deb hisoblanadi.
# (Aks holda siz keyinroq xuddi shu matnni qo'lda yozsangiz, u sanalmay qolardi.)
EXPECTATION_TTL = 120.0


class PresenceTracker:
    """Akkaunt egasi oxirgi marta qachon o'zi yozganini biladi."""

    def __init__(self) -> None:
        self._last_activity: float | None = None
        self._last_activity_at: dt.datetime | None = None
        self._own_ids: deque[int] = deque(maxlen=OWN_ID_MEMORY)
        self._own_lookup: set[int] = set()
        # (chat_id, matn) -> qachon kutila boshlandi
        self._expected: dict[tuple[int, str], float] = {}

    # ------------------------------------------------------------------ #
    # Bot yuborgan xabarlarni ajratish
    # ------------------------------------------------------------------ #
    def expect_own_message(self, chat_id: int, text: str) -> None:
        """Bot xabar yuborishidan OLDIN chaqiriladi (poygani yopadi)."""
        self._purge_expectations()
        self._expected[(chat_id, text)] = time.monotonic()

    def remember_own_message(self, message_id: int | None) -> None:
        """Bot yuborgan xabar ID sini eslab qoladi (ikkinchi qavat himoya)."""
        if message_id is None:
            return
        if len(self._own_ids) == self._own_ids.maxlen:
            self._own_lookup.discard(self._own_ids[0])
        self._own_ids.append(message_id)
        self._own_lookup.add(message_id)

    def _purge_expectations(self) -> None:
        now = time.monotonic()
        stale = [k for k, ts in self._expected.items() if now - ts > EXPECTATION_TTL]
        for key in stale:
            del self._expected[key]

    # ------------------------------------------------------------------ #
    # Faollik
    # ------------------------------------------------------------------ #
    def note_outgoing(
        self, message_id: int | None, chat_id: int | None = None, text: str | None = None
    ) -> bool:
        """Chiquvchi xabarni qayd etadi.

        Bot o'zi yuborgan bo'lsa `False` qaytaradi va faollik yangilanmaydi.
        """
        if message_id is not None and message_id in self._own_lookup:
            return False

        if chat_id is not None and text is not None:
            key = (chat_id, text)
            if key in self._expected:
                del self._expected[key]
                self.remember_own_message(message_id)
                return False

        self._last_activity = time.monotonic()
        self._last_activity_at = dt.datetime.now()
        logger.debug("Egasining faolligi qayd etildi (chat=%s).", chat_id)
        return True

    def seconds_since_activity(self) -> float | None:
        """Oxirgi faollikdan beri o'tgan soniyalar (hech qachon bo'lmasa `None`)."""
        if self._last_activity is None:
            return None
        return time.monotonic() - self._last_activity

    def is_owner_active(self, window_seconds: float) -> bool:
        """Egasi so'nggi `window_seconds` ichida o'zi yozganmi?"""
        elapsed = self.seconds_since_activity()
        return elapsed is not None and elapsed < window_seconds

    def last_activity_at(self) -> dt.datetime | None:
        return self._last_activity_at

    def describe(self, window_seconds: float) -> str:
        """Admin panel uchun qisqa holat matni."""
        elapsed = self.seconds_since_activity()
        if elapsed is None:
            return "hali faollik qayd etilmagan"
        minutes = int(elapsed // 60)
        ago = f"{minutes} daqiqa oldin" if minutes else "hozirgina"
        if elapsed < window_seconds:
            left = int((window_seconds - elapsed) // 60) + 1
            return f"siz {ago} yozgansiz — AI jim ({left} daq. qoldi)"
        return f"oxirgi faolligingiz {ago} — AI ishlayapti"


# Butun loyiha uchun yagona nusxa.
presence_tracker = PresenceTracker()
