import asyncio
import logging
import re
import sqlite3
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8790743030:AAHHeA850Jnr0CqiJqnouSsJKzQRxTlVv5s"
ADMIN_IDS = {8959972690, 8037758285}
PRICE_STARS = 7
DB_PATH = "bot.db"

# Опционально: токен Funstat API для расширенного поиска по username/ID.
FUNSTAT_TOKEN: Optional[str] = None  # например: "your_funstat_token"

# ========== БАЗА ДАННЫХ ==========
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                requests_left INTEGER DEFAULT 0
            )
        """)
        conn.commit()

async def get_requests(user_id: int) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT requests_left FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        return row[0] if row else 0

async def add_requests(user_id: int, amount: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO users (user_id, requests_left) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET requests_left = requests_left + ?
        """, (user_id, amount, amount))
        conn.commit()

async def spend_request(user_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT requests_left FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        if not row or row[0] < 1:
            return False
        conn.execute("UPDATE users SET requests_left = requests_left - 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        return True

async def list_users():
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("SELECT user_id, requests_left FROM users ORDER BY user_id")
        return cur.fetchall()

# ========== ОСИНТ-ПОИСК ==========
async def search_phone(phone: str) -> str:
    import phonenumbers
    try:
        parsed = phonenumbers.parse(phone, None)
        if not phonenumbers.is_valid_number(parsed):
            return "❌ Номер невалиден."
        carrier = phonenumbers.carrier.name_for_number(parsed, "ru")
        region = phonenumbers.region_code_for_number(parsed)
        return (
            f"📞 <b>Номер:</b> {phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)}\n"
            f"🌍 <b>Регион:</b> {region}\n"
            f"📶 <b>Оператор:</b> {carrier or 'не определён'}"
        )
    except Exception as e:
        return f"❌ Ошибка обработки номера: {e}"

async def search_full_name(name: str) -> str:
    return (
        "🔍 Поиск по ФИО в открытых источниках требует подключения внешних API "
        "(Google Custom Search, SerpAPI и т.п.). Функция находится в разработке."
    )

async def _get_funstat_client():
    if not FUNSTAT_TOKEN:
        return None
    try:
        from funstat_api import AsyncFunstatClient
        return AsyncFunstatClient(FUNSTAT_TOKEN)
    except ImportError:
        return None

async def _format_funstat_user_data(target: str) -> Optional[str]:
    client = await _get_funstat_client()
    if not client:
        return None
    try:
        async with client:
            names = await client.get_names(target)
            usernames = await client.get_usernames(target)
            chats = await client.get_chats(target)
            common_groups = await client.common_groups(target)

        lines = [f"👤 <b>Расширенный профиль:</b> <code>{target}</code>"]

        if names and names.data:
            lines.append("\n📛 <b>Прошлые имена:</b>")
            for item in names.data[:5]:
                lines.append(f"• {item.name} (≈ {item.date})")
        else:
            lines.append("\n📛 <b>Прошлые имена:</b> нет данных.")

        if usernames and usernames.data:
            lines.append("\n🔗 <b>Прошлые username:</b>")
            for item in usernames.data[:5]:
                lines.append(f"• @{item.username} (≈ {item.date})")
        else:
            lines.append("\n🔗 <b>Прошлые username:</b> нет данных.")

        if chats and chats.data:
            lines.append("\n💬 <b>Чаты (последние):</b>")
            for chat in chats.data[:10]:
                title = getattr(chat, "title", None) or getattr(chat, "username", "—")
                lines.append(f"• {title}")
        else:
            lines.append("\n💬 <b>Чаты:</b> нет данных.")

        if common_groups and common_groups.data:
            lines.append("\n👥 <b>Общие группы:</b>")
            for grp in common_groups.data[:10]:
                lines.append(f"• {grp.title}")
        else:
            lines.append("\n👥 <b>Общие группы:</b> нет данных.")

        return "\n".join(lines)
    except Exception as e:
        logging.warning(f"Funstat API error for {target}: {e}")
        return None

async def search_username(username: str) -> str:
    extended = await _format_funstat_user_data(f"@{username}")
    if extended:
        return extended

    import maigret
    try:
        results = maigret.search(username, top=10, timeout=10)
        lines = [f"🔎 <b>Поиск по @{username}</b>", f"Найдено совпадений: {len(results)}"]
        for site, data in list(results.items())[:10]:
            url = data.get("url_user")
            if url:
                lines.append(f"• {site}: {url}")
        return "\n".join(lines) if len(lines) > 2 else "Совпадений в открытых источниках не найдено."
    except Exception as e:
        return f"❌ Ошибка поиска по юзернейму: {e}"

async def search_by_id(user_id: str) -> str:
    extended = await _format_funstat_user_data(user_id)
    if extended:
        return extended
    return (
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
        "Прямой поиск по ID без подключённого API (например, Funstat) невозможен. "
        "Задайте <code>FUNSTAT_TOKEN</code>, чтобы получать историю имён и чаты."
    )

async def perform_osint_search(query: str) -> str:
    query = query.strip()
    if query.startswith("@"):
        return await search_username(query.lstrip("@"))
    if re.fullmatch(r"\+?\d[\d\s\-()]{6,}", query):
        return await search_phone(query)
    if re.fullmatch(r"\d{5,}", query):
        return await search_by_id(query)
    if re.fullmatch(r"[А-Яа-яЁёA-Za-z\s\-]{3,}", query):
        return await search_full_name(query)
    return "❓ Не удалось определить тип запроса. Отправьте @username, номер телефона или ФИО."

# ========== ХЕНДЛЕРЫ ==========
router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message):
    text = (
        "👋 <b>Добро пожаловать в OSINT-бота!</b>\n\n"
        "🔍 <b>Я умею искать по:</b>\n"
        "• <b>@username</b> — поиск по юзернейму (maigret + расширенные данные)\n"
        "• <b>Номер телефона</b> — валидация, регион, оператор\n"
        "• <b>ФИО</b> — поиск по открытым источникам (в разработке)\n"
        "• <b>ID</b> — расширенный профиль (при наличии API)\n\n"
        "💬 <b>По юзернейму или ID также можно получить:</b>\n"
        "• список контактов\n"
        "• прошлые имена и username\n"
        "• чаты и общие группы\n\n"
        f"⭐ <b>Стоимость одного запроса — {PRICE_STARS} звёзд.</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💫 Купить запрос — {PRICE_STARS} зв.", callback_data="buy_request")],
    ])
    await message.answer(text, reply_markup=kb)

@router.callback_query(F.data == "buy_request")
async def buy_request(callback: CallbackQuery):
    await callback.message.answer_invoice(
        title="OSINT-поиск",
        description="Один запрос к открытым источникам",
        payload="osint_search",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Поиск", amount=PRICE_STARS)],
    )
    await callback.answer()

@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@router.message(F.successful_payment)
async def on_successful_payment(message: Message):
    await add_requests(message.from_user.id, 1)
    await message.answer("✅ Оплата получена. Вам добавлен 1 поисковый запрос.")

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Список пользователей", callback_data="admin_list_users")],
        [InlineKeyboardButton(text="➕ Выдать запросы", callback_data="admin_give_requests")],
    ])
    await message.answer("🛠 <b>Админ-панель:</b>", reply_markup=kb)

@router.callback_query(F.data == "admin_list_users")
async def admin_list_users(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    users = await list_users()
    if not users:
        text = "Пользователей пока нет."
    else:
        lines = ["<b>ID</b> — <b>Запросы</b>"]
        for uid, req in users:
            lines.append(f"{uid} — {req}")
        text = "\n".join(lines)
    await callback.message.answer(text)
    await callback.answer()

@router.callback_query(F.data == "admin_give_requests")
async def admin_give_requests_prompt(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        return
    await callback.message.answer(
        "Отправьте сообщение в формате:\n"
        "<code>ID количество</code>\n"
        "Например: <code>123456789 5</code>",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()

@router.message(F.text.regexp(r"^\d+\s+\d+$"))
async def admin_give_requests(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    target_id = int(parts[0])
    amount = int(parts[1])
    await add_requests(target_id, amount)
    await message.answer(f"✅ Пользователю {target_id} выдано {amount} запросов.")

@router.message(F.text)
async def handle_query(message: Message):
    user_id = message.from_user.id
    query = message.text.strip()

    if query.isdigit() and int(query) in ADMIN_IDS:
        await message.answer("❌ Поиск администраторов запрещён.")
        return

    if await get_requests(user_id) < 1:
        await message.answer(
            f"❌ У вас нет доступных запросов.\n"
            f"Оплатите {PRICE_STARS} ⭐ командой /buy или через меню.",
        )
        return

    if not await spend_request(user_id):
        await message.answer("❌ Не удалось списать запрос. Попробуйте позже.")
        return

    await message.answer("🔎 Выполняю поиск...")
    result = await perform_osint_search(query)
    await message.answer(result)

# ========== ЗАПУСК ==========
async def main():
    init_db()
    logging.basicConfig(level=logging.INFO)
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())