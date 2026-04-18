import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors in handlers"""
    
    # Log the error
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    
    # Get user info for logging
    chat_id = None
    user = None
    if update and update.effective_user:
        user = update.effective_user
        chat_id = update.effective_chat.id
    
    # Log with context
    logger.error(
        f"Error for user {user} (ID: {chat_id}): {context.error}",
        exc_info=True
    )
    
    # Notify user if possible
    if update and update.message:
        try:
            await update.message.reply_text(
                "An error occurred while processing your request. "
                "Our team has been notified. Please try again later.",
                quote=False
            )
        except TelegramError as e:
            logger.error(f"Failed to send error notification: {e}")
    
    # Send notification to admin
    if context.bot and context.bot._bot_data and update:
        try:
            from config import ADMIN_ID
            error_message = (
                f"⚠️ ERROR REPORT\n\n"
                f"User: {user}\n"
                f"Chat ID: {chat_id}\n"
                f"Error: {context.error}\n"
            )
            await context.bot.send_message(ADMIN_ID, error_message)
        except Exception as e:
            logger.error(f"Failed to send error report to admin: {e}")

async def handle_invalid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle invalid/unknown commands"""
    await update.message.reply_text(
        "I don't recognize that command. Use /help to see available commands."
    )

class DatabaseError(Exception):
    """Custom exception for database errors"""
    pass

class ValidationError(Exception):
    """Custom exception for validation errors"""
    pass

def handle_db_error(func):
    """Decorator for database error handling"""
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Database error in {func.__name__}: {e}")
            update = args[0] if args else None
            if update and update.message:
                await update.message.reply_text(
                    "A database error occurred. Please try again later."
                )
    return wrapper

def handle_validation_error(func):
    """Decorator for validation error handling"""
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except ValidationError as e:
            update = args[0] if args else None
            if update and update.message:
                await update.message.reply_text(f"Validation error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in {func.__name__}: {e}")
            update = args[0] if args else None
            if update and update.message:
                await update.message.reply_text(
                    "An unexpected error occurred. Please try again."
                )
    return wrapper
