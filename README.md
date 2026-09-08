# 🤖 Telegram AI Assistant — Userbot + Admin Bot

Shaxsiy Telegram akkauntingizga kelgan **shaxsiy xabarlarga** avtomatik javob beruvchi tizim.
Sozlamalar alohida **admin bot** orqali jonli boshqariladi.

- **Telethon** — userbot (shaxsiy akkaunt)
- **Aiogram 3.x** — admin bot (inline menyu + FSM)
- **Google GenAI SDK** — `gemini-3.5-flash-lite`, kalitlar rotatsiyasi bilan
- **aiosqlite** — yengil `settings.db`

## Ishlash mantig'i (2 qatlam)

| Qatlam | Trigger | Xarajat |
|---|---|---|
| 1️⃣ Regex | `karta`, `kartangiz`, `hisob raqam`, `реквизиты`, `qayerga to'lov`… | **0 token** — bazadagi karta matni darhol yuboriladi |
| 2️⃣ Gemini | qolgan barcha tabiiy savollar | oxirgi 8 ta xabar konteksti bilan |

Karta raqamini AI generatsiya qilmaydi — bitta raqam almashsa pul begonaga ketadi.
Shuning uchun 1-qatlam bazadagi matnni **belgima-belgi** yuboradi.

### Regex qachon ISHLAMAYDI

Karta so'zi bo'lsa ham quyidagi holatlarda xabar AI ga uzatiladi
(`NEGATIVE_PATTERNS`, [filters.py](ai_userbot/userbot/filters.py)):

| Holat | Misol |
|---|---|
| To'lov allaqachon qilingan | «kartangizga pul tashladim, chek mana» |
| Chek / skrinshot yuborilmoqda | «chek yubordim» |
| Mijozning **o'z** kartasi | «kartam bloklanib qoldi» |
| Karta bilan muammo | «kartaning muddati tugagan» |
| To'lov **usuli** haqidagi savol | «karta orqali to'lasa bo'ladimi?» |
| Karta **tafsiloti** haqidagi savol | «bu qaysi bank kartasi?», «kimning nomiga?» |

Regex javob berganda ham almashinuv Gemini kontekstiga yoziladi
(`GeminiService.remember()`, token sarflamaydi) — shunda mijoz keyin
«bu qaysi bank?» deb so'rasa, AI nima haqida gap ketayotganini biladi.

Javobdan oldin chatda `typing` ko'rsatiladi va 1–2 soniya kechikish qo'shiladi (anti-spam).

## Struktura

```text
ai_userbot/
├── config.py                 # .env yuklovchi sozlamalar
├── database.py               # aiosqlite: settings + stats
├── main.py                   # Userbot va Admin botni birga ishga tushiradi
├── services/gemini_service.py# API key rotation + sliding window kontekst
├── userbot/client.py         # Telethon hodisalari
├── userbot/filters.py        # Regex filtrlari
└── admin_bot/                # bot.py · handlers.py · keyboards.py
```

## O'rnatish

```bash
python -m venv .venv
.venv\Scripts\activate        # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

copy .env.example .env        # Linux/macOS: cp .env.example .env
# .env ni to'ldiring
```

Kerakli qiymatlar:

| O'zgaruvchi | Qayerdan olinadi |
|---|---|
| `API_ID`, `API_HASH` | https://my.telegram.org → API development tools |
| `BOT_TOKEN` | [@BotFather](https://t.me/BotFather) |
| `ADMIN_ID` | [@userinfobot](https://t.me/userinfobot) |
| `GEMINI_API_KEYS` | https://aistudio.google.com/apikey (2–3 ta, vergul bilan) |

## Ishga tushirish

```bash
python -m ai_userbot.main
```

Birinchi ishga tushirishda Telethon telefon raqam va Telegramdan kelgan kodni so'raydi
(bir marta) — so'ng `userbot.session` fayli yaratiladi.

## Admin bot menyusi (`/menu`)

- 🤖 **AI holati** — javob berishni ON/OFF qilish
- 💳 **Karta avto-javobi** — regex qatlamini butunlay o'chirish
- 👥 **Ro'yxatlar** — kimga javob berishni boshqarish (pastda)
- 📝 **Promptni tahrirlash** — `system_instruction` ni bevosita botdan yangilash
- 💳 **Karta raqami** — regex qaytaradigan matnni o'zgartirish
- 📊 **Statistika** — kunlik/haftalik hisobot, token sarfi, kalitlar
- 🧹 **Kontekstni tozalash** — barcha suhbat xotirasini o'chirish

Sozlama o'zgarishi bilan userbot **keyingi xabardayoq** yangi qiymatni bazadan o'qiydi.

## 👥 Kimga javob berish (oq / qora ro'yxat)

Uchta rejim, admin botdan almashtiriladi:

| Rejim | Xatti-harakat |
|---|---|
| **Hammaga** | Ro'yxatlar e'tiborga olinmaydi |
| **Faqat oq ro'yxat** | Ro'yxatdagilardan boshqasiga umuman javob bermaydi (test uchun qulay) |
| **Qora ro'yxatdan tashqari** | Tanishlaringiz, hamkorlaringiz bilan yozishmaga bot aralashmaydi |

Ro'yxatga ID yoki `@username` bilan qo'shiladi (bir nechtasini qatorma-qator).
`@username` faqat oldin sizga yozgan odamlar uchun topiladi — qolganlariga raqamli ID kerak.
Izoh qo'shsa ham bo'ladi: `123456789 aka`.

**Javob berilmagan xabarlar ham arxivga tushadi** — filtr javobni to'xtatadi, yozuvni emas.

## 📁 Maxfiy arxiv kanali

Mijoz xabarini o'chirsa, suhbatni tozalasa yoki akkaunt bloklansa — yozishmalar yo'qoladi.
Har bir almashinuv ikki joyga yoziladi: **bazaga** (`messages` jadvali, 90 kun saqlanadi)
va **maxfiy kanalga**.

Sozlash:

1. Telegramda yopiq kanal yarating
2. Admin botingizni kanalga **admin** qilib qo'shing (post yuborish huquqi bilan)
3. Kanal ID sini oling ([@getmyid_bot](https://t.me/getmyid_bot)) va `.env` ga yozing:
   `LOG_CHANNEL_ID=-1001234567890`

`LOG_CHANNEL_ID` bo'sh bo'lsa kanal arxivi o'chiq qoladi, baza baribir yozib boradi.

Kanaldagi yozuv:

```text
👤 Ali Valiyev (@aka_ali) 🆕
555 · ochish · 28.08 20:09

💬 Mijoz:
kartangizni yuboring

⚡️ Regex:
8600 1111 2222 3333
```

Yuborish alohida navbat orqali ketadi — userbot kanalni kutib turmaydi. Telegram
flood-limiti (`RetryAfter`) avtomatik hisobga olinadi, navbat to'lib ketsa eski
yozuvlar tashlanadi (baza baribir saqlaydi).

## 📊 Statistika

Bugun / 7 kun / 30 kun kesimida:

- Kelgan xabarlar, necha kishidan, **nechta yangi mijoz**
- Javob berilganlar: AI / regex taqsimoti va javob berish foizi
- O'tkazib yuborilganlar (ro'yxat filtri) va xatolar
- **Token sarfi**: kirish / chiqish / fikrlash, o'rtacha so'rov narxi
- **🔑 Kalitlar** — har bir kalitning so'rovlari, tokenlari va cooldown sababi
- **🔥 Top savollar** — eng ko'p takrorlangan savollar (ularga tayyor javobni
  promptga qo'shsangiz, AI aniqroq va tejamkorroq ishlaydi)
- Eng faol suhbatdoshlar

## Kalitlar rotatsiyasi

| Xato | Reaksiya |
|---|---|
| `429` / `RESOURCE_EXHAUSTED` | kalit 60 s chetlatiladi, so'rov keyingi kalit bilan qayta yuboriladi |
| `401` / `403` (yaroqsiz kalit) | kalit 1 soatga chetlatiladi |
| `5xx` | qisqa pauza va keyingi kalit bilan qayta urinish |
| `404` (model topilmadi) | darhol to'xtaydi — kalit aybdor emas, `GEMINI_MODEL` ni tuzating |
| `400` (so'rov xatosi) | darhol to'xtaydi — barcha kalitlarda takrorlanadi |

## ⚠️ Bepul kvota — eng muhim cheklov

Google hujjatida aniq yozilgan: **«Rate limits are applied per project, not per API key.»**
Ya'ni bitta Google Cloud loyihasida yaratilgan 5 ta kalit ham bitta kvotani bo'lishadi —
rotatsiya hech narsa bermaydi.

**Rotatsiya ishlashi uchun har bir kalit alohida loyihada yaratilgan bo'lishi shart:**
AI Studio → Get API key → *Create API key in new project*.

Bepul kvota model bo'yicha keskin farq qiladi (kunlik limitni
[aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit) da ko'rish mumkin):

| Model | Tezlik | Fikrlash tokeni | Izoh |
|---|---|---|---|
| `gemini-3.5-flash-lite` | ~1.2 s | 0 | **standart** — userbot uchun eng maqbuli |
| `gemini-3.1-flash-lite` | ~1.5 s | ~130 | muqobil |
| `gemini-3.5-flash` | ~4.7 s | ~350 | sifatliroq, sekinroq |
| `gemini-3.6-flash` | ~3 s | ~300–450 | bepul kvota atigi **20 so'rov/kun** |
| `gemini-2.5-flash` | — | — | yangi akkauntlar uchun yopilgan (404) |

Kod `429` javobidagi `quotaId` ni o'qiydi va limit turini farqlaydi:
kunlik limit (`…PerDay…`) tugasa kalit **Tinch okeani vaqti bilan yarim tungacha**
chetlatiladi (har 60 soniyada behuda urinmaydi), daqiqalik limitda esa `retryDelay`
qiymati ishlatiladi. Sabab admin botning 📊 Statistika bo'limida ko'rinadi.

## ⚠️ Model va token budjeti

`gemini-2.5-flash` yangi akkauntlar uchun yopilgan (`404 NOT_FOUND`) — shuning uchun
standart model `gemini-3.5-flash-lite`.

Gemini 3.x da fikrlash `thinking_level` (`low`/`high`) bilan boshqariladi;
eski `thinking_budget` yuborilsa `400 INVALID_ARGUMENT` qaytadi. Kod buni model
nomiga qarab avtomatik tanlaydi.

**Muhim:** fikrlash tokenlari ham `MAX_OUTPUT_TOKENS` budjetidan yeyiladi — bitta
oddiy savolga ~300–450 token ketadi. Shu sababli budjet `1000` qilib qo'yilgan;
300 ga tushirsangiz javoblar o'rtasidan kesiladi (`MAX_TOKENS`).

## Eslatma

Userbotlar Telegram ToS bo'yicha xavf ostida: flood/spam qilmang, javob kechikishlarini
(`REPLY_DELAY_MIN/MAX`) juda kichik qilmang.
