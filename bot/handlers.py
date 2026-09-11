"""Telegram update handlers."""

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
#/help - Show help
#/ping - Check bot status

DYNAMIC_CALLBACK_PREFIX = "command:"


def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    rows: list[list[str]] =  [[MENU_ABOUT]]
    custom_rows: dict[int, list[str]] = {}
    for button in commands.reply_menu_buttons():
        custom_rows.setdefault(button["row_index"], []).append(button["label"])
    rows.extend(custom_rows[index] for index in sorted(custom_rows))
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите пункт меню", 
    )
        #previously ^^^ :   [[MENU_HELP, MENU_ABOUT], [MENU_PING]]
        #previously ^^^ :   Choose a menu item


def _dynamic_commands_text() -> str:
    items = commands.menu_commands()
    if not items:
        return ""
    lines = [f"/{name} - {description}" for name, description in items]
    return "\n\nДоступные команды меню:\n" + "\n".join(lines) 
        #previously ^^^: "\n\nAvailable menu commands:\n"


def _dynamic_commands_keyboard() -> InlineKeyboardMarkup | None:
    items = commands.menu_commands()
    if not items:
        return None

    buttons = [
        InlineKeyboardButton(
            description or f"/{name}",
            callback_data=f"{DYNAMIC_CALLBACK_PREFIX}{name}",
        )
        for name, description in items
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
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
    await message.reply_text(
        f"{greeting}, {name}!\n\n"
        "Предлагаем вам ознакомиться с основными нюансами, которые нужно знать перед регистрацией аккаунта на третье лицо.",
        reply_markup=_main_menu_keyboard(),
    )
    dynamic_keyboard = _dynamic_commands_keyboard()
    if dynamic_keyboard is not None:
        await message.reply_text("Выберите команду:", reply_markup=dynamic_keyboard)

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    message = update.effective_message
    if message is None:
        return

    await message.reply_text(
        ABOUT_TEXT
        + _dynamic_commands_text()
        + "\n\nЛюбое отправленное текстовое сообщение, автоматически вызывает подсказку /about.",
        reply_markup=_dynamic_commands_keyboard(),
    )

# async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    #del context
    #message = update.effective_message
    #if message is None:
    #    return
    #
    #await message.reply_text(
    #    HELP_TEXT
    #    + _dynamic_commands_text()
    #    + "\n\nОтправьте обычное текстовое сообщение, и бот ответит вам тем же.",
    #    reply_markup=_dynamic_commands_keyboard(),
    #)


#async def about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    #del context
    #message = update.effective_message
    #if message is None:
    #    return
    #
    #await message.reply_text(
    #    "This bot is built with python-telegram-bot and is ready to deploy on Railway."
    #)


#async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    #message = update.effective_message
    #if message is None:
    #    return
    #
    #del context
    #await message.reply_text("pong")


async def menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return

    text = message.text.strip()
    if text == MENU_ABOUT:
        await about(update, context)

    #if text == MENU_HELP:
    #    await help_command(update, context)
    #elif text == MENU_ABOUT:
    #    await about(update, context)
    #elif text == MENU_PING:
    #    await ping(update, context)


async def echo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = await about(update, context)
    #message = update.effective_message
    user = update.effective_user
    if message is None or not message.text or user is None:
        return

    target = commands.button_target(message.text.strip())
    if target is not None:
        if target == "about":
            await about(update, context)
        elif target == "start":
            await start(update, context)
        else:
            command = commands.lookup(target)
            if command is None:
                await message.reply_text("В данный момент эта команда недоступна.")
            else:
                await commands.send(message, command)
        return

        #if target == "help":
        #    await help_command(update, context)
        #elif target == "about":
        #    await about(update, context)
        #elif target == "ping":
        #    await ping(update, context)
        #elif target == "start":
        #    await start(update, context)
        #else:
        #    command = commands.lookup(target)
        #    if command is None:
        #        await message.reply_text("This button's command is currently unavailable.")
        #    else:
        #        await commands.send(message, command)
        #return

    count = _LOCAL_MESSAGE_COUNTS[user.id] = _LOCAL_MESSAGE_COUNTS.get(user.id, 0) + 1
    await message.reply_text(f"Вы отправили (#{count}):\n{message.text}")


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
    """Run a panel-managed command selected from an inline button."""
    del context
    query = update.callback_query
    if query is None or query.data is None:
        return

    name = query.data.removeprefix(DYNAMIC_CALLBACK_PREFIX)
    command = commands.lookup(name)
    if command is None:
        await query.answer("Эта команда недоступна.", show_alert=True)
        return

    await query.answer()
    if query.message is not None:
        await commands.send(query.message, command)


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
    menu = list(BOT_COMMANDS) + commands.menu_commands()
    await application.bot.set_my_commands(menu)


def register_handlers(application: Application) -> None:


    application.add_handler(CommandHandler("start", start))
    #application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about))
    #application.add_handler(CommandHandler("ping", ping))
    application.add_handler(
        CallbackQueryHandler(
            dynamic_command_button,
            pattern=f"^{DYNAMIC_CALLBACK_PREFIX}[a-z0-9_]{{1,32}}$",
        )
    )
    # Any other /command is resolved dynamically from the panel-managed registry.
    application.add_handler(MessageHandler(filters.COMMAND, dynamic_command_dispatcher))
    application.add_handler(
        MessageHandler(filters.Regex(f"^({MENU_ABOUT})$"), menu_button)
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_message))
        #previously ^^^ :   MessageHandler(filters.Regex(f"^({MENU_HELP}|{MENU_ABOUT}|{MENU_PING})$"), menu_button)