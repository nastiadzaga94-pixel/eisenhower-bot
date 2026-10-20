import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Налаштування логування
logging.basicConfig(level=logging.INFO)

# Безпечне отримання токена з налаштувань Render (Environment)
TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()


# --- РОБОТА З БАЗОЮ ДАНИХ ---
def init_db():
    conn = sqlite3.connect("eisenhower.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            quadrant TEXT,
            task_text TEXT,
            deadline_date TEXT,
            remind_time TEXT,
            frequency TEXT
        )
    """
    )
    conn.commit()
    conn.close()


init_db()


# --- СТАНИ FSM ---
class TaskStates(StatesGroup):
    waiting_for_task_text = State()
    waiting_for_quadrant = State()
    waiting_for_date = State()
    waiting_for_time = State()
    waiting_for_frequency = State()


# --- КЛАВІАТУРИ ---
# Головне меню тепер знизу екрана (ReplyKeyboardMarkup) з фіксацією resize_keyboard=True
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Додати завдання")],
            [KeyboardButton(text="📋 Переглянути матрицю")],
            [KeyboardButton(text="🗑 Очистити всі завдання")],
        ],
        resize_keyboard=True,
    )


def get_quadrants_keyboard():
    keyboard = [
        [
            InlineKeyboardButton(
                text="🔥 Важливо & Терміново", callback_data="q_1"
            )
        ],
        [
            InlineKeyboardButton(
                text="📅 Важливо & Нетерміново", callback_data="q_2"
            )
        ],
        [
            InlineKeyboardButton(
                text="⚡ Неважливо & Терміново", callback_data="q_3"
            )
        ],
        [
            InlineKeyboardButton(
                text="🗑 Неважливо & Нетерміново", callback_data="q_4"
            )
        ],
        [InlineKeyboardButton(text="« Назад у меню", callback_data="back_to_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_frequency_keyboard():
    keyboard = [
        [InlineKeyboardButton(text="1️⃣ Один раз", callback_data="freq_once")],
        [
            InlineKeyboardButton(
                text="⏱ Кожні 15 хвилин", callback_data="freq_15m"
            )
        ],
        [InlineKeyboardButton(text="⏰ Щогодини", callback_data="freq_1h")],
        [InlineKeyboardButton(text="⏳ Кожні 3 години", callback_data="freq_3h")],
        [InlineKeyboardButton(text="📆 Щодня", callback_data="freq_daily")],
        [
            InlineKeyboardButton(
                text="📅 Раз на тиждень", callback_data="freq_weekly"
            )
        ],
        [InlineKeyboardButton(text="« Назад", callback_data="back_to_quadrant")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_back_keyboard(callback_back):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data=callback_back)]
        ]
    )


# --- ОБРОБНИКИ КОМАНД ТА МЕНЮ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Привіт! Я твій бот для нагадувань за **Матрицею Ейзенхауера**.\n\n"
        "Обери дію в меню нижче:",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown",
    )


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.message.answer("Головне меню:", reply_markup=get_main_keyboard())
    await callback.answer()


# Обробка текстових кнопок нижнього меню
@dp.message(F.text == "➕ Додати завдання")
async def text_add_task(message: types.Message, state: FSMContext):
    await message.answer(
        "Введи текст свого завдання:",
        reply_markup=get_back_keyboard("back_to_menu"),
    )
    await state.set_state(TaskStates.waiting_for_task_text)


@dp.message(F.text == "📋 Переглянути матрицю")
async def text_show_matrix(message: types.Message):
    # Викликаємо логіку матриці
    await show_matrix_handler(message)


@dp.message(F.text == "🗑 Очистити всі завдання")
async def text_clear_tasks(message: types.Message):
    user_id = message.from_user.id
    conn = sqlite3.connect("eisenhower.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM tasks WHERE user_id = ?", (user_id,))
    user_tasks = cursor.fetchall()

    for task in user_tasks:
        job_id = f"task_{task[0]}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

    cursor.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    await message.answer(
        "🗑 Усі завдання та нагадування скасовано!",
        reply_markup=get_main_keyboard(),
    )


# --- КРОК 1: ТЕКСТ ЗАВДАННЯ ---
@dp.callback_query(F.data == "add_task")
async def add_task_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Введи текст свого завдання:",
        reply_markup=get_back_keyboard("back_to_menu"),
    )
    await state.set_state(TaskStates.waiting_for_task_text)
    await callback.answer()


@dp.message(TaskStates.waiting_for_task_text)
async def process_task_text(message: types.Message, state: FSMContext):
    await state.update_data(task_text=message.text)
    await message.answer(
        "Обери квадрант для цього завдання:",
        reply_markup=get_quadrants_keyboard(),
    )
    await state.set_state(TaskStates.waiting_for_quadrant)


# --- КРОК 2: КВАДРАНТ ---
@dp.callback_query(TaskStates.waiting_for_quadrant, F.data.startswith("q_"))
async def process_quadrant(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(quadrant=callback.data)
    await callback.message.edit_text(
        "Введи дату дедлайну у форматі **ДД.ММ.РРРР** (наприклад, `25.10.2026`):",
        reply_markup=get_back_keyboard("back_to_text_prompt"),
        parse_mode="Markdown",
    )
    await state.set_state(TaskStates.waiting_for_date)
    await callback.answer()


@dp.callback_query(F.data == "back_to_text_prompt")
async def back_to_text_prompt(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Введи текст свого завдання:",
        reply_markup=get_back_keyboard("back_to_menu"),
    )
    await state.set_state(TaskStates.waiting_for_task_text)
    await callback.answer()


# --- КРОК 3: ДАТА ДЕДЛАЙНУ ---
@dp.message(TaskStates.waiting_for_date)
async def process_date(message: types.Message, state: FSMContext):
    date_str = message.text.strip()
    try:
        datetime.strptime(date_str, "%d.%m.%Y")
        await state.update_data(deadline_date=date_str)
        await message.answer(
            "Введи час першого нагадування у форматі **ГГ:ХХ** (наприклад, `09:30` або `18:00`):",
            parse_mode="Markdown",
        )
        await state.set_state(TaskStates.waiting_for_time)
    except ValueError:
        await message.answer(
            "❌ Невірний формат дати. Введи дату у форматі **ДД.ММ.РРРР** (наприклад, `25.10.2026`):",
            parse_mode="Markdown",
        )


# --- КРОК 4: ЧАС НАГАДУВАННЯ ---
@dp.message(TaskStates.waiting_for_time)
async def process_time(message: types.Message, state: FSMContext):
    time_str = message.text.strip()
    try:
        datetime.strptime(time_str, "%H:%M")
        await state.update_data(remind_time=time_str)
        await message.answer(
            "Як часто нагадувати про це завдання?",
            reply_markup=get_frequency_keyboard(),
        )
        await state.set_state(TaskStates.waiting_for_frequency)
    except ValueError:
        await message.answer(
            "❌ Невірний формат часу. Введи час у форматі **ГГ:ХХ** (наприклад, `14:30`):",
            parse_mode="Markdown",
        )


@dp.callback_query(F.data == "back_to_quadrant")
async def back_to_quadrant(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Обери квадрант для цього завдання:",
        reply_markup=get_quadrants_keyboard(),
    )
    await state.set_state(TaskStates.waiting_for_quadrant)
    await callback.answer()


# --- ФУНКЦІЯ ПЛАНУВАННЯ ТА ЗБЕРЕЖЕННЯ ---
def schedule_task_reminder(
    task_id, user_id, task_text, deadline_date, time_str, frequency
):
    d_day, d_month, d_year = map(int, deadline_date.split("."))
    hours, minutes = map(int, time_str.split(":"))

    first_run = datetime(d_year, d_month, d_day, hours, minutes, 0)
    job_id = f"task_{task_id}"

    if frequency == "freq_once":
        if first_run < datetime.now():
            first_run = datetime.now() + timedelta(seconds=5)
        scheduler.add_job(
            send_reminder,
            "date",
            run_date=first_run,
            args=[user_id, task_text, task_id],
            id=job_id,
        )
    elif frequency == "freq_15m":
        scheduler.add_job(
            send_reminder,
            "interval",
            minutes=15,
            start_date=first_run,
            args=[user_id, task_text, task_id],
            id=job_id,
        )
    elif frequency == "freq_1h":
        scheduler.add_job(
            send_reminder,
            "interval",
            hours=1,
            start_date=first_run,
            args=[user_id, task_text, task_id],
            id=job_id,
        )
    elif frequency == "freq_3h":
        scheduler.add_job(
            send_reminder,
            "interval",
            hours=3,
            start_date=first_run,
            args=[user_id, task_text, task_id],
            id=job_id,
        )
    elif frequency == "freq_daily":
        scheduler.add_job(
            send_reminder,
            "cron",
            hour=hours,
            minute=minutes,
            args=[user_id, task_text, task_id],
            id=job_id,
        )
    elif frequency == "freq_weekly":
        scheduler.add_job(
            send_reminder,
            "interval",
            weeks=1,
            start_date=first_run,
            args=[user_id, task_text, task_id],
            id=job_id,
        )


async def send_reminder(user_id, task_text, task_id):
    try:
        await bot.send_message(
            chat_id=user_id,
            text=f"🔔 **НАГАДУВАННЯ!**\n\nНе забудь виконати завдання:\n👉 **{task_text}**",
            parse_mode="Markdown",
        )
    except Exception as e:
        logging.error(f"Помилка відправки нагадування: {e}")


# --- НОВА ФУНКЦІЯ: НАГАДУВАННЯ КОЖНІ 3 ДНІ (ПЛАНУВАЛЬНИК) ---
async def send_three_day_reminder():
    conn = sqlite3.connect("eisenhower.db")
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT user_id FROM tasks")
    users = cursor.fetchall()
    conn.close()

    for user in users:
        user_id = user[0]
        try:
            await bot.send_message(
                chat_id=user_id,
                text="🔔 **Нагадування (кожні 3 дні):** Час перевірити та актуалізуйте свою Матрицю Ейзенхауера!",
                reply_markup=get_main_keyboard(),
                parse_mode="Markdown",
            )
        except Exception as e:
            logging.error(
                f"Не вдалося надіслати загальне нагадування користувачу {user_id}: {e}"
            )


# --- КРОК 5: ЧАСТОТА ТА ЗБЕРЕЖЕННЯ ---
@dp.callback_query(TaskStates.waiting_for_frequency, F.data.startswith("freq_"))
async def process_frequency(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = callback.from_user.id
    task_text = data.get("task_text")
    quadrant = data.get("quadrant")
    deadline_date = data.get("deadline_date")
    time_str = data.get("remind_time")
    freq = callback.data

    conn = sqlite3.connect("eisenhower.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO tasks (user_id, quadrant, task_text, deadline_date, remind_time, frequency) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, quadrant, task_text, deadline_date, time_str, freq),
    )
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()

    try:
        schedule_task_reminder(
            task_id, user_id, task_text, deadline_date, time_str, freq
        )
    except Exception as e:
        logging.error(f"Помилка планувальника: {e}")

    freq_names = {
        "freq_once": "Один раз",
        "freq_15m": "Кожні 15 хвилин",
        "freq_1h": "Щогодини",
        "freq_3h": "Кожні 3 години",
        "freq_daily": "Щодня",
        "freq_weekly": "Раз на тиждень",
    }

    await state.clear()
    await callback.message.edit_text(
        f"✅ **Завдання збережено!**\n\n"
        f"📌 Завдання: {task_text}\n"
        f"📅 Дедлайн: {deadline_date}\n"
        f"⏰ Час нагадування: {time_str}\n"
        f"🔄 Частота: {freq_names.get(freq, freq)}",
        parse_mode="Markdown",
    )
    await callback.message.answer(
        "Головне меню:", reply_markup=get_main_keyboard()
    )
    await callback.answer()


# --- ВІДОБРАЖЕННЯ ТА ВИДАЛЕННЯ ---
async def show_matrix_handler(message_or_callback):
    user_id = message_or_callback.from_user.id
    conn = sqlite3.connect("eisenhower.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, quadrant, task_text, deadline_date, remind_time FROM tasks WHERE user_id = ?",
        (user_id,),
    )
    tasks = cursor.fetchall()
    conn.close()

    q_text = {
        "q_1": "🔥 **Важливо & Терміново:**",
        "q_2": "📅 **Важливо & Нетерміново:**",
        "q_3": "⚡ **Неважливо & Терміново:**",
        "q_4": "🗑 **Неважливо & Нетерміново:**",
    }

    grouped = {"q_1": [], "q_2": [], "q_3": [], "q_4": []}
    for task_id, q, text, d_date, r_time in tasks:
        if q in grouped:
            grouped[q].append(
                (task_id, f"{text} (до {d_date} ⏰ {r_time})")
            )

    response = "📊 **Твоя матриця Ейзенхауера:**\n\n"
    inline_kb = []

    for q_key, title in q_text.items():
        response += f"{title}\n"
        if grouped[q_key]:
            for t_id, t_desc in grouped[q_key]:
                response += f"  • {t_desc}\n"
                inline_kb.append(
                    [
                        InlineKeyboardButton(
                            text=f"❌ Видалити: {t_desc[:22]}...",
                            callback_data=f"del_{t_id}",
                        )
                    ]
                )
        else:
            response += "  *(порожньо)*\n"
        response += "\n"

    matrix_kb = InlineKeyboardMarkup(inline_keyboard=inline_kb)

    # Перевіряємо, чи це CallbackQuery чи звичайне Message від кнопки меню
    if isinstance(message_or_callback, types.CallbackQuery):
        await message_or_callback.message.edit_text(
            response, reply_markup=matrix_kb, parse_mode="Markdown"
        )
        await message_or_callback.answer()
    else:
        await message_or_callback.answer(
            response, reply_markup=matrix_kb, parse_mode="Markdown"
        )


@dp.callback_query(F.data == "show_matrix")
async def show_matrix_callback(callback: types.CallbackQuery):
    await show_matrix_handler(callback)


@dp.callback_query(F.data.startswith("del_"))
async def delete_single_task(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])

    job_id = f"task_{task_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    conn = sqlite3.connect("eisenhower.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()

    await callback.answer("✅ Завдання видалено!")
    await show_matrix_handler(callback)


@dp.callback_query(F.data == "clear_tasks")
async def clear_tasks(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    conn = sqlite3.connect("eisenhower.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM tasks WHERE user_id = ?", (user_id,))
    user_tasks = cursor.fetchall()

    for task in user_tasks:
        job_id = f"task_{task[0]}"
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

    cursor.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    await callback.message.edit_text(
        "🗑 Усі завдання та нагадування скасовано!",
        reply_markup=get_main_keyboard(),
    )
    await callback.answer()


# --- ЗАПУСК БОТА ---
async def main():
    # Запускаємо планувальник та додаємо регулярну задачу нагадування кожні 3 дні
    scheduler.add_job(send_three_day_reminder, "interval", days=3)
    scheduler.start()

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
