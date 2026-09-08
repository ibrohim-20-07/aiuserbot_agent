"""Admin bot handlerlari: sozlamalar, ro'yxatlar, prompt, karta, statistika, akkaunt."""
from __future__ import annotations

import html
import logging
import re

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

from ..config import config
from ..database import (
    LIST_BLACK,
    LIST_LABELS,
    LIST_WHITE,
    MODE_LABELS,
    PAUSE_CHOICES,
    add_to_list,
    find_contact,
    get_access_mode,
    get_auto_pause_minutes,
    get_list,
    get_period_report,
    get_setting,
    get_top_contacts,
    get_top_questions,
    is_ai_active,
    is_auto_pause_active,
    is_card_regex_active,
    remove_from_list,
    set_access_mode,
    set_auto_pause_minutes,
    set_setting,
    toggle_ai,
    toggle_auto_pause,
    toggle_card_regex,
)
from ..services.archive import archive_service
from ..services.gemini_service import gemini_service
from ..services.presence import presence_tracker
from ..userbot.manager import userbot_manager
from .keyboards import (
    CB,
    account_menu,
    back_menu,
    cancel_auth_menu,
    cancel_menu,
    confirm_logout_menu,
    list_menu,
    lists_menu,
    main_menu,
    pause_menu,
    stats_menu,
)

logger = logging.getLogger(__name__)

router = Router(name="admin")
# Butun router faqat adminga ishlaydi.
router.message.filter(F.from_user.id == config.admin_id)
router.callback_query.filter(F.from_user.id == config.admin_id)

MAX_PREVIEW = 3000


class Settings(StatesGroup):
    prompt = State()
    card = State()
    list_add = State()
    list_del = State()


class AuthStates(StatesGroup):
    phone = State()
    code = State()
    password = State()



def _clip(text: str) -> str:
    return text if len(text) <= MAX_PREVIEW else text[:MAX_PREVIEW] + "…"


def _esc(value: object) -> str:
    return html.escape(str(value or ""))


# --------------------------------------------------------------------- #
# Menyu
# --------------------------------------------------------------------- #
def _menu_text(
    ai_active: bool,
    card_regex_active: bool,
    mode: str,
    pause_active: bool = False,
    pause_minutes: int = 10,
    account_info: dict | None = None,
) -> str:
    state = "yoniq ✅" if ai_active else "o'chiq ⛔️"
    card_state = "yoniq ✅" if card_regex_active else "o'chiq ⛔️"
    if pause_active:
        pause_state = f"{pause_minutes} daq · {presence_tracker.describe(pause_minutes * 60)}"
    else:
        pause_state = "o'chiq ⛔️"

    if account_info:
        handle = (
            f"@{_esc(account_info.get('username'))}"
            if account_info.get("username")
            else _esc(account_info.get("full_name") or account_info.get("id"))
        )
        acc_state = f"ulangan ✅ ({handle})"
    else:
        acc_state = "ulanmagan ⛔️"

    arch = archive_service.stats()
    if arch["enabled"]:
        link = arch.get("link")
        opener = f' · <a href="{link}">ochish</a>' if link else ""
        arch_state = f"yoniq ✅ ({arch['sent']} yozuv){opener}"
    else:
        arch_state = "o'chiq ⛔️"

    return (
        "<b>⚙️ Userbot boshqaruv paneli</b>\n\n"
        f"📱 Akkaunt: <b>{acc_state}</b>\n"
        f"🤖 AI javob berish: <b>{state}</b>\n"
        f"💳 Karta avto-javobi: <b>{card_state}</b>\n"
        f"⏸ Avto-pauza: <b>{pause_state}</b>\n"
        f"👥 Javob rejimi: <b>{MODE_LABELS[mode]}</b>\n"
        f"📁 Arxiv kanali: <b>{arch_state}</b>\n"
        f"🧠 Model: <code>{_esc(config.gemini_model)}</code>\n"
        f"🔑 API kalitlar: <b>{len(config.gemini_api_keys)} ta</b>\n\n"
        "Kerakli bo'limni tanlang:"
    )


async def _show_menu(target: Message | CallbackQuery) -> None:
    """Menyuni yuboradi (xabar) yoki mavjudini yangilaydi (callback)."""
    ai, card, mode = await is_ai_active(), await is_card_regex_active(), await get_access_mode()
    pause, minutes = await is_auto_pause_active(), await get_auto_pause_minutes()
    acc = await userbot_manager.get_account_info()
    acc_label = f"🟢 @{acc['username'] or acc['id']}" if acc else "🔴 Ulanmagan"
    text = _menu_text(ai, card, mode, pause, minutes, acc)
    markup = main_menu(
        ai, card, MODE_LABELS[mode], f"{minutes} daq" if pause else "o'chiq", acc_label
    )
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.message(Command("start", "menu"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _show_menu(message)


@router.callback_query(F.data == CB.MENU)
async def cb_menu(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_menu(call)
    await call.answer()


@router.callback_query(F.data == CB.CANCEL)
async def cb_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show_menu(call)
    await call.answer("Bekor qilindi")


# --------------------------------------------------------------------- #
# 📱 Akkaunt boshqaruvi va Login
# --------------------------------------------------------------------- #
async def _account_view(target: Message | CallbackQuery) -> None:
    acc = await userbot_manager.get_account_info()
    if acc:
        phone_txt = f"+{acc.get('phone')}" if acc.get("phone") else "Noma'lum"
        user_handle = f"@{acc.get('username')}" if acc.get("username") else "mavjud emas"
        text = (
            "<b>📱 Telegram Akkaunt</b>\n\n"
            f"• 👤 Ism: <b>{_esc(acc.get('full_name'))}</b>\n"
            f"• 🔗 Username: <b>{_esc(user_handle)}</b>\n"
            f"• 📞 Telefon: <code>{_esc(phone_txt)}</code>\n"
            f"• 🆔 ID: <code>{acc.get('id')}</code>\n"
            "• 🟢 Holat: <b>Ulangan va faol</b>\n\n"
            "<i>Userbot mijozlar xabarlariga avtomatik javob berishga tayyor.</i>"
        )
        markup = account_menu(is_authorized=True)
    else:
        text = (
            "<b>📱 Telegram Akkaunt</b>\n\n"
            "🔴 <b>Akkaunt ulanmagan!</b>\n\n"
            "Userbot shaxsiy xabarlarga avtomatik javob berishi uchun Telegram akkauntingizni ulang.\n\n"
            "Ulash jarayoni (telefon kiritish, tasdiqlash kodi, 2FA) to'liq shu bot orqali "
            "amalga oshiriladi — terminal kerak emas."
        )
        markup = account_menu(is_authorized=False)

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.message(Command("account"))
async def cmd_account(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _account_view(message)


@router.callback_query(F.data == CB.ACCOUNT)
async def cb_account(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _account_view(call)
    await call.answer()


@router.callback_query(F.data == CB.ACCOUNT_REFRESH)
async def cb_account_refresh(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _account_view(call)
    await call.answer("Holat yangilandi 🔄")


@router.message(Command("login"))
@router.callback_query(F.data == CB.ACCOUNT_CONNECT)
async def cb_account_connect(target: Message | CallbackQuery, state: FSMContext) -> None:
    if await userbot_manager.is_authorized():
        msg = "Akkaunt allaqachon ulangan ✅"
        if isinstance(target, CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return

    await state.set_state(AuthStates.phone)
    text = (
        "<b>📱 Telegram akkauntni ulash</b>\n\n"
        "Ulanadigan akkauntingiz telefon raqamini xalqaro formatda yuboring:\n"
        "Masalan: <code>+998901234567</code>\n\n"
        "<i>Raqamga Telegram ilovasi orqali tasdiqlash kodi yuboriladi.</i>"
    )
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=cancel_auth_menu())
        await target.answer()
    else:
        await target.answer(text, reply_markup=cancel_auth_menu())


@router.message(StateFilter(AuthStates.phone), F.text)
async def process_phone(message: Message, state: FSMContext) -> None:
    raw_phone = message.text.strip()
    status_msg = await message.answer("⏳ Telegramga so'rov yuborilmoqda...")

    try:
        phone_code_hash = await userbot_manager.send_code(raw_phone)
    except PhoneNumberInvalidError:
        await status_msg.delete()
        await message.answer(
            "❌ Telefon raqami noto'g'ri kiritildi.\n"
            "Iltimos, xalqaro formatda yuboring (masalan: <code>+998901234567</code>):",
            reply_markup=cancel_auth_menu(),
        )
        return
    except PhoneNumberBannedError:
        await status_msg.delete()
        await state.clear()
        await message.answer(
            "🚫 Ushbu telefon raqam Telegram tomonidan bloklangan.",
            reply_markup=back_menu(),
        )
        return
    except FloodWaitError as exc:
        await status_msg.delete()
        await state.clear()
        await message.answer(
            f"⏳ Telegram cheklovi: Iltimos, {exc.seconds} soniya kuting.",
            reply_markup=back_menu(),
        )
        return
    except Exception as exc:
        await status_msg.delete()
        await message.answer(
            f"❌ Xatolik yuz berdi: {_esc(exc)}\n"
            "Telefon raqamini qaytadan tekshirib yuboring:",
            reply_markup=cancel_auth_menu(),
        )
        return

    await status_msg.delete()
    await state.update_data(phone=userbot_manager.phone, phone_code_hash=phone_code_hash)
    await state.set_state(AuthStates.code)

    await message.answer(
        "📩 <b>Tasdiqlash kodi yuborildi!</b>\n\n"
        "Telegram ilovangizga (yoki SMS orqali) 5 xonali tasdiqlash kodi bordi.\n\n"
        "⚠️ <b>MUHIM:</b> Telegram xavfsizlik cheklovlari (kodni yashirish/bloklash) sababli, "
        "kodni raqamlari orasiga <b>nuqta yoki probel</b> qo'yib yuboring!\n\n"
        "Misollar:\n"
        "• <code>1.2.3.4.5</code>\n"
        "• <code>1 2 3 4 5</code>\n"
        "• <code>1-2-3-4-5</code>\n\n"
        "<i>Tasdiqlash kodini yuboring:</i>",
        reply_markup=cancel_auth_menu(),
    )


@router.message(StateFilter(AuthStates.code), F.text)
async def process_code(message: Message, state: FSMContext) -> None:
    raw_code = message.text.strip()
    clean_code = "".join(re.findall(r"\d", raw_code))

    if len(clean_code) < 5:
        await message.answer(
            "⚠️ Kod kamida 5 ta raqamdan iborat bo'lishi kerak.\n\n"
            "Telegram cheklovlariga mos holatda kodni raqamlari orasiga "
            "nuqta qo'yib yuboring (masalan: <code>1.2.3.4.5</code>):",
            reply_markup=cancel_auth_menu(),
        )
        return

    data = await state.get_data()
    phone = data.get("phone")
    phone_code_hash = data.get("phone_code_hash")

    status_msg = await message.answer("⏳ Kod tekshirilmoqda...")

    try:
        user_info = await userbot_manager.sign_in_with_code(
            code=clean_code,
            phone=phone,
            phone_code_hash=phone_code_hash,
        )
        await status_msg.delete()
        await state.clear()
        phone_txt = f"+{user_info.get('phone')}" if user_info.get("phone") else ""
        handle = f"@{user_info.get('username')}" if user_info.get("username") else "yo'q"
        await message.answer(
            "🎉 <b>Akkaunt muvaffaqiyatli ulandi!</b>\n\n"
            f"• 👤 Ism: <b>{_esc(user_info.get('full_name'))}</b>\n"
            f"• 🔗 Username: <b>{_esc(handle)}</b>\n"
            f"• 📞 Telefon: <code>{_esc(phone_txt)}</code>\n"
            f"• 🆔 ID: <code>{user_info.get('id')}</code>\n\n"
            "Userbot faollashdi va xabarlarga avtomatik javob berishni boshladi! 🚀",
            reply_markup=back_menu(),
        )
    except SessionPasswordNeededError:
        await status_msg.delete()
        await state.set_state(AuthStates.password)
        await message.answer(
            "🔐 <b>Ikki bosqichli tekshiruv (2FA) yoqilgan!</b>\n\n"
            "Ushbu akkauntda bulutli parol (Cloud Password) o'rnatilgan.\n"
            "Iltimos, 2FA parolingizni yuboring.\n\n"
            "<i>Xavfsizlik uchun yuborgan xabaringiz darhol o'chiriladi.</i>",
            reply_markup=cancel_auth_menu(),
        )
    except PhoneCodeInvalidError:
        await status_msg.delete()
        await message.answer(
            "❌ Tasdiqlash kodi noto'g'ri!\n"
            "Iltimos, qaytadan tekshirib formatlangan holda yuboring (masalan: <code>1.2.3.4.5</code>):",
            reply_markup=cancel_auth_menu(),
        )
    except PhoneCodeExpiredError:
        await status_msg.delete()
        await state.clear()
        await message.answer(
            "⏱ Tasdiqlash kodining muddati tugadi. Iltimos, akkauntni ulashni qaytadan boshlang.",
            reply_markup=back_menu(),
        )
    except FloodWaitError as exc:
        await status_msg.delete()
        await state.clear()
        await message.answer(
            f"⏳ Telegram cheklovi: Iltimos, {exc.seconds} soniya kuting.",
            reply_markup=back_menu(),
        )
    except Exception as exc:
        await status_msg.delete()
        await message.answer(
            f"❌ Xatolik yuz berdi: {_esc(exc)}\n"
            "Kodni qaytadan tekshirib yuboring (masalan: <code>1.2.3.4.5</code>):",
            reply_markup=cancel_auth_menu(),
        )


@router.message(StateFilter(AuthStates.password), F.text)
async def process_password(message: Message, state: FSMContext) -> None:
    password = message.text.strip()
    try:
        await message.delete()
    except Exception:
        pass

    status_msg = await message.answer("⏳ 2FA paroli tekshirilmoqda...")

    try:
        user_info = await userbot_manager.sign_in_with_password(password)
        await status_msg.delete()
        await state.clear()
        phone_txt = f"+{user_info.get('phone')}" if user_info.get("phone") else ""
        handle = f"@{user_info.get('username')}" if user_info.get("username") else "yo'q"
        await message.answer(
            "🎉 <b>Akkaunt muvaffaqiyatli ulandi!</b>\n\n"
            f"• 👤 Ism: <b>{_esc(user_info.get('full_name'))}</b>\n"
            f"• 🔗 Username: <b>{_esc(handle)}</b>\n"
            f"• 📞 Telefon: <code>{_esc(phone_txt)}</code>\n"
            f"• 🆔 ID: <code>{user_info.get('id')}</code>\n\n"
            "Userbot faollashdi va xabarlarga avtomatik javob berishni boshladi! 🚀",
            reply_markup=back_menu(),
        )
    except PasswordHashInvalidError:
        await status_msg.delete()
        await message.answer(
            "❌ 2FA paroli noto'g'ri. Iltimos, parolingizni qaytadan kiriting:",
            reply_markup=cancel_auth_menu(),
        )
    except FloodWaitError as exc:
        await status_msg.delete()
        await state.clear()
        await message.answer(
            f"⏳ Telegram cheklovi: Iltimos, {exc.seconds} soniya kuting.",
            reply_markup=back_menu(),
        )
    except Exception as exc:
        await status_msg.delete()
        await message.answer(
            f"❌ Xatolik yuz berdi: {_esc(exc)}\nQaytadan urinib ko'ring:",
            reply_markup=cancel_auth_menu(),
        )


@router.message(Command("logout"))
@router.callback_query(F.data == CB.ACCOUNT_LOGOUT)
async def cb_account_logout(target: Message | CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    text = (
        "⚠️ <b>Akkauntni uzishni tasdiqlaysizmi?</b>\n\n"
        "Chiqsangiz, userbot faoliyati to'xtaydi va yangi akkaunt ulanmagunicha "
        "mijozlar xabarlariga avtomatik javob berilmaydi.\n\n"
        "Haqiqatan ham chiqmoqchimisiz?"
    )
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=confirm_logout_menu())
        await target.answer()
    else:
        await target.answer(text, reply_markup=confirm_logout_menu())


@router.callback_query(F.data == CB.ACCOUNT_LOGOUT_CONFIRM)
async def cb_account_logout_confirm(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    success = await userbot_manager.logout()
    if success:
        await call.answer("Akkaunt muvaffaqiyatli uzildi", show_alert=True)
    else:
        await call.answer("Akkaunt uzildi (xatolar bilan)", show_alert=True)
    await _account_view(call)



# --------------------------------------------------------------------- #
# 🤖 Yoqish / o'chirish
# --------------------------------------------------------------------- #
@router.callback_query(F.data == CB.TOGGLE_AI)
async def cb_toggle_ai(call: CallbackQuery) -> None:
    active = await toggle_ai()
    await _show_menu(call)
    await call.answer("AI yoqildi ✅" if active else "AI o'chirildi ⛔️")
    logger.info("Admin AI holatini o'zgartirdi: %s", active)


@router.callback_query(F.data == CB.TOGGLE_CARD_REGEX)
async def cb_toggle_card_regex(call: CallbackQuery) -> None:
    """O'chirilganda karta haqidagi barcha xabarlar AI ga uzatiladi."""
    active = await toggle_card_regex()
    await _show_menu(call)
    await call.answer(
        "Karta avto-javobi yoqildi ✅"
        if active
        else "O'chirildi — endi karta xabarlari AI ga boradi ⛔️"
    )
    logger.info("Admin karta regex holatini o'zgartirdi: %s", active)


# --------------------------------------------------------------------- #
# ⏸ Avto-pauza
# --------------------------------------------------------------------- #
async def _pause_view(call: CallbackQuery) -> None:
    active = await is_auto_pause_active()
    minutes = await get_auto_pause_minutes()
    status = presence_tracker.describe(minutes * 60)
    state = "yoqilgan ✅" if active else "o'chiq ⛔️"
    text = (
        "<b>⏸ Avto-pauza</b>\n\n"
        "Siz Telegramda o'zingiz xabar yozayotgan bo'lsangiz, AI aralashmaydi — "
        "mijoz bir vaqtda ikkita javob olmaydi.\n\n"
        f"Holat: <b>{state}</b>\n"
        f"Kutish oralig'i: <b>{minutes} daqiqa</b>\n"
        f"Hozir: <i>{status}</i>\n\n"
        "Oxirgi xabaringizdan keyin shu oraliq o'tsa, AI o'zi qayta yoqiladi.\n"
        "<i>Faollik istalgan qurilmadan hisobga olinadi. Botning o'z javoblari "
        "faollik deb sanalmaydi.</i>\n\n"
        "⚠️ Pauza paytidagi xabarlar javobsiz qoladi, lekin arxivga tushadi."
    )
    await call.message.edit_text(text, reply_markup=pause_menu(active, minutes, PAUSE_CHOICES))


@router.callback_query(F.data == CB.PAUSE)
async def cb_pause(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _pause_view(call)
    await call.answer()


@router.callback_query(F.data == CB.PAUSE_TOGGLE)
async def cb_pause_toggle(call: CallbackQuery) -> None:
    active = await toggle_auto_pause()
    await _pause_view(call)
    await call.answer(
        "Avto-pauza yoqildi ✅" if active else "Avto-pauza o'chirildi ⛔️"
    )
    logger.info("Admin avto-pauzani o'zgartirdi: %s", active)


@router.callback_query(F.data.startswith(CB.PAUSE_MINUTES))
async def cb_pause_minutes(call: CallbackQuery) -> None:
    minutes = int(call.data.removeprefix(CB.PAUSE_MINUTES))
    await set_auto_pause_minutes(minutes)
    await _pause_view(call)
    await call.answer(f"Oraliq: {minutes} daqiqa")


# --------------------------------------------------------------------- #
# 👥 Ro'yxatlar
# --------------------------------------------------------------------- #
async def _lists_view(target: CallbackQuery) -> None:
    mode = await get_access_mode()
    white, black = await get_list(LIST_WHITE), await get_list(LIST_BLACK)
    text = (
        "<b>👥 Kimga javob berish</b>\n\n"
        f"Joriy rejim: <b>{MODE_LABELS[mode]}</b>\n\n"
        "• <b>Hammaga</b> — ro'yxatlar e'tiborga olinmaydi\n"
        "• <b>Faqat oq ro'yxat</b> — boshqalarga umuman javob bermaydi\n"
        "• <b>Qora ro'yxatdan tashqari</b> — tanishlaringiz bilan yozishmaga "
        "bot aralashmaydi\n\n"
        f"⚪️ Oq ro'yxat: <b>{len(white)}</b> ta · ⚫️ Qora ro'yxat: <b>{len(black)}</b> ta\n\n"
        "<i>Javob berilmagan xabarlar ham arxivga tushadi — hech narsa yo'qolmaydi.</i>"
    )
    await target.message.edit_text(text, reply_markup=lists_menu(mode, len(white), len(black)))


@router.callback_query(F.data == CB.LISTS)
async def cb_lists(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _lists_view(call)
    await call.answer()


@router.callback_query(F.data.startswith(CB.MODE_PREFIX))
async def cb_set_mode(call: CallbackQuery) -> None:
    mode = call.data.removeprefix(CB.MODE_PREFIX)
    try:
        await set_access_mode(mode)
    except ValueError:
        await call.answer("Noma'lum rejim", show_alert=True)
        return
    await _lists_view(call)
    await call.answer(f"Rejim: {MODE_LABELS[mode]}")
    logger.info("Admin javob rejimini o'zgartirdi: %s", mode)


def _format_entries(entries: list[dict[str, object]]) -> str:
    if not entries:
        return "<i>Ro'yxat bo'sh.</i>"
    lines = []
    for item in entries:
        handle = f"@{_esc(item['username'])}" if item["username"] else _esc(item["full_name"])
        note = f" — {_esc(item['note'])}" if item["note"] else ""
        lines.append(f"• {handle} · <code>{item['user_id']}</code>{note}")
    return "\n".join(lines)


@router.callback_query(F.data.startswith(CB.LIST_SHOW))
async def cb_list_show(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    list_type = call.data.removeprefix(CB.LIST_SHOW)
    entries = await get_list(list_type)
    await call.message.edit_text(
        f"<b>{LIST_LABELS[list_type]}</b> — {len(entries)} ta\n\n{_format_entries(entries)}",
        reply_markup=list_menu(list_type),
    )
    await call.answer()


@router.callback_query(F.data.startswith(CB.LIST_ADD))
async def cb_list_add(call: CallbackQuery, state: FSMContext) -> None:
    list_type = call.data.removeprefix(CB.LIST_ADD)
    await state.set_state(Settings.list_add)
    await state.update_data(list_type=list_type)
    await call.message.edit_text(
        f"<b>{LIST_LABELS[list_type]} — qo'shish</b>\n\n"
        "ID yoki @username yuboring. Bir nechtasini qatorma-qator yozsangiz ham bo'ladi.\n"
        "Izoh qo'shish uchun: <code>123456789 aka</code>\n\n"
        "<i>@username faqat oldin sizga yozgan odamlar uchun topiladi — "
        "aks holda raqamli ID kerak.</i>",
        reply_markup=cancel_menu(),
    )
    await call.answer()


@router.message(StateFilter(Settings.list_add), F.text)
async def do_list_add(message: Message, state: FSMContext) -> None:
    list_type = (await state.get_data())["list_type"]
    added, failed = [], []

    for line in message.text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        target, note = parts[0], (parts[1] if len(parts) > 1 else None)

        if target.lstrip("-").isdigit():
            await add_to_list(list_type, int(target), note=note)
            added.append(target)
            continue

        contact = await find_contact(target)
        if contact is None:
            failed.append(target)
            continue
        await add_to_list(
            list_type,
            int(contact["user_id"]),
            username=contact["username"],
            full_name=contact["full_name"],
            note=note,
        )
        added.append(f"@{contact['username'] or contact['user_id']}")

    await state.clear()
    lines = [f"✅ Qo'shildi: <b>{len(added)}</b> ta"]
    if added:
        lines.append(", ".join(_esc(a) for a in added))
    if failed:
        lines.append(
            f"\n⚠️ Topilmadi: {', '.join(_esc(f) for f in failed)}\n"
            "<i>Bu odamlar hali sizga yozmagan — raqamli ID kiriting.</i>"
        )
    await message.answer("\n".join(lines), reply_markup=back_menu())


@router.callback_query(F.data.startswith(CB.LIST_DEL))
async def cb_list_del(call: CallbackQuery, state: FSMContext) -> None:
    list_type = call.data.removeprefix(CB.LIST_DEL)
    entries = await get_list(list_type)
    if not entries:
        await call.answer("Ro'yxat allaqachon bo'sh", show_alert=True)
        return
    await state.set_state(Settings.list_del)
    await state.update_data(list_type=list_type)
    await call.message.edit_text(
        f"<b>{LIST_LABELS[list_type]} — o'chirish</b>\n\n"
        f"{_format_entries(entries)}\n\n"
        "O'chiriladigan ID ni yuboring (bir nechtasi — qatorma-qator).",
        reply_markup=cancel_menu(),
    )
    await call.answer()


@router.message(StateFilter(Settings.list_del), F.text)
async def do_list_del(message: Message, state: FSMContext) -> None:
    list_type = (await state.get_data())["list_type"]
    removed = 0
    for line in message.text.split():
        target = line.strip()
        if target.lstrip("-").isdigit():
            if await remove_from_list(list_type, int(target)):
                removed += 1
        else:
            contact = await find_contact(target)
            if contact and await remove_from_list(list_type, int(contact["user_id"])):
                removed += 1
    await state.clear()
    await message.answer(f"🗑 O'chirildi: <b>{removed}</b> ta", reply_markup=back_menu())


# --------------------------------------------------------------------- #
# 📝 System prompt
# --------------------------------------------------------------------- #
@router.callback_query(F.data == CB.EDIT_PROMPT)
async def cb_edit_prompt(call: CallbackQuery, state: FSMContext) -> None:
    current = await get_setting("system_prompt")
    await state.set_state(Settings.prompt)
    await call.message.edit_text(
        "<b>📝 Joriy system prompt:</b>\n\n"
        f"<code>{_esc(_clip(current))}</code>\n\n"
        "Yangi promptni yuboring:",
        reply_markup=cancel_menu(),
    )
    await call.answer()


@router.message(StateFilter(Settings.prompt), F.text)
async def set_prompt(message: Message, state: FSMContext) -> None:
    await set_setting("system_prompt", message.text.strip())
    gemini_service.reset_history()  # yangi ko'rsatma toza kontekst bilan boshlansin
    await state.clear()
    await message.answer(
        "✅ System prompt yangilandi. Barcha suhbat konteksti tozalandi.",
        reply_markup=back_menu(),
    )


# --------------------------------------------------------------------- #
# 💳 Karta raqami
# --------------------------------------------------------------------- #
@router.callback_query(F.data == CB.EDIT_CARD)
async def cb_edit_card(call: CallbackQuery, state: FSMContext) -> None:
    current = await get_setting("card_details")
    await state.set_state(Settings.card)
    await call.message.edit_text(
        "<b>💳 Joriy karta ma'lumotlari:</b>\n\n"
        f"<code>{_esc(_clip(current))}</code>\n\n"
        "Yangi matnni yuboring (karta raqami so'ralganda aynan shu yuboriladi):",
        reply_markup=cancel_menu(),
    )
    await call.answer()


@router.message(StateFilter(Settings.card), F.text)
async def set_card(message: Message, state: FSMContext) -> None:
    await set_setting("card_details", message.text.strip())
    await state.clear()
    await message.answer("✅ Karta ma'lumotlari yangilandi.", reply_markup=back_menu())


@router.message(
    StateFilter(
        Settings.prompt,
        Settings.card,
        Settings.list_add,
        Settings.list_del,
        AuthStates.phone,
        AuthStates.code,
        AuthStates.password,
    )
)
async def wrong_input(message: Message) -> None:
    await message.answer("⚠️ Faqat matn yuboring.", reply_markup=cancel_menu())


# --------------------------------------------------------------------- #
# 📊 Statistika
# --------------------------------------------------------------------- #
def _period_name(days: int) -> str:
    return {1: "Bugun", 7: "So'nggi 7 kun", 30: "So'nggi 30 kun"}.get(days, f"{days} kun")


async def _stats_text(days: int) -> str:
    r = await get_period_report(days)
    answered = r["ai"] + r["regex"]
    # Statistika va arxiv sanoqlari biroz farq qilishi mumkin (masalan
    # bot qayta ishga tushganda) — foizni 100 dan oshirmaymiz.
    rate = f"{min(answered / r['incoming'] * 100, 100):.0f}%" if r["incoming"] else "—"
    tokens = r["prompt_t"] + r["output_t"] + r["think_t"]
    avg = f"{tokens / r['requests']:.0f}" if r["requests"] else "0"

    lines = [
        f"<b>📊 {_period_name(days)}</b>  <i>({r['since']} dan)</i>\n",
        "<b>Xabarlar</b>",
        f"• Kelgan: <b>{r['incoming']}</b> ta · {r['unique_users']} kishidan",
        f"• 🆕 Yangi mijoz: <b>{r['new_contacts']}</b>",
        f"• Javob berilgan: <b>{answered}</b> ({rate})",
        f"    🤖 AI: {r['ai']} · ⚡️ regex: {r['regex']}",
    ]
    if r["skipped"]:
        lines.append(f"• 🚫 O'tkazib yuborilgan: {r['skipped']}")
    if r["paused"]:
        lines.append(f"• ⏸ Avto-pauza tufayli: {r['paused']}")
    if r["error"]:
        lines.append(f"• ⚠️ Xato: {r['error']}")

    lines += [
        "\n<b>Token sarfi</b>",
        f"• So'rovlar: <b>{r['requests']}</b> · jami <b>{tokens:,}</b> token".replace(",", " "),
        f"• Kirish: {r['prompt_t']} · chiqish: {r['output_t']} · fikrlash: {r['think_t']}",
        f"• O'rtacha: {avg} token/so'rov",
    ]

    arch = archive_service.stats()
    if arch["enabled"]:
        extra = f" · navbatda {arch['queued']}" if arch["queued"] else ""
        dropped = f" · ⚠️ tashlangan {arch['dropped']}" if arch["dropped"] else ""
        lines.append(f"\n📁 Arxiv kanali: {arch['sent']} yozuv{extra}{dropped}")

    top = await get_top_contacts(days)
    if top:
        lines.append("\n<b>Eng faol suhbatdoshlar</b>")
        for name, uid, count in top:
            lines.append(f"• {_esc(name)} · <code>{uid}</code> — {count} xabar")

    return "\n".join(lines)


@router.callback_query(F.data == CB.STATS)
async def cb_stats(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await call.message.edit_text(await _stats_text(1), reply_markup=stats_menu(1))
    await call.answer()


@router.callback_query(F.data.startswith(CB.STATS_PERIOD))
async def cb_stats_period(call: CallbackQuery) -> None:
    days = int(call.data.removeprefix(CB.STATS_PERIOD))
    await call.message.edit_text(await _stats_text(days), reply_markup=stats_menu(days))
    await call.answer()


@router.callback_query(F.data == CB.STATS_KEYS)
async def cb_stats_keys(call: CallbackQuery) -> None:
    report = await get_period_report(30)
    usage = {item["tail"]: item for item in report["per_key"]}

    lines = ["<b>🔑 API kalitlar</b>\n", "<i>Holat hozirgi, sarf — 30 kunlik.</i>\n"]
    for item in gemini_service.key_status():
        stat = usage.get(item["tail"], {})
        if item["active"]:
            head = "🟢"
            tail_info = ""
        else:
            minutes = max(1, item["cooldown"] // 60)
            head = "🔴"
            tail_info = f"\n    ⏳ {minutes} daq. — {item['reason']}"
        lines.append(
            f"{head} Kalit #{item['index']} (…{item['tail']})\n"
            f"    {stat.get('requests', 0)} so'rov · {stat.get('tokens', 0)} token"
            f"{tail_info}"
        )
    await call.message.edit_text("\n".join(lines), reply_markup=stats_menu(0))
    await call.answer()


@router.callback_query(F.data == CB.STATS_TOP)
async def cb_stats_top(call: CallbackQuery) -> None:
    questions = await get_top_questions(days=30)
    if questions:
        body = "\n".join(f"<b>{n}×</b> {_esc(q)}" for q, n in questions)
    else:
        body = "<i>Hali takrorlangan savol yo'q.</i>"
    await call.message.edit_text(
        f"<b>🔥 Eng ko'p takrorlangan savollar</b>  <i>(30 kun)</i>\n\n{body}\n\n"
        "<i>Bu savollarga tayyor javoblarni promptga qo'shsangiz, "
        "AI aniqroq va tejamkorroq ishlaydi.</i>",
        reply_markup=stats_menu(0),
    )
    await call.answer()


# --------------------------------------------------------------------- #
# 🧹 Kontekst
# --------------------------------------------------------------------- #
@router.callback_query(F.data == CB.RESET_CONTEXT)
async def cb_reset_context(call: CallbackQuery) -> None:
    gemini_service.reset_history()
    await call.answer("Barcha suhbat konteksti tozalandi 🧹", show_alert=True)


# --------------------------------------------------------------------- #
# Boshqa hamma narsa
# --------------------------------------------------------------------- #
@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Menyuni ochish uchun /menu buyrug'ini yuboring.")
