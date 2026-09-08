"""Gemini bilan ishlovchi servis: kalitlar rotatsiyasi + kontekst xotirasi."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import time
from collections import defaultdict, deque
from typing import Any, Deque

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from ..config import config
from ..database import record_usage

logger = logging.getLogger(__name__)

# Daqiqalik limit (RPM) urilganda kalit shuncha soniya "dam oladi".
COOLDOWN_SECONDS = 60.0
# Kunlik limit (RPD) tugaganda kalit ertangi kungacha chetlatiladi.
# Google bepul kvotasi Tinch okeani vaqti bilan yarim tunda yangilanadi.
QUOTA_RESET_TZ = "America/Los_Angeles"
DAILY_FALLBACK_COOLDOWN = 3600.0
# Bitta xabar uchun umumiy urinishlar chegarasi (kalitlar soniga bog'liq).
RETRY_SLEEP = 1.0


def _extract_quota_info(exc: genai_errors.APIError) -> dict[str, Any]:
    """429 javobidan retryDelay va kvota tafsilotlarini ajratib oladi."""
    info: dict[str, Any] = {"retry_after": None, "quota_id": None, "quota_value": None}
    details = getattr(exc, "details", None)
    error = details.get("error", details) if isinstance(details, dict) else {}

    for item in (error or {}).get("details", []) or []:
        kind = str(item.get("@type", ""))
        if "RetryInfo" in kind:
            match = re.match(r"(\d+(?:\.\d+)?)s", str(item.get("retryDelay", "")))
            if match:
                info["retry_after"] = float(match.group(1))
        elif "QuotaFailure" in kind:
            for violation in item.get("violations", []) or []:
                info["quota_id"] = violation.get("quotaId") or info["quota_id"]
                info["quota_value"] = violation.get("quotaValue") or info["quota_value"]
    return info


def _seconds_until_daily_reset() -> float:
    """Bepul kunlik kvota yangilanishigacha qolgan soniyalar."""
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(QUOTA_RESET_TZ)
        now = dt.datetime.now(tz)
        midnight = dt.datetime.combine(
            now.date() + dt.timedelta(days=1), dt.time.min, tzinfo=tz
        )
        return max((midnight - now).total_seconds(), 60.0)
    except Exception:  # tzdata o'rnatilmagan bo'lsa
        logger.debug("Vaqt mintaqasi topilmadi, zaxira cooldown ishlatiladi.")
        return DAILY_FALLBACK_COOLDOWN


class GeminiService:
    """Bir nechta bepul API kalit bilan ishlovchi, kontekstni eslab qoluvchi servis."""

    def __init__(
        self,
        api_keys: list[str] | None = None,
        model: str | None = None,
        max_output_tokens: int | None = None,
        history_limit: int | None = None,
    ) -> None:
        self.api_keys = list(api_keys or config.gemini_api_keys)
        if not self.api_keys:
            raise RuntimeError("Kamida bitta GEMINI_API_KEYS qiymati kerak.")

        self.model = model or config.gemini_model
        self.max_output_tokens = max_output_tokens or config.max_output_tokens
        self.history_limit = history_limit or config.history_limit
        self.thinking_level = config.thinking_level

        self._clients: dict[str, genai.Client] = {}
        self._cooldown_until: dict[str, float] = {}
        self._cooldown_reason: dict[str, str] = {}
        self._index = 0
        self._lock = asyncio.Lock()

        # user_id -> oxirgi N ta xabar (sliding window)
        self._history: dict[int, Deque[types.Content]] = defaultdict(
            lambda: deque(maxlen=self.history_limit)
        )
        self._usage: dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------ #
    # Kalitlarni boshqarish
    # ------------------------------------------------------------------ #
    def _client_for(self, api_key: str) -> genai.Client:
        client = self._clients.get(api_key)
        if client is None:
            client = genai.Client(api_key=api_key)
            self._clients[api_key] = client
        return client

    def _is_cooling(self, api_key: str) -> bool:
        until = self._cooldown_until.get(api_key, 0.0)
        return until > time.monotonic()

    async def _next_key(self) -> tuple[str, int] | None:
        """Navbatdagi ishlaydigan kalitni qaytaradi (round-robin)."""
        async with self._lock:
            total = len(self.api_keys)
            for offset in range(total):
                idx = (self._index + offset) % total
                key = self.api_keys[idx]
                if not self._is_cooling(key):
                    self._index = (idx + 1) % total
                    return key, idx
        return None

    def _mark_exhausted(self, api_key: str, quota: dict[str, Any] | None = None) -> None:
        """429 dan keyin kalitni chetlatadi.

        Kunlik (RPD) va daqiqalik (RPM) limit farqlanadi: kunlik limit
        tugaganda kalitni har 60 soniyada qayta sinash befoyda — u ertangi
        kungacha ishlamaydi.
        """
        quota = quota or {}
        quota_id = str(quota.get("quota_id") or "")
        is_daily = "PerDay" in quota_id

        if is_daily:
            cooldown = _seconds_until_daily_reset()
            reason = f"kunlik limit tugadi ({quota.get('quota_value') or '?'}/kun)"
        else:
            cooldown = max(float(quota.get("retry_after") or 0.0), COOLDOWN_SECONDS)
            reason = "daqiqalik limit (RPM)"

        self._cooldown_until[api_key] = time.monotonic() + cooldown
        self._cooldown_reason[api_key] = reason
        logger.warning(
            "Kalit ...%s chetlatildi: %s — %.0f daqiqadan keyin qayta sinaladi.",
            api_key[-4:],
            reason,
            cooldown / 60,
        )

    def key_status(self) -> list[dict[str, object]]:
        """Admin bot statistikasi uchun kalitlar holati."""
        now = time.monotonic()
        status: list[dict[str, object]] = []
        for idx, key in enumerate(self.api_keys, start=1):
            wait = max(0.0, self._cooldown_until.get(key, 0.0) - now)
            status.append(
                {
                    "index": idx,
                    "tail": key[-4:],
                    "active": wait == 0.0,
                    "cooldown": int(wait),
                    "reason": self._cooldown_reason.get(key, "") if wait else "",
                    "requests": self._usage.get(key, 0),
                }
            )
        return status

    # ------------------------------------------------------------------ #
    # Model sozlamalari
    # ------------------------------------------------------------------ #
    def _thinking_config(self) -> types.ThinkingConfig:
        """Fikrlash sozlamasi model oilasiga qarab farq qiladi.

        Gemini 3.x — `thinking_level` ("low" / "high"); `thinking_budget`
        yuborilsa 400 INVALID_ARGUMENT qaytadi.
        Gemini 2.x — `thinking_budget=0` bilan fikrlashni butunlay o'chirish
        mumkin.

        Diqqat: fikrlash tokenlari ham `max_output_tokens` budjetidan
        yeyiladi (3.x da ~300-450 token), shuning uchun budjet yetarlicha
        katta bo'lishi kerak, aks holda javob MAX_TOKENS bilan kesiladi.
        """
        family = self.model.removeprefix("models/")
        if family.startswith("gemini-3") or family.startswith("gemini-4"):
            return types.ThinkingConfig(thinking_level=self.thinking_level)
        return types.ThinkingConfig(thinking_budget=0)

    # ------------------------------------------------------------------ #
    # Kontekst xotirasi
    # ------------------------------------------------------------------ #
    @staticmethod
    def _content(role: str, text: str) -> types.Content:
        return types.Content(role=role, parts=[types.Part.from_text(text=text)])

    def reset_history(self, user_id: int | None = None) -> None:
        if user_id is None:
            self._history.clear()
        else:
            self._history.pop(user_id, None)

    def remember(self, user_id: int, user_text: str, bot_text: str) -> None:
        """Almashinuvni token sarflamasdan kontekstga yozadi.

        Regex qatlami javob berganda (masalan karta raqami) API chaqirilmaydi,
        lekin AI keyingi savolda («bu qaysi bank?») nima haqida gap ketayotganini
        bilishi uchun bu almashinuv ham tarixga tushishi kerak.
        """
        history = self._history[user_id]
        history.append(self._content("user", user_text))
        history.append(self._content("model", bot_text))

    def remember_user(self, user_id: int, user_text: str) -> None:
        """Foydalanuvchi yuborgan xabarni (masalan, avto-pauzada) xotiraga yozadi."""
        if not user_text:
            return
        self._history[user_id].append(self._content("user", user_text))

    def remember_reply(self, user_id: int, reply_text: str) -> None:
        """Egasi yoki assistent yozgan javobni xotiraga yozadi."""
        if not reply_text:
            return
        self._history[user_id].append(self._content("model", reply_text))

    def history_size(self, user_id: int) -> int:
        return len(self._history[user_id])

    def get_context_stats(self) -> dict[str, int]:
        """Kontekst xotirasi statistikasini qaytaradi."""
        active_users = len(self._history)
        total_messages = sum(len(d) for d in self._history.values())
        return {
            "active_users": active_users,
            "total_messages": total_messages,
            "history_limit": self.history_limit,
        }

    def set_history_limit(self, limit: int) -> None:
        """Kontekst xabarlar chegarasini o'zgartiradi va mavjud navbatlarni yangilaydi."""
        self.history_limit = max(2, limit)
        for user_id, old_deque in list(self._history.items()):
            self._history[user_id] = deque(old_deque, maxlen=self.history_limit)

    # ------------------------------------------------------------------ #
    # Asosiy so'rov
    # ------------------------------------------------------------------ #
    async def generate(
        self,
        user_id: int,
        text: str,
        system_instruction: str,
    ) -> str | None:
        """Foydalanuvchi xabariga javob matnini qaytaradi (xato bo'lsa `None`)."""
        # Agar xotira bo'sh bo'lsa, bazadan oxirgi suhbat tarixini olib tiklaymiz
        if len(self._history[user_id]) == 0:
            try:
                from ..database import get_history_limit, get_recent_messages

                self.history_limit = await get_history_limit()
                recent = await get_recent_messages(user_id, limit=self.history_limit)
                for item in recent:
                    self._history[user_id].append(self._content(item["role"], item["text"]))
            except Exception:
                logger.debug("Bazadan kontekstni tiklashda xatolik:", exc_info=True)

        history = self._history[user_id]
        user_message = self._content("user", text)
        contents = [*history, user_message]

        gen_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            max_output_tokens=self.max_output_tokens,
            temperature=0.7,
            thinking_config=self._thinking_config(),
            safety_settings=[
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                    threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                    threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                    threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                    threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY,
                    threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
                ),
            ],
        )

        attempts = len(self.api_keys)
        for attempt in range(attempts):
            picked = await self._next_key()
            if picked is None:
                logger.error("Barcha API kalitlar vaqtincha limitda.")
                return None

            api_key, idx = picked
            try:
                response = await self._client_for(api_key).aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=gen_config,
                )
            except genai_errors.APIError as exc:
                code = getattr(exc, "code", None)
                message = str(exc)
                if code == 429 or "RESOURCE_EXHAUSTED" in message.upper():
                    self._mark_exhausted(api_key, _extract_quota_info(exc))
                    continue
                if code in (500, 502, 503, 504):
                    logger.warning("Gemini server xatosi (%s), qayta urinamiz.", code)
                    await asyncio.sleep(RETRY_SLEEP)
                    continue
                if code in (401, 403):
                    # Kalit yaroqsiz yoki bloklangan — uzoq muddatga chetlatamiz.
                    logger.error("Kalit #%s rad etildi (%s): %s", idx + 1, code, message)
                    self._cooldown_until[api_key] = time.monotonic() + 3600
                    continue
                if code == 404:
                    # Model nomi noto'g'ri yoki bu akkaunt uchun yopilgan.
                    # Kalitni ayblash noto'g'ri — boshqa kalitlarda ham xuddi shu xato.
                    logger.error(
                        "Model «%s» topilmadi yoki mavjud emas: %s. "
                        "GEMINI_MODEL qiymatini .env da yangilang.",
                        self.model,
                        message,
                    )
                    return None
                if code == 400:
                    # So'rov xatosi (masalan modelga mos kelmaydigan parametr).
                    # Barcha kalitlarda takrorlanadi — qayta urinish befoyda.
                    logger.error("Gemini so'rovi rad etildi (400): %s", message)
                    return None
                logger.error("Gemini API kutilmagan xatosi (%s): %s", code, message)
                return None
            except Exception:  # tarmoq uzilishi va h.k.
                logger.exception("Gemini so'rovida kutilmagan xato (urinish %s)", attempt + 1)
                await asyncio.sleep(RETRY_SLEEP)
                continue

            try:
                answer = (response.text or "").strip()
            except Exception as e:
                logger.warning("Gemini javob matnini o'qib bo'lmadi (ehtimol xavfsizlik filtri): %s", e)
                answer = ""

            finish = next(
                (c.finish_reason for c in (response.candidates or []) if c.finish_reason),
                None,
            )
            if finish and "SAFETY" in str(finish):
                logger.warning("Gemini xavfsizlik filtri (SAFETY) sababli javob bloklandi.")
                return None
            if finish and "MAX_TOKENS" in str(finish):
                logger.warning(
                    "Javob MAX_TOKENS bilan kesildi — MAX_OUTPUT_TOKENS (%s) ni oshiring.",
                    self.max_output_tokens,
                )
            if not answer:
                logger.warning("Gemini bo'sh javob qaytardi (finish_reason=%s).", finish)
                return None

            self._usage[api_key] += 1
            self._cooldown_reason.pop(api_key, None)

            usage = response.usage_metadata
            if usage is not None:
                try:
                    await record_usage(
                        api_key[-4:],
                        prompt_t=usage.prompt_token_count or 0,
                        output_t=usage.candidates_token_count or 0,
                        think_t=usage.thoughts_token_count or 0,
                    )
                except Exception:
                    # Statistika yozilmasa ham javob berishga to'sqinlik qilmasin.
                    logger.debug("Token sarfini yozib bo'lmadi.", exc_info=True)
            history.append(user_message)
            history.append(self._content("model", answer))
            return answer

        logger.error("Barcha kalitlar sinovdan o'tdi, javob olinmadi.")
        return None


# Butun loyiha uchun yagona nusxa.
gemini_service = GeminiService()
