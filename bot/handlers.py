"""Telegram update handlers."""

import datetime
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.error import Conflict, NetworkError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot import commands, db


logger = logging.getLogger(__name__)

# Keys used to read shared connections from Application.bot_data.
DB_KEY = "db"

# Message counts are intentionally process-local and reset after a redeploy.
_LOCAL_MESSAGE_COUNTS: dict[int, int] = {}

BOT_COMMANDS = (
    ("start", "Запустить бота"),
    #("help", "Show help"),
    ("about", "Информация о боте"),
    #("ping", "Check bot status"),
)

#MENU_HELP = "Help"
MENU_ABOUT = "Информация о боте"
#MENU_PING = "Ping"

ABOUT_TEXT = """Доступные команды:
/start - Запустить бота
/about - Информация о боте"""

DEFAULT_ECHO_TEXT = "Выберите интересующий вас раздел в меню:"

DYNAMIC_CALLBACK_PREFIX = "command:"


def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    # Шаблон структуры: { row_index: [ (sort_order, label), ... ] }
    # Сразу сажаем MENU_ABOUT в первый ряд (0) с наивысшим приоритетом сортировки (-100)
    keyboard_structure: dict[int, list[tuple[int, str]]] = {
        0: [(-100, MENU_ABOUT)]
    }
    
    # Распределяем кнопки из базы по рядам
    for button in commands.reply_menu_buttons():
        r_index = button.get("row_index", 0)
        s_order = button.get("sort_order", 0)
        label = button.get("label", "Кнопка")
        
        keyboard_structure.setdefault(r_index, []).append((s_order, label))
    
    # Собираем финальную сетку клавиатуры
    rows: list[list[str]] = []
    # Сортируем сами ряды по возрастанию (0, 1, 2...)
    for r_index in sorted(keyboard_structure.keys()):
        # Сортируем кнопки ВНУТРИ текущего ряда по их sort_order
        sorted_buttons_in_row = sorted(keyboard_structure[r_index], key=lambda item: item[0])
        # Извлекаем только чистый текст кнопок (label)
        clean_row = [label for s_order, label in sorted_buttons_in_row]
        rows.append(clean_row)
        
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите пункт меню", 
    )


def _dynamic_commands_text() -> str:
    # Заменили на about_menu_commands(), чтобы выводить только разрешенные для /about команды
    items = commands.about_menu_commands()
    if not items:
        return ""
    lines = [f"/{name} - {description}" for name, description in items]
    return "\n\nДоступные команды меню:\n" + "\n".join(lines)


def _start_commands_keyboard() -> InlineKeyboardMarkup | None:
    items = commands.start_menu_commands() # Берем только для старта
    if not items:
        return None
    buttons = [
        InlineKeyboardButton(text=description, callback_data=f"{DYNAMIC_CALLBACK_PREFIX}{name}")
        for name, description in items
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def _start_response_keyboard() -> ReplyKeyboardMarkup | None:
    # Получаем команды, отсортированные по ID, у которых включен показ в старте
    items = commands.start_menu_commands()
    if not items:
        return None
        
    # Собираем чистый текст для кнопок (берём красивые описания вместо команд со слэшем)
    buttons = [description for name, description in items]
    
    # Распределяем кнопки по 2 штуки в один ряд
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите пункт меню"
    )


def _about_commands_keyboard() -> InlineKeyboardMarkup | None:
    items = commands.about_menu_commands() # Берем только для общих ответов/about
    if not items:
        return None
    buttons = [
        InlineKeyboardButton(text=description, callback_data=f"{DYNAMIC_CALLBACK_PREFIX}{name}")
        for name, description in items
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    received_at = datetime.datetime.now(datetime.timezone.utc)
    if message is None or user is None:
        return

    # Persist the user in PostgreSQL when available (insert on first contact,
    # refresh otherwise). Without a database the bot still greets the user.
    pool = context.bot_data.get(DB_KEY)
    is_new = True
    if pool is not None:
        is_new = await db.upsert_user(pool, user.id, user.username, user.first_name)

    name = user.first_name if user.first_name else "посетитель"
    greeting = "Здравствуйте" if is_new else "Добро пожаловать"
    full_start_text = (f"{greeting}, {name}!\n\n"
            "Предлагаем вам ознакомиться с основными нюансами, которые нужно знать перед регистрацией аккаунта на третье лицо.")
    
    await message.reply_text(
        full_start_text,
        reply_markup=_main_menu_keyboard(),
    )
    dynamic_keyboard = _start_commands_keyboard()
    if dynamic_keyboard is not None:
        await message.reply_text("Выберите команду:", reply_markup=dynamic_keyboard)
        pool = context.bot_data.get(DB_KEY)
    if pool and user:
        replied_at = datetime.datetime.now(datetime.timezone.utc)
        await db.log_user_interaction(
            pool,
            telegram_id=user.id,
            username=user.username,
            user_message_type="command",
            user_text="/start",
            reply_type="text",
            reply_description="Системное приветствие",
            reply_command="start",
            reply_text=full_start_text,
            received_at=received_at,
            replied_at=replied_at
        )


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    received_at = datetime.datetime.now(datetime.timezone.utc)
    
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    # Собираем полный текст в одну переменную
    full_about_text = (
        ABOUT_TEXT
        + _dynamic_commands_text()
        + "\n\nВыберите интересующий вас раздел в меню ниже:"
    )

    # Берем ту же самую нижнюю стартовую response-клавиатуру
    response_keyboard = _start_response_keyboard()

    # Отправляем
    await message.reply_text(full_about_text, reply_markup=response_keyboard)
    
    pool = context.bot_data.get(DB_KEY)
    if pool:
        replied_at = datetime.datetime.now(datetime.timezone.utc)
        await db.log_user_interaction(
            pool,
            telegram_id=user.id,
            username=user.username,
            user_message_type="command",
            user_text="/about",
            reply_type="text",
            reply_description="Информация о доступных командах бота",
            reply_command="about",
            reply_text=full_about_text,
            received_at=received_at,
            replied_at=replied_at
        )


async def menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    received_at = datetime.datetime.now(datetime.timezone.utc)
    message = update.effective_message
    user = update.effective_user
    if message is None or not message.text or user is None:
        return

    pool = context.bot_data.get(DB_KEY)
    text = message.text.strip()
    
    if text == MENU_ABOUT:
        await about(update, context)
        return

    matched_command = None
    for cmd in commands._REGISTRY.values():
        if cmd.get("description") and cmd["description"].strip() == text:
            matched_command = cmd
            break

    if matched_command:
        await commands.send(message, matched_command)
        if pool:
            replied_at = datetime.datetime.now(datetime.timezone.utc)
            await db.log_user_interaction(
                pool,
                telegram_id=user.id,
                username=user.username,
                user_message_type="response_button",
                user_text=text,
                reply_type=matched_command.get("reply_type", "text"),
                reply_description=matched_command.get("description"),
                reply_command=matched_command["name"],
                reply_text=matched_command.get("reply_text") or "[Media Content]",
                received_at=received_at,
                replied_at=replied_at
            )
        return

    target = commands.button_target(text)
    if target is not None:
        command = commands.lookup(target)
        if command is not None:
            await commands.send(message, command)
            if pool:
                replied_at = datetime.datetime.now(datetime.timezone.utc)
                await db.log_user_interaction(
                    pool,
                    telegram_id=user.id,
                    username=user.username,
                    user_message_type="response_button",
                    user_text=text,
                    reply_type=command.get("reply_type", "text"),
                    reply_description=command.get("description"),
                    reply_command=target,
                    reply_text=command.get("reply_text") or "[Media Content]",
                    received_at=received_at,
                    replied_at=replied_at
                )
        return

    await echo_message(update, context)


async def echo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    received_at = datetime.datetime.now(datetime.timezone.utc)
    message = update.effective_message
    user = update.effective_user
    if message is None or not message.text or user is None:
        return

    pool = context.bot_data.get(DB_KEY)
    
    # ПРИНУДИТЕЛЬНО вызываем ту самую клавиатуру, которая идет при /start
    main_keyboard = _main_menu_keyboard()

    # Отправляем сообщение с главной стартовой клавиатурой
    await message.reply_text(
        DEFAULT_ECHO_TEXT,
        reply_markup=main_keyboard,
    )

    # Записываем взаимодействие в историю
    if pool:
        replied_at = datetime.datetime.now(datetime.timezone.utc)
        await db.log_user_interaction(
            pool,
            telegram_id=user.id,
            username=user.username,
            user_message_type="text",
            user_text=message.text,
            reply_type="text",
            reply_description="Ответ на произвольный пользовательский текст со стандартным меню",
            reply_command="echo",
            reply_text=DEFAULT_ECHO_TEXT,
            received_at=received_at,
            replied_at=replied_at
        )


def _parse_command_name(text: str) -> str:
    """Extract the bare command name from message text (e.g. '/promo@bot a' -> 'promo')."""
    token = text.strip().split(maxsplit=1)[0]  # '/promo@bot'
    token = token.lstrip("/")
    token = token.split("@", 1)[0]  # drop optional @botusername
    return token.lower()


async def dynamic_command_dispatcher(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle any command not served by a built-in handler.

    Looks the command up in the panel-managed registry and replies with its
    configured response, falling back to the 'unknown command' message.
    """
    del context
    message = update.effective_message
    if message is None or not message.text:
        return

    command = commands.lookup(_parse_command_name(message.text))
    if command is not None:
        await commands.send(message, command)
        return

    await message.reply_text("Неизвестная команда. Напишите /about для ознакомления с доступными командами.")


async def dynamic_command_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    received_at = datetime.datetime.now(datetime.timezone.utc)
    query = update.callback_query
    user = update.effective_user
    if query is None or query.data is None or user is None:
        return

    pool = context.bot_data.get(DB_KEY)
    name = query.data.replace(DYNAMIC_CALLBACK_PREFIX, "")
    command = commands.lookup(name)
    
    if command is not None:
        await query.answer()
        await commands.send(query.message, command)
        
        if pool:
            replied_at = datetime.datetime.now(datetime.timezone.utc)
            button_label = command.get("description") or f"Inline: /{name}"
            await db.log_user_interaction(
                pool,
                telegram_id=user.id,
                username=user.username,
                user_message_type="inline_button",
                user_text=button_label,
                reply_type=command.get("reply_type", "text"),
                reply_description=command.get("description"),
                reply_command=name,
                reply_text=command.get("reply_text") or "[Media Content]",
                received_at=received_at,
                replied_at=replied_at
            )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error

    # Transient polling/network errors (e.g. a brief 409 Conflict during a
    # Railway redeploy when two instances overlap) are self-healing, so log them
    # as warnings without a traceback instead of alarming-looking errors.
    if isinstance(error, (Conflict, NetworkError, TimedOut)):
        logger.warning("Transient Telegram error: %s", error)
        return

    logger.exception("Error while processing update: %s", update, exc_info=error)

    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "Возникла ошибка при обработке сообщения."
        )


async def set_bot_commands(application: Application) -> None:
    """Publish the built-in commands plus any panel-managed ones to Telegram."""
    menu = list(BOT_COMMANDS) + commands.about_menu_commands()
    await application.bot.set_my_commands(menu)


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(
        CallbackQueryHandler(
            dynamic_command_button,
            pattern=f"^{DYNAMIC_CALLBACK_PREFIX}[a-z0-9_]{{1,32}}$",
        )
    )
    # Any other /command is resolved dynamically from the panel-managed registry.
    application.add_handler(MessageHandler(filters.COMMAND, dynamic_command_dispatcher))
    
    # Направляем все текстовые сообщения сначала в menu_button для проверки на кнопки.
    # Если это не кнопка, menu_button сама внутри перенаправит в echo_message.
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_button))
