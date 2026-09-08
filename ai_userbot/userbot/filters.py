"""1-qatlam: AI ga bormasdan, regex orqali aniqlanadigan so'rovlar."""
from __future__ import annotations

import re

# Turli xil apostroflarni bitta ko'rinishga keltirish uchun.
_APOSTROPHES = str.maketrans({"'": "'", "`": "'", "’": "'", "‘": "'"})

# BIZNING karta / to'lov rekvizitlarimiz so'ralganda ishlaydigan naqshlar.
#
# DIQQAT: naqshlar ataylab TOR — faqat 2-shaxs egalik shakli («kartangiz»,
# «raqamingiz») yoki bizga qaratilgan buyruq («karta raqamini yuboring»)
# hisobga olinadi. Shunchaki «karta» so'zi yetarli emas, chunki mijozning
# o'zi karta raqamini yuborayotgan bo'lishi mumkin.
CARD_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 2-shaxs egalik: kartangiz, kartangizni, kartangizga, plastikangiz…
    re.compile(r"\bkarta\w*ngiz\w*\b", re.IGNORECASE),
    re.compile(r"\bplastik\w*ngiz\w*\b", re.IGNORECASE),
    re.compile(r"\b(karta|hisob|schy?ot)\w*\s+raqam\w*ngiz\w*\b", re.IGNORECASE),
    re.compile(r"\braqam\w*ngiz\w*\b.{0,15}\b(karta|hisob)\w*\b", re.IGNORECASE),
    # Rekvizit so'zi o'zi 2-shaxsni anglatadi.
    re.compile(r"\brekvizit\w*\b", re.IGNORECASE),
    # Bizga qaratilgan buyruq: «karta raqamini yuboring / tashlang / bering».
    # Mijozning taklifi («yuboraymi», «tashlaymi») bu naqshga tushmaydi.
    # «raqam» so'zi MAJBURIY: «kartaga tashlang» — bu mijoz pulni o'z
    # kartasiga so'rayapti, rekvizit emas.
    re.compile(
        r"\b(karta|hisob|schy?ot)\w*\s+raqam\w*\s+"
        r"(yuboring|tashlang|bering|jo'?nating|yozing|yuboring-?chi)\b",
        re.IGNORECASE,
    ),
    # To'lov qayerga qilinishi so'ralganda.
    re.compile(r"\bqa[yj]?er(ga|da)\b.{0,25}\bto'?lo\w*\b", re.IGNORECASE),
    re.compile(r"\bto'?lo\w*\b.{0,25}\bqa[yj]?er(ga|da)\b", re.IGNORECASE),
    # --- ruscha ---
    re.compile(r"\bваш\w*\s+(карт|реквизит|сч[её]т)\w*\b", re.IGNORECASE),
    re.compile(r"\b(карт|сч[её]т)\w*\s+ваш\w*\b", re.IGNORECASE),
    re.compile(r"\bреквизит\w*\b", re.IGNORECASE),
    re.compile(
        r"\bномер\s+карт\w*\b.{0,25}\b(скинь\w*|отправь\w*|дай\w*|можно|киньте)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(скинь\w*|отправь\w*|дай\w*|киньте)\b.{0,25}\bномер\s+карт\w*\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bкуда\b.{0,20}\bоплат\w*\b", re.IGNORECASE),
)

# Karta so'zi uchrasa ham regex ISHLAMASLIGI kerak bo'lgan holatlar.
# Bular kontekstni o'zgartiradi: mijoz karta so'ramayapti, balki to'lov
# qilganini aytyapti yoki o'z kartasi haqida gapiryapti. Bunday xabarlar
# AI ga uzatiladi.
NEGATIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 1) To'lov allaqachon qilingan (o'tgan zamon).
    #    to'ladim / tashladim / o'tkazdim / yubordim / oshirgan…
    #    Buyruq shakllari (yuboring, tashlang) bu naqshga TUSHMAYDI.
    re.compile(
        r"\b(to'?la|tashla|o'?tkaz|yubor|jo'?nat|tushir|oshir|sol)(di|gan)\w*\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bto'?lab\s+(bo'?ld|qo'?yd|yubord)\w*\b", re.IGNORECASE),
    # 2) Chek / kvitansiya yuborilmoqda.
    re.compile(r"\b(chek|kvitansiya|skrin\w*|screenshot)\b", re.IGNORECASE),
    # 3) Mijozning O'Z kartasi haqida (bizning rekvizit so'ralmayapti).
    re.compile(r"\bkarta(m|mni|mga|mda|mdan|miz\w*)\b", re.IGNORECASE),
    re.compile(r"\bmening\s+kart\w*\b", re.IGNORECASE),
    # 4) Karta bilan muammo.
    re.compile(r"\b(bloklan\w*|ishlamay\w*|ishlamad\w*|yo'?qol\w*|o'?g'?irla\w*)\b", re.IGNORECASE),
    re.compile(r"\bmuddati\s+tuga\w*\b", re.IGNORECASE),
    # 5) Ruscha: to'lov qilingan / muammo (infinitiv «оплатить» BUNGA KIRMAYDI).
    re.compile(
        r"\b(оплатил\w*|оплачен\w*|перев[её]л\w*|отправил\w*|скинул\w*|"
        r"закинул\w*|кинул\w*|заблокир\w*)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(чек|квитанци\w*|скрин\w*)\b", re.IGNORECASE),
    re.compile(r"\bмо[яейи]\s+карт\w*\b", re.IGNORECASE),
    re.compile(r"\bне\s+работает\b", re.IGNORECASE),
    # 7) Karta HAQIDAGI savol — raqam allaqachon yuborilgan, mijoz
    #    tafsilot so'rayapti («qaysi bank?», «kimning nomiga?»).
    re.compile(r"\bqa(y|na)\w*\s+bank\w*\b", re.IGNORECASE),
    re.compile(r"\bbank\w*\s+(qaysi|qanaqa|nima|nomi)\b", re.IGNORECASE),
    re.compile(r"\bbank(ning)?\s+nom\w*\b", re.IGNORECASE),
    re.compile(r"\bkim(ning)?\s+nom\w*\b", re.IGNORECASE),
    re.compile(r"\bnomiga\b", re.IGNORECASE),
    re.compile(r"\begas\w*\s+kim\b", re.IGNORECASE),
    re.compile(r"\bкак(ой|ого|ая)\s+банк\w*\b", re.IGNORECASE),
    re.compile(r"\bна\s+чь[её]\s+имя\b", re.IGNORECASE),
    # 6) To'lov USULI haqidagi savol — rekvizit emas, ha/yo'q javobi kerak.
    #    «karta orqali to'lasa bo'ladimi?» ha, lekin
    #    «kartangizni yuborsangiz bo'ladimi?» yo'q (rekvizit so'ralyapti).
    re.compile(
        r"\bkarta\w*\s+(orqali|bilan|bo'?yicha)\b.{0,30}\b(bo'?ladimi|mumkinmi|mumkinmi|bo'?lardi)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(можно|можете)\b.{0,20}\bоплат\w*\b.{0,20}\bкарт\w*\b",
        re.IGNORECASE,
    ),
)

# Javob berilmasligi kerak bo'lgan texnik xabarlar (servis kodlari va h.k.).
SERVICE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*/(start|help|stop)\b", re.IGNORECASE),
    re.compile(r"\blogin\s+code\b", re.IGNORECASE),
    re.compile(r"\bkirish\s+kodi\b", re.IGNORECASE),
)


def normalize(text: str) -> str:
    """Apostroflarni birxillashtiradi va ortiqcha bo'shliqlarni olib tashlaydi."""
    return re.sub(r"\s+", " ", (text or "").translate(_APOSTROPHES)).strip()


def is_card_request(text: str) -> bool:
    """Matnda BIZNING karta / to'lov rekvizitlarimiz so'ralganmi?

    Avval inkor signallari tekshiriladi: «kartangizga pul tashladim»,
    «kartam bloklandi», «chek yubordim» kabi xabarlarda karta so'zi bor,
    lekin rekvizit so'ralmayapti — ular AI ga uzatiladi.
    """
    cleaned = normalize(text)
    if not cleaned:
        return False
    if any(pattern.search(cleaned) for pattern in NEGATIVE_PATTERNS):
        return False
    return any(pattern.search(cleaned) for pattern in CARD_PATTERNS)


def is_service_message(text: str) -> bool:
    """Javob berish shart bo'lmagan xizmat xabarimi?"""
    cleaned = normalize(text)
    if not cleaned:
        return True
    return any(pattern.search(cleaned) for pattern in SERVICE_PATTERNS)
