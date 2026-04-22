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
    FLUTTERWAVE_BASIC_NEW_USER,
    FLUTTERWAVE_PREMIUM_NEW_USER,
    FLUTTERWAVE_UPGRADE,
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

# ==================== HARDCODED PACKAGE DEFINITIONS ====================
# Package configurations: GlamFee (basic) and GlamPremium (premium)
PACKAGES = {
    'glamfee': {
        'id': 'glamfee',
        'display_name': 'GlamFee',
        'emoji': '💎',
        'price_naira': 14000,
        'price_euro': 7,
        'is_premium': False,
        'is_active': True,  # Always available
        'flutterwave_link': FLUTTERWAVE_BASIC_NEW_USER,
    },
    'glampremium': {
        'id': 'glampremium',
        'display_name': 'GlamPremium',
        'emoji': '👑',
        'price_naira': 35000,
        'price_euro': 18,
        'is_premium': True,
        'is_active': False,  # Deactivated by default - activate with /activate_premium
        'flutterwave_link': FLUTTERWAVE_PREMIUM_NEW_USER,
    },
}

PREMIUM_FEATURES = {
    'priority_support': True,
    'bonus_earning_rate': 1.5,
    'exclusive_tasks': True,
    'vip_group_access': True,
    'monthly_cash_bonus': 5000,
    'referral_bonus_multiplier': 2.0,
    'advanced_analytics': True,
    'withdrawal_fee_waived': True,
}

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
        "✨ *Welcome to GLAMOUR!* ✨\n\n"
        "💡 *The Luxury of Digital Earning*\n"
        "Your social influence is currency in the modern economy. Glamour transforms your online presence into real wealth.\n\n"
        "🎯 *How GLAMOUR Works*\n"
        "• Share your lifestyle - €2/hour\n"
        "• Engage with content - €2/hour\n"
        "• Build your network - €2/hour\n"
        "• Complete tasks - Unlimited earning potential\n"
        "• Earn through referrals - 2x multiplier\n\n"
        "💰 *Get Started Today*\n"
        "Choose your GLAMOUR package and start your luxury earning journey. "
        "Quick recovery strategy with multiple income streams equals sustainable wealth.\n\n"
        "🌟 *Join thousands earning globally right now!*",
        parse_mode='Markdown',
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
    payment_method = state.get('payment_method', 'manual')
    
    if not package or not account:
        await update.message.reply_text("Please choose a package and payment account before sending your screenshot.")
        return

    total_amount = 14000
    
    try:
        payment_id = create_payment(
            chat_id=chat_id,
            payment_type='registration',
            package=package,
            quantity=1,
            total_amount=total_amount,
            payment_account=account,
            is_upgrade=False,
            status='pending_payment',
            method=payment_method,
        )

        await context.bot.send_photo(
            ADMIN_ID,
            file_id,
            caption=(
                f"📌 Registration payment screenshot from @{update.effective_user.username or 'Unknown'} "
                f"(chat_id: {chat_id})\nPackage: {package}\nAmount: ₦{total_amount}\nPayment ID: {payment_id}"
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

    # Handle admin text commands
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


async def reveal_payment_confirmation_button(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, flutterwave_link: str):
    """After 10 seconds, edit message to reveal 'I have made my Payment' button"""
    await asyncio.sleep(10)
    
    # Create buttons: URL button + confirmation + go back
    buttons = [
        [InlineKeyboardButton("💳 Click Here To Proceed", url=flutterwave_link)],
        [InlineKeyboardButton("✅ I Have Made My Payment", callback_data="reg_flutterwave_paid")],
        [InlineKeyboardButton("🔙 Go Back", callback_data="reg_bank")],
    ]
    
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        logging.error(f"Failed to update payment buttons for user {chat_id}: {e}")


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
        buttons = []
        user = get_user(chat_id)
        is_new_user = not user or user.get('payment_status') != 'registered'
        
        # Display only active packages
        for pkg_id, pkg_data in PACKAGES.items():
            if not pkg_data.get('is_active', False):
                continue  # Skip inactive packages
            display_text = f"{pkg_data['emoji']} {pkg_data['display_name']} (₦{pkg_data['price_naira']})"
            buttons.append([InlineKeyboardButton(display_text, callback_data=f"reg_{pkg_id}")])
        
        buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu")])
        await query.edit_message_text("💎 Choose your package:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    # === SPECIFIC PAYMENT METHOD CALLBACKS (must check BEFORE generic "reg_" check) ===

    if data == "reg_bank":
        state = user_state.setdefault(chat_id, {})
        state['expecting'] = 'reg_screenshot'
        state['payment_method'] = 'bank'
        buttons = [[InlineKeyboardButton(name, callback_data=f"reg_account_{name}")] for name in PAYMENT_ACCOUNTS.keys()]
        buttons.append([InlineKeyboardButton("Other country option", callback_data="reg_other")])
        buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu")])
        await query.edit_message_text("Select a bank account to pay to:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data == "reg_flutterwave_selection":
        state = user_state.setdefault(chat_id, {})
        state['payment_method'] = 'flutterwave'
        state['selected_account'] = 'flutterwave'
        state['expecting'] = 'reg_screenshot'
        
        # Get flutterwave link from stored package info
        flutterwave_link = state.get('flutterwave_link', 'https://flutterwave.com/pay/exuv4kvor1cn')
        
        # Initial buttons: Show only "Click Here To Proceed" and "Go Back"
        buttons = [
            [InlineKeyboardButton("💳 Click Here To Proceed", url=flutterwave_link)],
            [InlineKeyboardButton("🔙 Go Back", callback_data="reg_bank")],
        ]
        
        payment_msg = f"💰 Complete payment of ₦{state.get('amount_naira', 'N/A')} (€{state.get('amount_euro', 'N/A')}) via Flutterwave.\n\n"
        payment_msg += "🔗 Click the button below to open the payment portal\n"
        payment_msg += "💳 Complete your payment on the Flutterwave page\n"
        payment_msg += "✅ Once done, return here and confirm your payment\n\n"
        payment_msg += "⏳ A confirmation button will appear shortly..."
        
        sent_msg = await query.edit_message_text(payment_msg, reply_markup=InlineKeyboardMarkup(buttons))
        message_id = sent_msg.message_id
        
        # Schedule the button reveal for 5 seconds later
        asyncio.create_task(reveal_payment_confirmation_button(context, chat_id, message_id, flutterwave_link))
        return

    if data == "reg_flutterwave_paid":
        state = user_state.setdefault(chat_id, {})
        state['expecting'] = 'reg_screenshot'
        
        # User confirmed payment, now request screenshot
        await query.edit_message_text(
            "📸 Please upload a screenshot of your payment confirmation.\n\n"
            "Make sure the payment details are clearly visible.",
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
        state['selected_account_details'] = payment_details
        state['payment_method'] = 'bank'
        state['expecting'] = 'reg_screenshot'
        
        await query.edit_message_text(
            f"📋 *Payment Details*\n\n{payment_details}\n\n"
            f"💎 Amount: ₦{state.get('amount_naira', 'N/A')}\n\n"
            "Please send a screenshot of your payment proof after transferring.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]),
        )
        return

    if data == "reg_other":
        await query.edit_message_text(
            "Please contact @bigscottmedia to complete your payment for other regions.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]),
        )
        return

    # === GENERIC PACKAGE SELECTION (must be AFTER specific payment method checks) ===

    if data.startswith("reg_"):
        package_id = data[4:]  # Remove "reg_" prefix
        
        # Get package from hardcoded PACKAGES dictionary
        if package_id not in PACKAGES:
            await query.edit_message_text("Invalid package selected. Please try again.", 
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]))
            return
        
        package = PACKAGES[package_id]
        user = get_user(chat_id)
        is_upgrade = user and user.get("payment_status") == 'registered'
        
        # Determine correct Flutterwave link based on context
        if is_upgrade and package.get('is_premium'):
            # User upgrading to premium
            flutterwave_link = FLUTTERWAVE_UPGRADE
        elif package.get('is_premium'):
            # New user buying premium (only if premium is active)
            flutterwave_link = FLUTTERWAVE_PREMIUM_NEW_USER
        else:
            # New user buying basic package
            flutterwave_link = FLUTTERWAVE_BASIC_NEW_USER
        
        # Store package info in user_state
        user_state[chat_id] = {
            'package_id': package_id,
            'package': package_id,
            'package_name': package['display_name'],
            'is_upgrade': is_upgrade,
            'flutterwave_link': flutterwave_link,
            'amount_naira': package['price_naira'],
            'amount_euro': package['price_euro'],
        }
        
        # Show payment method selection: only two options (removed "I paid with Flutterwave")
        buttons = [
            [InlineKeyboardButton("Pay with Flutterwave (Fast)", callback_data="reg_flutterwave_selection")],
            [InlineKeyboardButton("Pay with Bank Account", callback_data="reg_bank")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu")],
        ]
        
        payment_text = f"You selected: {package['emoji']} {package['display_name']}\n"
        payment_text += f"Price: ₦{package['price_naira']} (€{package['price_euro']})\n\n"
        payment_text += "Choose your payment method:"
        
        await query.edit_message_text(payment_text, reply_markup=InlineKeyboardMarkup(buttons))
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
        keyboard = [
            [InlineKeyboardButton("💎CLICK TO PROCEED!", callback_data="package_selector")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]
        ]
        await query.edit_message_text(
            "💎 *GLAMFEE PACKAGE*\n\n"
            "Investment: *₦14,000* (*€7*) ✨\n"
            "• Instant access to GlamFee earning platform\n"
            "• Direct earning opportunities: €2/hour multiple streams\n"
            "• Network commission: 1st Indirect ₦400, 2nd Indirect ₦100\n"
            "• Daily earning potential: €12+/hour\n"
            "• Fast ROI with consistent daily income\n"
            "• Access to all earning channels\n\n"
            "💰 *WHY CHOOSE GLAMOUR?*\n"
            "✅ Multiple daily income streams (up to €12+/hour)\n"
            "✅ Quick investment recovery & scaling earnings\n"
            "✅ Global earning potential with flexible work hours\n"
            "✅ Easy access with network-driven expansion\n"
            "✅ Transparent payment system in EUR & NGN\n"
            "✅ Consistent daily income flow\n\n"
            "🔥 *Your Soft Life begins with GLAMOUR - Choose Your Package Now!* 🔥",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        # Send voice note
        voice_keyboard = [
            [InlineKeyboardButton("✅ I'm done listening...", callback_data="close_voice")]
        ]
        voice_markup = InlineKeyboardMarkup(voice_keyboard)
        try:
            import os
            voice_path = os.path.join(os.path.dirname(__file__), "voice.ogg")
            with open(voice_path, "rb") as voice:
                await context.bot.send_voice(
                    chat_id=query.message.chat_id,
                    voice=voice,
                    caption="Glamour Explained 🎧",
                    reply_markup=voice_markup
                )
        except FileNotFoundError:
            logger.error("Voice file 'voice.ogg' not found")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Error: Voice note file not found. Please contact support.",
                reply_markup=voice_markup
            )
        except Exception as e:
            logger.error(f"Error sending voice note: {e}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="An error occurred while sending the voice note. Please try again.",
                reply_markup=voice_markup
            )
        return

    if data == "close_voice":
        try:
            await query.message.delete()
        except Exception as e:
            logger.error(f"Error deleting voice message: {e}")
            await query.answer("Message deleted or already removed.")
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


# ==================== ADMIN PACKAGE MANAGEMENT COMMANDS ====================

async def admin_activate_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to activate premium package"""
    chat_id = update.effective_user.id
    if chat_id != ADMIN_ID:
        await update.message.reply_text("❌ This command is admin only.")
        return
    
    # Activate premium package
    PACKAGES['glampremium']['is_active'] = True
    
    await update.message.reply_text(
        "✅ Premium package (GlamPremium) has been activated!\n\n"
        "New users will now see the option to purchase GlamPremium.\n"
        "Registered users can upgrade to GlamPremium.\n\n"
        "Flutterwave Payment Links:\n"
        f"• Basic (New): {FLUTTERWAVE_BASIC_NEW_USER}\n"
        f"• Premium (New): {FLUTTERWAVE_PREMIUM_NEW_USER}\n"
        f"• Upgrade: {FLUTTERWAVE_UPGRADE}"
    )
    log_action(chat_id, "premium_activated")


async def admin_deactivate_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to deactivate premium package"""
    chat_id = update.effective_user.id
    if chat_id != ADMIN_ID:
        await update.message.reply_text("❌ This command is admin only.")
        return
    
    # Deactivate premium package
    PACKAGES['glampremium']['is_active'] = False
    
    await update.message.reply_text(
        "✅ Premium package (GlamPremium) has been deactivated!\n\n"
        "New users will only see GlamFee option.\n"
        "Registered users cannot upgrade to GlamPremium.\n\n"
        "Only GlamFee is now available for purchase."
    )
    log_action(chat_id, "premium_deactivated")


# ==================== BOT INITIALIZATION AND RUN ====================

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
    
    # Premium package management commands
    application.add_handler(CommandHandler("activate_premium", admin_activate_premium))
    application.add_handler(CommandHandler("deactivate_premium", admin_deactivate_premium))

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
