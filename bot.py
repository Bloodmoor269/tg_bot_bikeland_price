import re
import sqlite3
import requests
import html
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# --- Конфигурация ---
TOKEN = ""  # Замените на токен от @BotFather
CHECK_INTERVAL = 300  # 5 минут в секундах

# --- База данных ---
DB_PATH = "bikeland_prices.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            old_price TEXT,
            current_price TEXT,
            chat_id INTEGER NOT NULL,
            UNIQUE(url, chat_id)   -- уникальность в рамках одного пользователя
        )
    """)
    conn.commit()
    conn.close()


def add_link(url: str, chat_id: int) -> bool:
    """Добавляет ссылку для конкретного пользователя.
    Возвращает True, если ссылка новая именно для него."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO links (url, chat_id) VALUES (?, ?)",
            (url, chat_id),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # уже есть у этого пользователя
        return False
    finally:
        conn.close()

def get_links(chat_id: int):
    """Возвращает список всех ссылок для указанного чата."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, url, old_price, current_price FROM links WHERE chat_id = ?",
        (chat_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows

def remove_link(link_id: int, chat_id: int) -> bool:
    """Удаляет ссылку по ID. Возвращает True, если запись была удалена."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM links WHERE id = ? AND chat_id = ?",
        (link_id, chat_id),
    )
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted

def update_prices(link_id: int, old_price: str, current_price: str):
    """Обновляет цены в БД."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE links SET old_price = ?, current_price = ? WHERE id = ?",
        (old_price, current_price, link_id),
    )
    conn.commit()
    conn.close()

# --- Парсинг цен ---
def parse_prices(url: str):
    """
    Загружает страницу и извлекает старую и текущую цены.
    Использует BeautifulSoup для поиска элементов с классами prices__old и prices__cur.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")

        # Ищем все элементы с классом, содержащим "prices__old" и "prices__cur"
        old_el = soup.find(class_=re.compile(r"prices__old"))
        cur_el = soup.find(class_=re.compile(r"prices__cur"))

        old_price = old_el.get_text(strip=True) if old_el else None
        current_price = cur_el.get_text(strip=True) if cur_el else None

        return old_price, current_price
    except Exception as e:
        print(f"Ошибка парсинга {url}: {e}")
        return None, None

# --- Обработчики команд ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет приветственное сообщение."""
    await update.message.reply_text(
        "Привет! Отправь мне ссылку с bikeland.ru, и я буду следить за ценой.\n"
        "Команды:\n"
        "/list — показать все отслеживаемые ссылки\n"
        "/remove <номер> — удалить ссылку по номеру"
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовые сообщения, содержащие ссылку."""
    text = update.message.text.strip()
    if "bikeland.ru" not in text:
        await update.message.reply_text("Пожалуйста, отправьте ссылку с bikeland.ru.")
        return

    chat_id = update.effective_chat.id
    if add_link(text, chat_id):
        await update.message.reply_text(
            "✅ Ссылка добавлена! Я буду проверять её каждые 5 минут."
        )
        # Сразу проверим цены и сохраним
        old_price, cur_price = parse_prices(text)
        if old_price or cur_price:
            # Находим ID только что добавленной ссылки
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM links WHERE url = ? AND chat_id = ?",
                (text, chat_id),
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                update_prices(row[0], old_price, cur_price)
    else:
        await update.message.reply_text("⚠️ Эта ссылка уже отслеживается.")

async def list_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    links = get_links(chat_id)
    if not links:
        await update.message.reply_text("Список пуст.")
        return

    parts = ["📋 <b>Отслеживаемые ссылки:</b>\n"]
    for idx, (link_id, url, old_price, current_price) in enumerate(links, start=1):
        safe_url = html.escape(url, quote=True)
        old_display = html.escape(old_price) if old_price else "—"
        cur_display = html.escape(current_price) if current_price else "—"

        parts.append(
            f"{idx}. <a href=\"{safe_url}\">{safe_url}</a>\n"
            f"   Старая: {old_display} | Текущая: {cur_display}\n"
        )

    parts.append("\nЧтобы удалить, используйте <code>/remove &lt;номер&gt;</code>.")
    await update.message.reply_text(
        "\n".join(parts),
        parse_mode="HTML",
        disable_web_page_preview=True,   # чтобы Telegram не разворачивал превью для каждой ссылки
    )

async def remove_link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет ссылку по номеру из списка."""
    chat_id = update.effective_chat.id
    try:
        num = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Использование: /remove <номер>")
        return

    links = get_links(chat_id)
    if num < 1 or num > len(links):
        await update.message.reply_text("Неверный номер.")
        return

    link_id = links[num - 1][0]
    if remove_link(link_id, chat_id):
        await update.message.reply_text(f"🗑️ Ссылка №{num} удалена.")
    else:
        await update.message.reply_text("Не удалось удалить ссылку.")

# --- Периодическая проверка ---
async def check_prices(context: ContextTypes.DEFAULT_TYPE):
    """
    Вызывается JobQueue каждые 5 минут.
    Проходит по всем ссылкам, парсит цены и уведомляет об изменениях.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, url, old_price, current_price, chat_id FROM links")
    rows = cursor.fetchall()
    conn.close()

    for link_id, url, old_price, current_price, chat_id in rows:
        new_old, new_cur = parse_prices(url)
        if new_old is None and new_cur is None:
            continue  # Пропускаем ошибки парсинга

        # Сравниваем с сохранёнными значениями
        if new_old != old_price or new_cur != current_price:
            # Обновляем БД
            update_prices(link_id, new_old, new_cur)

            # Формируем сообщение
            msg = f"🔔 *Изменение цены!*\n{url}\n"
            if new_old != old_price:
                msg += f"Старая цена: {old_price or '—'} → {new_old or '—'}\n"
            if new_cur != current_price:
                msg += f"Текущая цена: {current_price or '—'} → {new_cur or '—'}\n"

            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode="Markdown",
                )
            except Exception as e:
                print(f"Не удалось отправить сообщение в чат {chat_id}: {e}")

# --- Запуск ---
def main():
    init_db()
    application = Application.builder().token(TOKEN).build()

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", list_links))
    application.add_handler(CommandHandler("remove", remove_link_cmd))

    # Обработка текстовых сообщений (ссылок)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link)
    )

    # Периодическая задача каждые 5 минут
    job_queue = application.job_queue
    job_queue.run_repeating(check_prices, interval=CHECK_INTERVAL, first=10)

    print("Бот запущен. Ожидание сообщений...")
    application.run_polling()

if __name__ == "__main__":
    main()