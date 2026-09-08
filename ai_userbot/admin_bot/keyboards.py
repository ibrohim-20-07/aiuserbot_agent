"""Admin bot uchun inline klaviaturalar."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..database import LIST_BLACK, LIST_WHITE, MODE_ALL, MODE_BLACKLIST, MODE_WHITELIST


class CB:
    """Callback data konstantalari."""

    MENU = "menu"
    TOGGLE_AI = "toggle_ai"
    TOGGLE_CARD_REGEX = "toggle_card_regex"
    EDIT_PROMPT = "edit_prompt"
    EDIT_CARD = "edit_card"
    RESET_CONTEXT = "reset_context"
    CANCEL = "cancel"

    # Akkaunt (Userbot)
    ACCOUNT = "account"
    ACCOUNT_CONNECT = "acc_connect"
    ACCOUNT_REFRESH = "acc_refresh"
    ACCOUNT_LOGOUT = "acc_logout"
    ACCOUNT_LOGOUT_CONFIRM = "acc_logout_confirm"

    # Avto-pauza
    PAUSE = "pause"
    PAUSE_TOGGLE = "pause_toggle"
    PAUSE_MINUTES = "pausem:"      # pausem:10

    # Ro'yxatlar
    LISTS = "lists"
    MODE_PREFIX = "mode:"          # mode:all | mode:whitelist | mode:blacklist
    LIST_SHOW = "list:"            # list:white | list:black
    LIST_ADD = "ladd:"             # ladd:white | ladd:black
    LIST_DEL = "ldel:"             # ldel:white | ldel:black

    # Statistika
    STATS = "stats"
    STATS_PERIOD = "stp:"          # stp:1 | stp:7 | stp:30
    STATS_TOP = "sttop"
    STATS_KEYS = "stkeys"


def main_menu(
    ai_active: bool,
    card_regex_active: bool,
    mode_label: str,
    pause_label: str = "o'chiq",
    account_label: str = "o'chiq 🔴",
) -> InlineKeyboardMarkup:
    state = "🟢 YONIQ" if ai_active else "🔴 O'CHIQ"
    card_state = "🟢" if card_regex_active else "🔴"
    builder = InlineKeyboardBuilder()
    builder.button(text=f"📱 Akkaunt: {account_label}", callback_data=CB.ACCOUNT)
    builder.button(text=f"🤖 AI holati: {state}", callback_data=CB.TOGGLE_AI)
    builder.button(
        text=f"{card_state} Karta avto-javobi (regex)",
        callback_data=CB.TOGGLE_CARD_REGEX,
    )
    builder.button(text=f"⏸ Avto-pauza: {pause_label}", callback_data=CB.PAUSE)
    builder.button(text=f"👥 Ro'yxatlar: {mode_label}", callback_data=CB.LISTS)
    builder.button(text="📝 Promptni tahrirlash", callback_data=CB.EDIT_PROMPT)
    builder.button(text="💳 Karta raqami", callback_data=CB.EDIT_CARD)
    builder.button(text="📊 Statistika", callback_data=CB.STATS)
    builder.button(text="🧹 Kontekstni tozalash", callback_data=CB.RESET_CONTEXT)
    builder.adjust(1, 1, 1, 1, 1, 2, 2)
    return builder.as_markup()


def pause_menu(active: bool, minutes: int, choices: tuple[int, ...]) -> InlineKeyboardMarkup:
    """Avto-pauza: yoqish/o'chirish va kutish oralig'ini tanlash."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text=("🟢 Yoqilgan — o'chirish" if active else "🔴 O'chiq — yoqish"),
        callback_data=CB.PAUSE_TOGGLE,
    )
    for value in choices:
        tick = "✅ " if value == minutes else ""
        builder.button(text=f"{tick}{value} daq", callback_data=f"{CB.PAUSE_MINUTES}{value}")
    builder.button(text="⬅️ Menyu", callback_data=CB.MENU)
    builder.adjust(1, len(choices), 1)
    return builder.as_markup()


def lists_menu(mode: str, white_count: int, black_count: int) -> InlineKeyboardMarkup:
    """Rejim tanlash va ro'yxatlarni ochish."""
    tick = lambda active: "✅ " if active else ""  # noqa: E731
    builder = InlineKeyboardBuilder()
    builder.button(text=f"{tick(mode == MODE_ALL)}Hammaga javob ber", callback_data=CB.MODE_PREFIX + MODE_ALL)
    builder.button(
        text=f"{tick(mode == MODE_WHITELIST)}Faqat oq ro'yxatga",
        callback_data=CB.MODE_PREFIX + MODE_WHITELIST,
    )
    builder.button(
        text=f"{tick(mode == MODE_BLACKLIST)}Qora ro'yxatdan tashqari",
        callback_data=CB.MODE_PREFIX + MODE_BLACKLIST,
    )
    builder.button(text=f"⚪️ Oq ro'yxat ({white_count})", callback_data=CB.LIST_SHOW + LIST_WHITE)
    builder.button(text=f"⚫️ Qora ro'yxat ({black_count})", callback_data=CB.LIST_SHOW + LIST_BLACK)
    builder.button(text="⬅️ Menyu", callback_data=CB.MENU)
    builder.adjust(1, 1, 1, 2, 1)
    return builder.as_markup()


def list_menu(list_type: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Qo'shish", callback_data=CB.LIST_ADD + list_type)
    builder.button(text="➖ O'chirish", callback_data=CB.LIST_DEL + list_type)
    builder.button(text="⬅️ Ro'yxatlar", callback_data=CB.LISTS)
    builder.adjust(2, 1)
    return builder.as_markup()


def stats_menu(active_period: int = 1) -> InlineKeyboardMarkup:
    tick = lambda n: "✅ " if n == active_period else ""  # noqa: E731
    builder = InlineKeyboardBuilder()
    builder.button(text=f"{tick(1)}Bugun", callback_data=CB.STATS_PERIOD + "1")
    builder.button(text=f"{tick(7)}7 kun", callback_data=CB.STATS_PERIOD + "7")
    builder.button(text=f"{tick(30)}30 kun", callback_data=CB.STATS_PERIOD + "30")
    builder.button(text="🔑 Kalitlar", callback_data=CB.STATS_KEYS)
    builder.button(text="🔥 Top savollar", callback_data=CB.STATS_TOP)
    builder.button(text="⬅️ Menyu", callback_data=CB.MENU)
    builder.adjust(3, 2, 1)
    return builder.as_markup()


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Menyu", callback_data=CB.MENU)]]
    )


def cancel_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✖️ Bekor qilish", callback_data=CB.CANCEL)]]
    )


def account_menu(is_authorized: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_authorized:
        builder.button(text="🔄 Qayta tekshirish", callback_data=CB.ACCOUNT_REFRESH)
        builder.button(text="🚪 Akkauntni uzish", callback_data=CB.ACCOUNT_LOGOUT)
        builder.button(text="⬅️ Menyu", callback_data=CB.MENU)
        builder.adjust(2, 1)
    else:
        builder.button(text="📲 Akkauntni ulash", callback_data=CB.ACCOUNT_CONNECT)
        builder.button(text="⬅️ Menyu", callback_data=CB.MENU)
        builder.adjust(1, 1)
    return builder.as_markup()


def confirm_logout_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Ha, akkauntni uzish", callback_data=CB.ACCOUNT_LOGOUT_CONFIRM)
    builder.button(text="✖️ Yo'q, bekor qilish", callback_data=CB.ACCOUNT)
    builder.adjust(1, 1)
    return builder.as_markup()


def cancel_auth_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✖️ Bekor qilish", callback_data=CB.ACCOUNT)]]
    )

