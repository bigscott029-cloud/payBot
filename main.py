#!/usr/bin/env python3
# main.py — Glamour Main Bot for Telegram (modular version)

import logging
import os
import datetime
import asyncio
from threading import Thread
from flask import Flask
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    KeyboardButton,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

import config
from config import (
    BOT_TOKEN,
    ADMIN_ID,
    PAYMENT_ACCOUNTS,
    COUPON_PAYMENT_ACCOUNTS,
    FAQS,
    HELP_TOPICS,
    WEBAPP_URL,
    GROUP_LINK,
    DAILY_TASK_LINK,
    SITE_LINK,
    AI_BOOST_LINK,
    FLUTTERWAVE_PAYMENT_LINK,
)
from db import (
    init_database,
    get_status,
    is_registered,
    get_user,
    create_user,
    log_interaction,
    get_conn,
    return_conn,
)
from payments import create_payment, get_payment, approve_payment, reject_payment
from utils import (
    validate_email,
    validate_phone,
    validate_username,
    sanitize_input,
    generate_referral_code,
    command_limiter,
    withdrawal_limiter,
    log_action,
)
from admin_handlers import (
    admin_analytics,
    admin_broadcast,
    admin_stats_by_package,
    admin_manual_payment_approval,
    admin_approve_payment,
    admin_reject_payment,
    admin_pending_payments,
    admin_help,
)
from error_handlers import error_handler, handle_invalid_command

# Flask setup for keep-alive
app = Flask(__name__)

# Global application instance
application = None


@app.route('/')
def home():
    return "Glamour is alive!"


def run_web():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)


def keep_alive():
    from threading import Thread
    thread = Thread(target=run_web, daemon=True)
    thread.start()


if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required in environment (.env)")
if not ADMIN_ID:
    raise ValueError("ADMIN_ID is required in environment (.env)")

user_state = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not command_limiter.is_allowed(chat_id):
        await update.message.reply_text("Please wait a moment before trying again.")
        return

    args = context.args
    referred_by = None
    if args and args[0].startswith("ref_"):
        try:
            referred_by = int(args[0].split("_")[1])
        except (IndexError, ValueError):
            referred_by = None

    log_interaction(chat_id, "start")

    user = get_user(chat_id)
    if not user:
        referral_code = generate_referral_code()
        create_user(chat_id, update.effective_user.username or "Unknown", referral_code, referred_by)
        log_action(chat_id, "user_created", details=f"referred_by={referred_by}")

    keyboard = [[InlineKeyboardButton("🚀 Get Started", callback_data="menu")]]
    await update.message.reply_text(
        "Welcome to Glamour!\n\n"
        "Social Media is the new Oil Money and Glamour will help you get started mining from it.\n"
        "Get paid for using your phone and doing what you love most.\n"
        "• Read posts ➜ earn $2.5/10 words\n"
        "• Take a Walk ➜ earn $5\n"
        "• Connect with friends with streaks ➜ earn up to $20\n"
        "• Invite friends and more!\n\n"
        "Choose your package and start earning today. Click the button below to continue.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        chat_id = update.callback_query.from_user.id
        await update.callback_query.answer()
    else:
        chat_id = update.effective_chat.id

    log_interaction(chat_id, "show_main_menu")
    user = get_user(chat_id)
    buttons = [
        [InlineKeyboardButton("How It Works", callback_data="how_it_works")],
        [InlineKeyboardButton("Purchase Coupon Code", callback_data="coupon")],
        [InlineKeyboardButton("💸 Get Registered", callback_data="package_selector")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]

    if user and user["payment_status"] == 'registered':
        buttons = [
            [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
            [InlineKeyboardButton("Do Daily Tasks", callback_data="daily_tasks")],
            [InlineKeyboardButton("💰 Earn Extra for the Day", callback_data="earn_extra")],
            [InlineKeyboardButton("Purchase Coupon", callback_data="coupon")],
            [InlineKeyboardButton("❓ Help", callback_data="help")],
        ]
        if user["package"] == "X":
            buttons.insert(1, [InlineKeyboardButton("🚀 Boost with AI", callback_data="boost_ai")])

    text = "Select an option below:"
    markup = InlineKeyboardMarkup(buttons)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup)
    else:
        await update.message.reply_text(text, reply_markup=markup)


async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return
    chat_id = update.callback_query.from_user.id
    await update.callback_query.answer()

    buttons = [[InlineKeyboardButton(topic["label"], callback_data=key)] for key, topic in HELP_TOPICS.items()]
    user = get_user(chat_id)
    if user and user["payment_status"] == 'registered':
        buttons.append([InlineKeyboardButton("👥 Refer a Friend", callback_data="refer_friend")])
    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu")])

    await update.callback_query.edit_message_text("Help topics:", reply_markup=InlineKeyboardMarkup(buttons))


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    log_interaction(chat_id, "stats")
    user = get_user(chat_id)
    if not user:
        await update.message.reply_text("No user data found. Please start with /start.")
        return

    text = (
        "📊 Your Platform Stats:\n\n"
        f"• Package: {user.get('package') or 'Not selected'}\n"
        f"• Payment Status: {user.get('payment_status', 'unknown').capitalize()}\n"
        f"• Streaks: {user.get('streaks', 0)}\n"
        f"• Invites: {user.get('invites', 0)}\n"
        f"• Balance: ${user.get('balance', 0):.2f}"
    )
    await update.message.reply_text(text)


async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_state[chat_id] = {'expecting': 'support_message'}
    await update.message.reply_text("Please describe your issue or question:")
    log_interaction(chat_id, "support_initiated")


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        reward_value = float(reward)
    except ValueError:
        await update.message.reply_text("Reward must be a number.")
        return

    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO tasks (type, link, reward, expires_at) VALUES (%s, %s, %s, %s)",
            (task_type, link, reward_value, datetime.datetime.now() + datetime.timedelta(days=1)),
        )
        await update.message.reply_text("Task added successfully.")
        log_interaction(chat_id, "add_task")
    except Exception as exc:
        logger.error(f"Error adding task: {exc}")
        await update.message.reply_text("An error occurred while adding the task.")
    finally:
        return_conn(conn)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = user_state.get(chat_id, {})
    if state.get('expecting') != 'reg_screenshot':
        return

    file_id = update.message.photo[-1].file_id
    package = state.get('package')
    account = state.get('selected_account')
    if not package or not account:
        await update.message.reply_text("Please choose a package and payment account before sending your screenshot.")
        return

    payment_method = state.get('payment_method', 'manual')
    if payment_method == 'manual' and account == FLUTTERWAVE_PAYMENT_LINK:
        payment_method = 'flutterwave'

    total_amount = 14000 if package == 'X' else 20000
    try:
        payment_id = create_payment(
            chat_id=chat_id,
            payment_type='registration',
            package=package,
            quantity=1,
            total_amount=total_amount,
            payment_account=account,
            is_upgrade=(package == 'X'),
            status='pending_payment',
            method=payment_method,
        )

        await context.bot.send_photo(
            ADMIN_ID,
            file_id,
            caption=(
                f"📌 Registration payment screenshot from @{update.effective_user.username or 'Unknown'} "
                f"(chat_id: {chat_id})\nPackage: {package}\nPayment method: {payment_method}\nPayment ID: {payment_id}"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Approve", callback_data=f"approve_payment_{payment_id}")],
                [InlineKeyboardButton("Reject", callback_data=f"reject_payment_{payment_id}")],
            ]),
        )
        await update.message.reply_text(
            "Screenshot received! Await admin approval. You can check back later with /stats or /menu."
        )
        user_state[chat_id]['expecting'] = None
        user_state[chat_id]['payment_id'] = payment_id
    except Exception as exc:
        logger.error(f"Error saving payment screenshot: {exc}")
        await update.message.reply_text("An error occurred while uploading the screenshot. Please try again.")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = user_state.get(chat_id, {})
    if state.get('expecting') != 'reg_screenshot':
        return

    file_id = update.message.document.file_id
    if not update.message.document.mime_type.startswith('image/'):
        await update.message.reply_text("Please send an image file such as PNG or JPG.")
        return

    await handle_photo(update, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = sanitize_input(update.message.text)
    log_interaction(chat_id, "text_message")

    if context.user_data.get('expecting') == 'broadcast_message' and chat_id == ADMIN_ID:
        message = text
        conn = get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT chat_id FROM users WHERE payment_status IS NOT NULL")
            user_rows = cursor.fetchall()
            count = 0
            for row in user_rows:
                try:
                    await context.bot.send_message(row['chat_id'], message)
                    count += 1
                except Exception:
                    continue
            await update.message.reply_text(f"Broadcast sent to {count} users.")
            context.user_data['expecting'] = None
        except Exception as exc:
            logger.error(f"Broadcast error: {exc}")
            await update.message.reply_text("Failed to send broadcast.")
        finally:
            return_conn(conn)
        return

    state = user_state.get(chat_id, {})
    if state.get('expecting') == 'support_message':
        await context.bot.send_message(ADMIN_ID, f"Support request from @{update.effective_user.username or 'Unknown'} ({chat_id}): {text}")
        await update.message.reply_text("Thank you! Our support team will contact you soon.")
        state['expecting'] = None
        return

    if state.get('expecting') == 'name':
        if len(text) < 3:
            await update.message.reply_text("Please enter a valid full name.")
            return
        state['name'] = text
        state['expecting'] = 'email'
        await update.message.reply_text("Great! Now send your email address.")
        return

    if state.get('expecting') == 'email':
        if not validate_email(text):
            await update.message.reply_text("Please enter a valid email address.")
            return
        state['email'] = text
        state['expecting'] = 'phone'
        await update.message.reply_text("Please send your phone number with country code (e.g. +2341234567890).")
        return

    if state.get('expecting') == 'phone':
        if not validate_phone(text):
            await update.message.reply_text("Please enter a valid phone number.")
            return
        state['phone'] = text
        state['expecting'] = 'telegram_username'
        await update.message.reply_text("Please send your Telegram username (e.g. @yourname).")
        return

    if state.get('expecting') == 'telegram_username':
        if not validate_username(text.lstrip('@')):
            await update.message.reply_text("Please send a valid Telegram username starting with @.")
            return
        username = text if text.startswith('@') else f"@{text}"
        conn = get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET name=%s, email=%s, phone=%s, username=%s, payment_status=%s, registration_date=%s WHERE chat_id=%s",
                (state['name'], state['email'], state['phone'], username, 'registered', datetime.datetime.now(), chat_id),
            )
            await update.message.reply_text(
                "🎉 Registration complete! Your account is now active.\n"
                "You can now use the menu to access your tasks and start earning."
            )
        except Exception as exc:
            logger.error(f"Error saving registration details: {exc}")
            await update.message.reply_text("An error occurred while completing registration.")
        finally:
            return_conn(conn)
        state['expecting'] = None
        return

    if state.get('expecting') == 'password_recovery':
        if not validate_email(text):
            await update.message.reply_text("Please provide a valid email address.")
            return
        conn = get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT username FROM users WHERE email=%s AND chat_id=%s AND payment_status='registered'", (text, chat_id))
            row = cursor.fetchone()
            if row:
                await update.message.reply_text("A password recovery request has been received. The admin will respond shortly.")
                await context.bot.send_message(ADMIN_ID, f"Password recovery requested by {chat_id} for email {text}.")
            else:
                await update.message.reply_text("No registered account found with that email.")
        except Exception as exc:
            logger.error(f"Password recovery error: {exc}")
            await update.message.reply_text("An error occurred while processing password recovery.")
        finally:
            return_conn(conn)
        state['expecting'] = None
        return

    if state.get('expecting') == 'faq':
        await context.bot.send_message(ADMIN_ID, f"FAQ question from @{update.effective_user.username or 'Unknown'} ({chat_id}): {text}")
        await update.message.reply_text("Thanks! Our team will answer your question soon.")
        state['expecting'] = None
        return

    # If no state matches, fallback to main menu
    await show_main_menu(update, context)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.from_user.id
    log_interaction(chat_id, f"callback_{data}")

    if data == "menu":
        await show_main_menu(update, context)
        return

    if data == "help":
        await help_menu(update, context)
        return

    if data == "stats":
        await stats(update, context)
        return

    if data == "refer_friend":
        link = f"https://t.me/{context.bot.username}?start=ref_{chat_id}"
        await query.edit_message_text(
            f"👥 Refer a Friend and Earn Rewards!\n\n"
            f"Share your referral link with friends.\n\n"
            f"Your referral link: {link}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Help Menu", callback_data="help")]]),
        )
        return

    if data == "withdraw":
        if not withdrawal_limiter.is_allowed(chat_id):
            await query.answer("Withdrawals are limited. Try again later.")
            return
        user = get_user(chat_id)
        if not user or user.get('balance', 0) < 30:
            await query.answer("Your balance is less than $30.")
            return
        await context.bot.send_message(ADMIN_ID, f"Withdrawal request from @{update.effective_user.username or 'Unknown'} ({chat_id}) amount ${user['balance']:.2f}")
        await query.edit_message_text("Your withdrawal request has been sent to the admin.")
        return

    if data == "package_selector":
        buttons = [
            [InlineKeyboardButton("✈ Glamour Gold Package (₦14,000)", callback_data="reg_standard")],
            [InlineKeyboardButton("🚀 Glamour Diamond Package (₦20,000)", callback_data="reg_x")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu")],
        ]
        await query.edit_message_text("Choose your package:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data in ["reg_standard", "reg_x"]:
        package = "Standard" if data == "reg_standard" else "X"
        user_state[chat_id] = {
            'expecting': 'reg_method',
            'package': package,
            'selected_account': None,
            'payment_method': None,
        }
        buttons = [
            [InlineKeyboardButton("Pay with Flutterwave", url=FLUTTERWAVE_PAYMENT_LINK)],
            [InlineKeyboardButton("I paid with Flutterwave", callback_data="reg_flutterwave")],
            [InlineKeyboardButton("Pay with bank account", callback_data="reg_bank")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu")],
        ]
        await query.edit_message_text(
            "Choose how you want to pay for your package:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if data == "reg_bank":
        state = user_state.setdefault(chat_id, {})
        state['expecting'] = 'reg_screenshot'
        buttons = [[InlineKeyboardButton(name, callback_data=f"reg_account_{name}")] for name in PAYMENT_ACCOUNTS.keys()]
        buttons.append([InlineKeyboardButton("Other country option", callback_data="reg_other")])
        buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu")])
        await query.edit_message_text("Select an account to pay to:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data == "reg_flutterwave":
        state = user_state.setdefault(chat_id, {})
        state['expecting'] = 'reg_screenshot'
        state['payment_method'] = 'flutterwave'
        state['selected_account'] = FLUTTERWAVE_PAYMENT_LINK
        await query.edit_message_text(
            f"Please complete your payment via Flutterwave and then send a screenshot of the successful payment receipt.\n\nPay here: {FLUTTERWAVE_PAYMENT_LINK}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]),
        )
        return

    if data.startswith("reg_account_"):
        account_name = data[len("reg_account_"):]
        payment_details = PAYMENT_ACCOUNTS.get(account_name)
        if not payment_details:
            await query.edit_message_text("Invalid payment account selected. Please try again.")
            return
        state = user_state.setdefault(chat_id, {})
        state['selected_account'] = account_name
        state['payment_method'] = 'manual'
        state['expecting'] = 'reg_screenshot'
        state['package'] = state.get('package', 'Standard')
        await query.edit_message_text(
            f"Payment details:\n\n{payment_details}\n\nPlease pay and send your payment screenshot.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]),
        )
        return

    if data == "reg_other":
        await query.edit_message_text(
            "Please contact @bigscottmedia to complete your payment for other regions.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]),
        )
        return

    if data.startswith("approve_payment_") or data.startswith("reject_payment_"):
        if chat_id != ADMIN_ID:
            await query.answer("Only admin can approve or reject payments.")
            return

        payment_id = int(data.split("_")[-1])
        payment = get_payment(payment_id)
        if not payment:
            await query.edit_message_text("Payment record not found.")
            return

        if data.startswith("approve_payment_"):
            approve_payment(payment_id)
            user_chat_id = payment['chat_id']
            conn = get_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE users SET payment_status=%s WHERE chat_id=%s",
                    ('pending_details', user_chat_id),
                )
                await context.bot.send_message(
                    user_chat_id,
                    "✅ Your payment has been approved by the admin. Please send your full name to continue registration."
                )
                user_state[user_chat_id] = {'expecting': 'name'}
            finally:
                return_conn(conn)
            await query.edit_message_text(f"Payment {payment_id} approved.")
            return

        if data.startswith("reject_payment_"):
            reject_payment(payment_id)
            user_chat_id = payment['chat_id']
            await context.bot.send_message(
                user_chat_id,
                "❌ Your payment has been rejected by the admin. Please review the instructions and try again or contact @bigscottmedia."
            )
            await query.edit_message_text(f"Payment {payment_id} rejected.")
            return

    if data == "coupon":
        await query.edit_message_text(
            "Coupon purchases are currently managed by the admin. Please contact @bigscottmedia.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]),
        )
        return

    if data == "how_it_works":
        await query.edit_message_text(
            "GLAMOUR is a digital earning platform that helps you learn, earn, and grow from your smartphone. "
            "You get paid for daily actions like walking, reading posts, and inviting friends.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]),
        )
        return

    if data == "daily_tasks":
        await query.edit_message_text(
            f"Follow this link to perform your daily tasks and earn: {DAILY_TASK_LINK}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]),
        )
        return

    if data == "boost_ai":
        await query.edit_message_text(
            f"🚀 Boost with AI\n\nAccess advanced AI-powered features here: {AI_BOOST_LINK}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]),
        )
        return

    if data.startswith("faq_"):
        faq_key = data[len("faq_"):]
        if faq_key == "custom":
            state = user_state.setdefault(chat_id, {})
            state['expecting'] = 'faq'
            await query.edit_message_text("Please type your question:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Help Menu", callback_data="help")]]))
        else:
            faq = FAQS.get(faq_key)
            if faq:
                await query.edit_message_text(
                    f"❓ {faq['question']}\n\n{faq['answer']}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 FAQ Menu", callback_data="help"), InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]),
                )
        return

    if data in HELP_TOPICS:
        topic = HELP_TOPICS[data]
        if topic["type"] == "input":
            state = user_state.setdefault(chat_id, {})
            state['expecting'] = data
            await query.edit_message_text(topic["text"], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Help Menu", callback_data="help")]]))
        elif topic["type"] == "toggle":
            await query.edit_message_text(
                "Toggle features are not yet active in this release.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Help Menu", callback_data="help")]]),
            )
        elif topic["type"] == "faq":
            await help_menu(update, context)
        else:
            content = topic.get("text") or topic.get("url")
            await query.edit_message_text(content, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Help Menu", callback_data="help")]]))
        return

    logger.warning(f"Unknown callback data: {data}")
    await query.edit_message_text("Unknown action. Please try again.")


async def daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id FROM users WHERE alarm_setting=1")
        rows = cursor.fetchall()
        for row in rows:
            try:
                await context.bot.send_message(row['chat_id'], "🌟 Daily Reminder: Complete your Glamour tasks today!")
            except Exception as exc:
                logger.error(f"Failed sending reminder to {row['chat_id']}: {exc}")
    except Exception as exc:
        logger.error(f"Error in daily_reminder job: {exc}")
    finally:
        return_conn(conn)


async def run_bot():
    global application
    application = Application.builder().token(BOT_TOKEN).build()

    # === YOUR HANDLERS (exactly as before) ===
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", show_main_menu))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("support", support))
    application.add_handler(CommandHandler("add_task", add_task))
    application.add_handler(CommandHandler("broadcast", admin_broadcast))
    application.add_handler(CommandHandler("analytics", admin_analytics))
    application.add_handler(CommandHandler("stats_package", admin_stats_by_package))
    application.add_handler(CommandHandler("payment_approve", admin_manual_payment_approval))
    application.add_handler(CommandHandler("approve_payment", admin_approve_payment))
    application.add_handler(CommandHandler("reject_payment", admin_reject_payment))
    application.add_handler(CommandHandler("payments_pending", admin_pending_payments))
    application.add_handler(CommandHandler("admin_help", admin_help))

    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.COMMAND, handle_invalid_command))
    application.add_error_handler(error_handler)

    # Job queue
    application.job_queue.run_repeating(daily_reminder, interval=86400, first=30)

    logger.info("🚀 Starting bot with polling...")

    await application.initialize()
    await application.start()
    await application.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )
    # Keep the bot alive forever
    await asyncio.Event().wait()


# ====================== ENTRY POINT ======================
if __name__ == "__main__":
    init_database()
    keep_alive()                    # start the keep-alive first
    asyncio.run(run_bot())          # ← This is the fix for Python 3.14
