import logging
import re
import datetime
from typing import Dict, Any

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update,
    ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
)
from telegram.ext import ContextTypes

from config import (
    PAYMENT_ACCOUNTS, COUPON_PAYMENT_ACCOUNTS, FAQS,
    HELP_TOPICS, WEBAPP_URL, ADMIN_ID
)
from db import get_user, is_registered, create_user, log_interaction
from utils import (
    validate_email, validate_phone, sanitize_input, generate_referral_code,
    command_limiter, withdrawal_limiter, log_action, format_user_stats
)
from redis_cache import get_cached_user, set_cached_user, invalidate_user_cache
from error_handlers import handle_db_error, handle_validation_error

logger = logging.getLogger(__name__)

class UserHandlers:
    """Handle user-related bot commands and interactions"""

    def __init__(self, user_state: Dict[int, Dict[str, Any]]):
        self.user_state = user_state

    @handle_db_error
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        chat_id = update.effective_chat.id

        # Rate limiting check
        if not command_limiter.is_allowed(chat_id):
            await update.message.reply_text("Please wait a moment before using commands again.")
            return

        referral_code = generate_referral_code()
        args = context.args
        referred_by = None

        if args and args[0].startswith("ref_"):
            try:
                referred_by = int(args[0].split("_")[1])
            except (IndexError, ValueError):
                pass

        log_interaction(chat_id, "start")

        try:
            # Check if user exists
            user = get_user(chat_id)
            if not user:
                create_user(chat_id, update.effective_user.username or "Unknown", referral_code, referred_by)

            keyboard = [[InlineKeyboardButton("🚀 Get Started", callback_data="menu")]]
            await update.message.reply_text(
                "Welcome to Glamour!\n\n"
                "Social Media is the new Oil Money and Glamour will help you get started mining form it.\n"
                "Get paid for using your phone and doing what you love most.\n"
                "• Read posts ➜ earn $2.5/10 words\n• Take a Walk ➜ earn $5\n"
                "• Connect with friends with streaks ➜ earn up to $20\n"
                "• Invite friends and more!\n\n"
                "Choose your package and start earning today.\nClick the button below to get started.",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

            reply_keyboard = [["/menu(🔙)"]]
            if is_registered(chat_id):
                reply_keyboard.append([KeyboardButton(text="Play Glamour", web_app=WebAppInfo(url=f"{WEBAPP_URL}/?chat_id={chat_id}"))])

        except Exception as e:
            logger.error(f"Unexpected error in start: {e}")
            await update.message.reply_text("An unexpected error occurred. Please try again or contact @bigscottmedia.")

    @handle_db_error
    async def cmd_game(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle game command"""
        chat_id = update.effective_user.id

        if not is_registered(chat_id):
            await update.message.reply_text("Please complete registration to get login's to Glamour.")
            return

        kb = [[KeyboardButton(
            text="Play Glamour",
            web_app=WebAppInfo(
                url=f"{WEBAPP_URL}/?chat_id={chat_id}&username={update.effective_user.username or 'guest'}"
            )
        )]]
        await update.message.reply_text(
            "Tap to earn coins!",
            reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
        )

    @handle_db_error
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle stats command"""
        chat_id = update.effective_chat.id
        log_interaction(chat_id, "stats")

        try:
            # Try cache first
            user_data = get_cached_user(chat_id)
            if not user_data:
                user_data = get_user(chat_id)
                if user_data:
                    set_cached_user(chat_id, user_data)

            if not user_data:
                response_text = "No user data found. Please start with /start."
                if update.callback_query:
                    await update.callback_query.answer("No user data found. Please start with /start.")
                    await update.callback_query.edit_message_text(response_text)
                else:
                    await update.message.reply_text(response_text)
                return

            text = format_user_stats(user_data)

            keyboard = []
            if user_data.get('balance', 0) >= 30:
                keyboard = [[InlineKeyboardButton("💸 Withdraw", callback_data="withdraw")]]

            if update.callback_query:
                await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

        except Exception as e:
            logger.error(f"Error in stats: {e}")
            await update.message.reply_text("An error occurred. Please try again.")

    async def support(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle support command"""
        chat_id = update.effective_chat.id
        self.user_state[chat_id] = {'expecting': 'support_message'}
        await update.message.reply_text("Please describe your issue or question:")
        log_interaction(chat_id, "support_initiated")

    async def reset_state(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Reset user state"""
        chat_id = update.effective_chat.id
        if chat_id in self.user_state:
            del self.user_state[chat_id]
        await update.message.reply_text("State reset. Try the flow again.")
        log_interaction(chat_id, "reset_state")

class AdminHandlers:
    """Handle admin-specific commands"""

    @handle_db_error
    async def add_task(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Add new task (admin only)"""
        chat_id = update.effective_chat.id
        if chat_id != ADMIN_ID:
            await update.message.reply_text("This command is restricted to the admin.")
            return

        args = context.args
        if len(args) != 3:
            await update.message.reply_text("Usage: /add_task <type> <link> <reward>")
            return

        task_type, link, reward = args
        try:
            reward = float(reward)
        except ValueError:
            await update.message.reply_text("Reward must be a number.")
            return

        from db import get_conn, return_conn
        import psycopg

        conn = get_conn()
        try:
            cursor = conn.cursor()
            created_at = datetime.datetime.now()
            expires_at = created_at + datetime.timedelta(days=1)

            cursor.execute(
                "INSERT INTO tasks (type, link, reward, created_at, expires_at) VALUES (%s, %s, %s, %s, %s)",
                (task_type, link, reward, created_at, expires_at)
            )
            conn.commit()

            await update.message.reply_text("Task added successfully.")
            log_interaction(chat_id, "add_task")

        except psycopg.Error as e:
            logger.error(f"Database error in add_task: {e}")
            await update.message.reply_text("An error occurred. Please try again.")
        finally:
            return_conn(conn)

    async def broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start broadcast message flow (admin only)"""
        chat_id = update.effective_chat.id
        if chat_id != ADMIN_ID:
            await update.message.reply_text("This command is restricted to the admin.")
            return

        context.user_data['expecting'] = 'broadcast_message'
        await update.message.reply_text("Please enter the broadcast message to send to all registered users:")
        log_interaction(chat_id, "broadcast_initiated")
