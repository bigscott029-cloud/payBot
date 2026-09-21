#!/usr/bin/env python3
# main.py — Evermore AI Main Bot for Telegram (modular version)

import logging
import os
import datetime
import asyncio
import hmac
import csv
import io
from threading import Thread
from flask import Flask, request, jsonify
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
    FAQS,
    HELP_TOPICS,
    WEBAPP_URL,
    GROUP_LINK,
    DAILY_TASK_LINK,
    SITE_LINK,
    AI_BOOST_LINK,
    FLUTTERWAVE_WEBHOOK_HASH,
    EVERAI_TRIAL_PRICE,
    EVERAI_PREMIUM_PRICE,
    EVERAI_PREMIUM_REGULAR_PRICE,
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
    get_setting,
    set_setting,
)
from payments import (create_payment, get_payment, approve_payment, reject_payment,
                      initialize_flutterwave_payment, verify_flutterwave_payment,
                      add_access_code, fulfill_waiting_codes, allocate_access_code,
                      get_code_stock, revoke_access_code, payment_export_rows)
from utils import (
    validate_email,
    validate_phone,
    validate_username,
    sanitize_input,
    generate_referral_code,
    command_limiter,
    withdrawal_limiter,
    log_action,
    get_available_media_files,
    verify_task_with_gemini,
    ask_evermore_ai,
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
    admin_set_explainer,
)
from error_handlers import error_handler, handle_invalid_command

# ==================== HARDCODED PACKAGE DEFINITIONS ====================
# Prices match the active Optinex EverAI plans.
PACKAGES = {
    'trial': {
        'id': 'trial',
        'display_name': 'EverAI Trial',
        'emoji': '💎',
        'price_naira': EVERAI_TRIAL_PRICE,
        'is_premium': False,
        'is_active': True,
    },
    'premium': {
        'id': 'premium',
        'display_name': 'EverAI Premium',
        'emoji': '👑',
        'price_naira': EVERAI_PREMIUM_PRICE,
        'original_price_naira': EVERAI_PREMIUM_REGULAR_PRICE,
        'is_premium': True,
        'is_active': True,
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
bot_loop = None
telegram_polling_ready = False
telegram_bot_username = None


@app.route('/')
def home():
    return "Evermore AI is alive!"


@app.route('/health')
def health():
    """Render/operations health check; does not expose credentials."""
    return jsonify({
        "web": "ok",
        "telegram_polling": telegram_polling_ready,
        "bot_username": telegram_bot_username,
    }), 200 if telegram_polling_ready else 503


@app.route('/flutterwave/callback')
def flutterwave_callback():
    """Flutterwave redirects here. Payment is verified server-to-server."""
    tx_ref = request.args.get('tx_ref', '')
    if not tx_ref:
        return "Payment reference missing. Return to Telegram and try again.", 400
    try:
        payment, status = verify_flutterwave_payment(tx_ref)
        if payment and status == 'pending_code':
            allocation = allocate_access_code(payment['id'])
            if allocation:
                code, paid_payment = allocation
                if application and bot_loop:
                    asyncio.run_coroutine_threadsafe(deliver_code(paid_payment['chat_id'], code), bot_loop)
                return "Payment confirmed. Your verified access code has been sent in Telegram."
            return "Payment confirmed. Your access code is pending stock and will be delivered in Telegram automatically."
        return "Payment is not yet confirmed. Return to Telegram and use Check payment status."
    except Exception as exc:
        logger.exception("Flutterwave callback error")
        return "We could not verify this payment yet. Return to Telegram and try Check payment status.", 502


@app.route('/flutterwave/webhook', methods=['POST'])
def flutterwave_webhook():
    """Optional automatic fulfillment endpoint; configure this URL in Flutterwave."""
    supplied_hash = request.headers.get('verif-hash', '')
    if not FLUTTERWAVE_WEBHOOK_HASH or not hmac.compare_digest(supplied_hash, FLUTTERWAVE_WEBHOOK_HASH):
        return "Unauthorized", 401
    payload = request.get_json(silent=True) or {}
    transaction = payload.get('data') or {}
    tx_ref = transaction.get('tx_ref', '')
    if not tx_ref:
        return "OK", 200
    try:
        payment, status = verify_flutterwave_payment(tx_ref)
        if payment and status == 'pending_code':
            allocation = allocate_access_code(payment['id'])
            if allocation and application and bot_loop:
                code, paid_payment = allocation
                asyncio.run_coroutine_threadsafe(deliver_code(paid_payment['chat_id'], code), bot_loop)
        return "OK", 200
    except Exception:
        logger.exception("Flutterwave webhook error")
        return "Retry", 500


# ==================== TELEGRAM MINI-APP REST API ENDPOINTS ====================

@app.route('/api/user/stats', methods=['GET'])
def api_user_stats():
    """Returns live user stats for Telegram WebApp"""
    chat_id = request.args.get('chat_id', type=int)
    if not chat_id:
        return jsonify({"success": False, "error": "chat_id parameter required"}), 400
    user = get_user(chat_id)
    if not user:
        return jsonify({"success": False, "error": "User not found"}), 404
    return jsonify({"success": True, "user": dict(user)})


@app.route('/api/tasks', methods=['GET'])
def api_tasks():
    """Returns available daily tasks and completion status for Telegram WebApp"""
    chat_id = request.args.get('chat_id', type=int)
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE expires_at > CURRENT_TIMESTAMP OR expires_at IS NULL")
        tasks = cursor.fetchall()
        completed_ids = []
        if chat_id:
            cursor.execute("SELECT task_id FROM user_tasks WHERE user_id=%s", (chat_id,))
            completed_ids = [r['task_id'] for r in cursor.fetchall()]
        return jsonify({"success": True, "tasks": tasks, "completed_task_ids": completed_ids})
    except Exception as e:
        logger.error(f"API tasks error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        return_conn(conn)


@app.route('/api/tasks/complete', methods=['POST'])
def api_complete_task():
    """Processes task completion with Gemini AI verification for Telegram WebApp"""
    data = request.get_json(silent=True) or {}
    chat_id = data.get('chat_id')
    task_id = data.get('task_id')
    submission = data.get('submission', '')

    if not chat_id or not task_id:
        return jsonify({"success": False, "error": "chat_id and task_id required"}), 400

    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id=%s", (task_id,))
        task = cursor.fetchone()
        if not task:
            return jsonify({"success": False, "error": "Task not found"}), 404

        is_valid, msg = verify_task_with_gemini(task.get('type', 'general'), task.get('link', ''), submission)
        if not is_valid:
            return jsonify({"success": False, "error": msg}), 400

        cursor.execute("INSERT INTO user_tasks (user_id, task_id, completed_at) VALUES (%s, %s, CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING", (chat_id, task_id))
        reward = task.get('reward', 0.0) or 0.0
        if reward > 0:
            cursor.execute("UPDATE users SET balance = balance + %s WHERE chat_id=%s", (reward, chat_id))

        return jsonify({"success": True, "message": msg, "reward": reward})
    except Exception as e:
        logger.error(f"API complete task error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        return_conn(conn)


@app.route('/api/withdraw', methods=['POST'])
def api_withdraw():
    """Handles balance withdrawal request for Telegram WebApp"""
    data = request.get_json(silent=True) or {}
    chat_id = data.get('chat_id')
    amount = data.get('amount', 0.0)

    if not chat_id or amount <= 0:
        return jsonify({"success": False, "error": "Valid chat_id and positive amount required"}), 400

    user = get_user(chat_id)
    if not user or user.get('balance', 0) < amount:
        return jsonify({"success": False, "error": "Insufficient balance"}), 400

    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET balance = balance - %s WHERE chat_id=%s", (amount, chat_id))
        log_action(chat_id, "withdrawal_requested", details=f"amount={amount}")
        return jsonify({"success": True, "message": "Withdrawal request submitted successfully."})
    except Exception as e:
        logger.error(f"API withdraw error: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        return_conn(conn)


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
last_stock_alert = None

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def deliver_code(chat_id, code):
    await application.bot.send_message(
        chat_id,
        f"✅ Payment confirmed\n\nYour verified EverAI access code is:\n`{code}`\n\nKeep it private and use it only on the official platform.",
        parse_mode='Markdown',
    )


async def add_code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_ID:
        await update.message.reply_text("This command is restricted to the admin.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /add_code <code> [trial|premium] [valid_days]")
        return
    code, plan = context.args[0], (context.args[1].lower() if len(context.args) > 1 else None)
    if plan and plan not in PACKAGES:
        await update.message.reply_text("Plan must be trial or premium.")
        return
    try:
        valid_days = int(context.args[2]) if len(context.args) > 2 else None
        if valid_days is not None and valid_days < 1:
            raise ValueError("valid_days must be at least 1")
        expires_at = datetime.datetime.now() + datetime.timedelta(days=valid_days) if valid_days else None
        add_access_code(code, plan, expires_at)
        fulfilled = fulfill_waiting_codes()
        for _, (issued_code, payment) in fulfilled:
            await deliver_code(payment['chat_id'], issued_code)
        await update.message.reply_text(f"Code added. Delivered {len(fulfilled)} waiting code(s).")
    except Exception as exc:
        logger.exception("Could not add access code")
        await update.message.reply_text(f"Could not add code: {exc}")


async def code_stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_ID:
        return
    stock, waiting = get_code_stock()
    stock_text = "\n".join(f"• {row['plan']}: {row['status']} — {row['count']}" for row in stock) or "No codes in inventory."
    waiting_text = "\n".join(f"• {row['package']}: {row['count']} waiting" for row in waiting) or "No customers waiting."
    await update.message.reply_text(f"ACCESS-CODE STOCK\n\n{stock_text}\n\nPENDING DELIVERY\n{waiting_text}")


async def revoke_code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /revoke_code <code> [reason]")
        return
    result = revoke_access_code(context.args[0], " ".join(context.args[1:]) or "Revoked by admin")
    await update.message.reply_text("Code revoked." if result else "Code was not found or was already revoked.")


async def export_payments_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_ID:
        return
    rows = payment_export_rows()
    output = io.StringIO()
    fields = ['id', 'chat_id', 'package', 'total_amount', 'method', 'status', 'tx_ref', 'timestamp', 'approved_at']
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    document = io.BytesIO(output.getvalue().encode())
    document.name = f"everai-payments-{datetime.date.today().isoformat()}.csv"
    await update.message.reply_document(document=document, caption=f"Payment export: {len(rows)} record(s).")


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
        "💙♾️↗️ WELCOME TO EVERMORE / EVERAI!\n\n"
        "🤖 Africa’s generative AI training and opportunity platform.\n\n"
        "✨ Learn how EverAI works, explore available AI training opportunities, "
        "and get your verified access plan code in just a few taps.\n\n"
        "🚀 Ready to get started?\n\n"
        "Tap the button below, then choose How Evermore Works or Buy Verified Access Plans Code.",
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
        [InlineKeyboardButton("💸 Get Registered", callback_data="package_selector")],
        [InlineKeyboardButton("Buy Verified Access Plans Code", callback_data="package_selector")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]

    if user and user["payment_status"] == 'registered':
        buttons = [
            [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
            [InlineKeyboardButton("Do Daily Tasks", callback_data="daily_tasks")],
            [InlineKeyboardButton("💰 Earn Extra for the Day", callback_data="earn_extra")],
            [InlineKeyboardButton("Buy Verified Access Plans Code", callback_data="package_selector")],
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


async def _handle_payment_proof_upload(update: Update, context: ContextTypes.DEFAULT_TYPE, file_id: str, file_kind: str):
    chat_id = update.effective_chat.id
    state = user_state.get(chat_id, {})
    if state.get('expecting') != 'reg_screenshot':
        return

    package = state.get('package')
    account = state.get('selected_account')
    payment_method = state.get('payment_method', 'manual')
    
    if not package or not account:
        await update.message.reply_text("Please choose a package and payment account before sending your screenshot.")
        return

    total_amount = state.get('amount_naira')
    
    try:
        payment_id = create_payment(
            chat_id=chat_id,
            payment_type='coupon',
            package=package,
            quantity=1,
            total_amount=total_amount,
            payment_account=account,
            is_upgrade=False,
            status='pending_payment',
            method=payment_method,
        )

        caption = (
            f"📌 Registration payment proof from @{update.effective_user.username or 'Unknown'} "
            f"(chat_id: {chat_id})\nPlan: {package}\nAmount: ₦{total_amount}\nPayment ID: {payment_id}"
        )
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("Approve", callback_data=f"approve_payment_{payment_id}")],
            [InlineKeyboardButton("Reject", callback_data=f"reject_payment_{payment_id}")],
        ])

        if file_kind == "document":
            await context.bot.send_document(
                ADMIN_ID,
                document=file_id,
                caption=caption,
                reply_markup=reply_markup,
            )
        else:
            await context.bot.send_photo(
                ADMIN_ID,
                photo=file_id,
                caption=caption,
                reply_markup=reply_markup,
            )
        await update.message.reply_text(
            "Transfer proof received. Once approved, your verified access code will be delivered here."
        )
        user_state[chat_id]['expecting'] = None
        user_state[chat_id]['payment_id'] = payment_id
    except Exception:
        logger.exception("Error saving payment proof upload")
        await update.message.reply_text("An error occurred while uploading the screenshot. Please try again.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = user_state.get(chat_id, {})
    if state.get('expecting') != 'reg_screenshot':
        return

    if not update.message.photo:
        await update.message.reply_text("Please send a clear payment screenshot image.")
        return

    await _handle_payment_proof_upload(update, context, update.message.photo[-1].file_id, "photo")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = user_state.get(chat_id, {})
    if state.get('expecting') != 'reg_screenshot':
        return

    document = update.message.document
    mime_type = document.mime_type or ""
    if not mime_type.startswith('image/'):
        await update.message.reply_text("Please send an image file such as PNG or JPG.")
        return

    await _handle_payment_proof_upload(update, context, document.file_id, "document")


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

    if state.get('expecting') == 'flutterwave_email':
        if not validate_email(text):
            await update.message.reply_text("Please send a valid email address for your Flutterwave receipt.")
            return
        try:
            payment_id, tx_ref, link = initialize_flutterwave_payment(
                chat_id, state['package'], state['amount_naira'], text,
                update.effective_user.full_name or "Telegram member",
            )
            state.update({'expecting': None, 'payment_id': payment_id, 'tx_ref': tx_ref})
            await update.message.reply_text(
                f"💳 Secure checkout created for ₦{state['amount_naira']:,}.\n\n"
                "Tap the Flutterwave button below. It opens the secure Flutterwave checkout in Telegram’s in-app browser on supported clients. "
                "After payment, return here and tap Check payment status.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Pay with Flutterwave", url=link)],
                    [InlineKeyboardButton("Check payment status", callback_data="check_flutterwave")],
                    [InlineKeyboardButton("🔙 Main Menu", callback_data="menu")],
                ]),
            )
        except Exception as exc:
            logger.exception("Could not create Flutterwave checkout")
            state['expecting'] = None
            await update.message.reply_text(
                "⚠️ Flutterwave checkout is temporarily unavailable. This is usually a payment-gateway configuration issue, not a problem with the EverAI website.\n\n"
                "Please try again, pay through a verified agent, or return to the menu.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Flutterwave Again", callback_data="reg_flutterwave_selection")],
                    [InlineKeyboardButton("🏦 Pay To A Verified Agent", callback_data="reg_bank")],
                    [InlineKeyboardButton("🔙 Main Menu", callback_data="menu")],
                ]),
            )
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
    """After 20 seconds, edit message to reveal 'I have made my Payment' button"""
    await asyncio.sleep(20)
    
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
        buttons = [[InlineKeyboardButton("Pay To A Verified Agent", callback_data=f"reg_account_{name}")] for name in PAYMENT_ACCOUNTS]
        buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu")])
        await query.edit_message_text("Select the verified agent payment option below:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data == "reg_flutterwave_selection":
        state = user_state.setdefault(chat_id, {})
        state['expecting'] = 'flutterwave_email'
        await query.edit_message_text(
            "📧 Send the email address Flutterwave should use for your receipt.\n\n"
            "You can return to the menu at any time.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏦 Pay To A Verified Agent Instead", callback_data="reg_bank")],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="menu")],
            ]),
        )
        return

    if data == "reg_flutterwave_confirm":
        state = user_state.setdefault(chat_id, {})
        flutterwave_link = state.get('flutterwave_link', 'https://flutterwave.com/pay/exuv4kvor1cn')
        
        # Show payment link - timer starts NOW (only after user confirms)
        buttons = [
            [InlineKeyboardButton("💳 Click Here To Proceed", url=flutterwave_link)],
            [InlineKeyboardButton("🔙 Go Back", callback_data="reg_bank")],
        ]
        
        payment_msg = f"💰 Complete payment of ₦{state.get('amount_naira', 'N/A')} (€{state.get('amount_euro', 'N/A')}) via Flutterwave.\n\n"
        payment_msg += "🔗 Click the button below to open the payment portal\n"
        payment_msg += "💳 Complete your payment on the Flutterwave page\n\n"
        payment_msg += "⏳ After clicking, a confirmation button will appear in 20 seconds..."
        
        sent_msg = await query.edit_message_text(payment_msg, reply_markup=InlineKeyboardMarkup(buttons))
        message_id = sent_msg.message_id
        
        # Start the 20-second timer ONLY NOW (after user clicked confirm)
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

    if data == "check_flutterwave":
        state = user_state.get(chat_id, {})
        tx_ref = state.get('tx_ref')
        if not tx_ref:
            await query.edit_message_text("No active Flutterwave payment was found. Choose a plan to begin again.")
            return
        try:
            payment, status = verify_flutterwave_payment(tx_ref)
            if status == 'pending_code':
                allocation = allocate_access_code(payment['id'])
                if allocation:
                    code, _ = allocation
                    await query.edit_message_text(f"✅ Payment confirmed. Your verified access code is:\n`{code}`", parse_mode='Markdown')
                else:
                    await query.edit_message_text("✅ Payment confirmed. No code is currently available; your code is pending and will be delivered automatically when stock is added.")
            else:
                await query.edit_message_text("Your payment is not confirmed yet. Complete checkout, wait a moment, then check again.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Check payment status", callback_data="check_flutterwave")]]))
        except Exception:
            logger.exception("Flutterwave status check failed")
            await query.edit_message_text("We could not check Flutterwave right now. Please try again shortly.")
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
            f"📋 *Verified Agent Payment Details*\n\n{payment_details}\n\n"
            f"💎 Amount: ₦{state.get('amount_naira', 'N/A')}\n\n"
            "Please send a screenshot of your payment proof after transferring.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]),
        )
        return

    if data == "reg_other":
        await query.edit_message_text(
            "Please contact @everaiafrica to complete your payment for other regions.",
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
        
        # Store package info in user_state
        user_state[chat_id] = {
            'package_id': package_id,
            'package': package_id,
            'package_name': package['display_name'],
            'is_upgrade': is_upgrade,
            'amount_naira': package['price_naira'],
        }
        
        # Show payment method selection: only two options (removed "I paid with Flutterwave")
        buttons = [
            [InlineKeyboardButton("Pay with Flutterwave (Fast)", callback_data="reg_flutterwave_selection")],
            [InlineKeyboardButton("Pay To A Verified Agent", callback_data="reg_bank")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu")],
        ]
        
        payment_text = f"You selected: {package['emoji']} {package['display_name']}\n"
        payment_text += f"Price: ₦{package['price_naira']:,}\n\n"
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
            approved_payment, _ = approve_payment(payment_id)
            if not approved_payment or approved_payment['status'] != 'approved':
                await query.edit_message_text(f"Payment {payment_id} was already processed.")
                return
            user_chat_id = payment['chat_id']
            conn = get_conn()
            try:
                cursor = conn.cursor()
                allocation = allocate_access_code(payment_id)
                if allocation:
                    code, _ = allocation
                    await context.bot.send_message(user_chat_id, f"✅ Payment approved. Your verified access code is: `{code}`", parse_mode='Markdown')
                else:
                    cursor.execute("UPDATE payments SET status='pending_code' WHERE id=%s", (payment_id,))
                    await context.bot.send_message(user_chat_id, "✅ Payment approved. Your code is pending stock and will be delivered automatically.")
            finally:
                return_conn(conn)
            await query.edit_message_text(f"Payment {payment_id} approved.")
            return

        if data.startswith("reject_payment_"):
            reject_payment(payment_id)
            user_chat_id = payment['chat_id']
            await context.bot.send_message(
                user_chat_id,
                "❌ Your payment has been rejected by the admin. Please review the instructions and try again or contact @everaiafrica."
            )
            await query.edit_message_text(f"Payment {payment_id} rejected.")
            return

    if data == "coupon":
        await query.edit_message_text(
            "Verified access purchases are currently managed by the admin. Please contact @everaiafrica.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]),
        )
        return

    if data == "how_it_works":
        keyboard = [
            [InlineKeyboardButton("💎CLICK TO PROCEED!", callback_data="package_selector")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]
        ]
        await query.edit_message_text(
            "✔️ HOW EVERMORE 💙♾️↗️ WORKS\n\n"
            "Evermore is the parent brand of EVERAI,\n"
            "Africa’s first generative AI assistant.\n\n"
            "EverAI is built in partnership with leading AI platforms like ChatGPT, Claude, Gemini, and others. Before launch, EverAI needs trainers to teach it human interactions, correct its memory, and rate its responses.\n\n"
            "💰 EARN UP TO:\n\n"
            "✅ $16.2/hour — Rate EverAI responses as Good or Bad\n\n"
            "✅ $18.6/hour — Answer simple questions to correct EverAI’s memory\n\n"
            "✅ $17.2/hour — Complete opinion/survey tasks with no right or wrong answers\n"
            "Example: “Ronaldo is better than Messi.”\n\n"
            "🌐 EVERAI also scans the internet thousands of times daily and alerts subscribers to available remote jobs.\n\n"
            "💼 Remote opportunities can pay up to $19.2/hour, including:\n\n"
            "• Audio Transcription (type out short recordings) — up to $12/hour\n"
            "• AI Content Rating — up to $14.6/hour\n"
            "• Click n Earn — up to $12.3/hour\n"
            "• Survey & Opinion Tasks (provide feedback on products or AI responses) — up to $18.6/hour\n\n"
            "🚀 Recruitment is currently ongoing. Earned rewards are paid three times a week.\n\n"
            "✨ Tap below to choose your verified access plan.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        # Send explainer media (DB-configured Telegram file_id, or local disk fallback)
        explainer_id = get_setting('explainer_file_id')
        explainer_type = get_setting('explainer_file_type')

        sent_any = False

        if explainer_id and explainer_type:
            try:
                if explainer_type == 'video':
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=explainer_id,
                        caption="Evermore AI Explained 🎬",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ I'm done watching...", callback_data="close_media")]])
                    )
                elif explainer_type == 'voice':
                    await context.bot.send_voice(
                        chat_id=query.message.chat_id,
                        voice=explainer_id,
                        caption="Evermore AI Explained 🎧",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ I'm done listening...", callback_data="close_media")]])
                    )
                else:
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=explainer_id,
                        caption="Evermore AI Explained 🎧",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ I'm done listening...", callback_data="close_media")]])
                    )
                sent_any = True
            except Exception as e:
                logger.error(f"Error sending DB explainer media ({explainer_id}): {e}")

        if not sent_any:
            base_dir = os.path.dirname(__file__)
            videos, audios = get_available_media_files(base_dir)

            if videos:
                close_video_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ I'm done watching...", callback_data="close_media")]
                ])
                for vpath in videos:
                    try:
                        with open(vpath, "rb") as vid:
                            await context.bot.send_video(
                                chat_id=query.message.chat_id,
                                video=vid,
                                caption="Evermore AI Explained 🎬",
                                reply_markup=close_video_markup
                            )
                        sent_any = True
                    except Exception as e:
                        logger.error(f"Error sending video '{vpath}': {e}")

            if audios:
                close_voice_markup = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ I'm done listening...", callback_data="close_media")]
                ])
                for apath in audios:
                    try:
                        with open(apath, "rb") as aud:
                            if apath.lower().endswith('.ogg'):
                                await context.bot.send_voice(
                                    chat_id=query.message.chat_id,
                                    voice=aud,
                                    caption="Evermore AI Explained 🎧",
                                    reply_markup=close_voice_markup
                                )
                            else:
                                await context.bot.send_audio(
                                    chat_id=query.message.chat_id,
                                    audio=aud,
                                    caption="Evermore AI Explained 🎧",
                                    reply_markup=close_voice_markup
                                )
                        sent_any = True
                    except Exception as e:
                        logger.error(f"Error sending audio '{apath}': {e}")

        if not sent_any:
            logger.error("No media files (video or audio) found")
            fallback_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Close", callback_data="close_media")]
            ])
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Error: Media explanation file not found. Please contact support.",
                reply_markup=fallback_markup
            )
        return

    if data in ("close_voice", "close_video", "close_media"):
        try:
            await query.message.delete()
        except Exception as e:
            logger.error(f"Error deleting media message: {e}")
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
                await context.bot.send_message(row['chat_id'], "🌟 Daily Reminder: Complete your Evermore AI tasks today!")
            except Exception as exc:
                logger.error(f"Failed sending reminder to {row['chat_id']}: {exc}")
    except Exception as exc:
        logger.error(f"Error in daily_reminder job: {exc}")
    finally:
        return_conn(conn)


async def low_stock_alert(context: ContextTypes.DEFAULT_TYPE):
    """Notify the admin at most once per day when paid customers cannot receive codes."""
    global last_stock_alert
    stock, waiting = get_code_stock()
    available = sum(row['count'] for row in stock if row['status'] == 'available')
    waiting_total = sum(row['count'] for row in waiting)
    today = datetime.date.today()
    if waiting_total and available == 0 and last_stock_alert != today:
        await context.bot.send_message(ADMIN_ID, f"⚠️ Code stock alert: {waiting_total} paid customer(s) are waiting and no access codes are available. Use /add_code <code> [trial|premium].")
        last_stock_alert = today


# ==================== ADMIN PACKAGE MANAGEMENT COMMANDS ====================

async def admin_activate_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to activate premium package"""
    chat_id = update.effective_user.id
    if chat_id != ADMIN_ID:
        await update.message.reply_text("❌ This command is admin only.")
        return
    
    # Activate premium package
    PACKAGES['premium']['is_active'] = True
    
    await update.message.reply_text(
        "✅ Premium package (Evermore AI Premium) has been activated!\n\n"
        "New users can now buy an EverAI Premium verified access code."
    )
    log_action(chat_id, "premium_activated")


async def admin_deactivate_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to deactivate premium package"""
    chat_id = update.effective_user.id
    if chat_id != ADMIN_ID:
        await update.message.reply_text("❌ This command is admin only.")
        return
    
    # Deactivate premium package
    PACKAGES['premium']['is_active'] = False
    
    await update.message.reply_text(
        "✅ Premium package (Evermore AI Premium) has been deactivated!\n\n"
        "New users will only see the EverAI Trial plan."
    )
    log_action(chat_id, "premium_deactivated")


async def handle_media_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin explainer media uploads (video, voice, audio)"""
    chat_id = update.effective_chat.id
    state = user_state.get(chat_id, {})
    if state.get('expecting') == 'explainer_media' and chat_id == ADMIN_ID:
        msg = update.message
        file_id = None
        file_type = None

        if msg.video:
            file_id = msg.video.file_id
            file_type = 'video'
        elif msg.voice:
            file_id = msg.voice.file_id
            file_type = 'voice'
        elif msg.audio:
            file_id = msg.audio.file_id
            file_type = 'audio'
        elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith('video/'):
            file_id = msg.document.file_id
            file_type = 'video'

        if file_id and file_type:
            set_setting('explainer_file_id', file_id)
            set_setting('explainer_file_type', file_type)
            state['expecting'] = None
            await update.message.reply_text(
                f"✅ Explainer media updated successfully!\n\nType: *{file_type.capitalize()}*\nFile ID: `{file_id}`",
                parse_mode='Markdown'
            )
            return

        await update.message.reply_text("Please send a valid video, voice note, or audio file.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Smart /help command: Admin gets admin command list, users get standard help menu"""
    chat_id = update.effective_chat.id
    if chat_id == ADMIN_ID:
        await admin_help(update, context)
    else:
        await help_menu(update, context)


# ==================== BOT INITIALIZATION AND RUN ====================

async def run_bot():
    """Run Telegram on an explicitly managed event loop (required by Python 3.14)."""
    global application, bot_loop, telegram_polling_ready, telegram_bot_username
    bot_loop = asyncio.get_running_loop()

    application = Application.builder().token(BOT_TOKEN).build()

    # === HANDLERS ===
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("menu", show_main_menu))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("support", support))
    application.add_handler(CommandHandler("add_task", add_task))
    application.add_handler(CommandHandler("add_code", add_code_command))
    application.add_handler(CommandHandler("code_stock", code_stock_command))
    application.add_handler(CommandHandler("revoke_code", revoke_code_command))
    application.add_handler(CommandHandler("export_payments", export_payments_command))
    application.add_handler(CommandHandler("broadcast", admin_broadcast))
    application.add_handler(CommandHandler("analytics", admin_analytics))
    application.add_handler(CommandHandler("stats_package", admin_stats_by_package))
    application.add_handler(CommandHandler("payment_approve", admin_manual_payment_approval))
    application.add_handler(CommandHandler("approve_payment", admin_approve_payment))
    application.add_handler(CommandHandler("reject_payment", admin_reject_payment))
    application.add_handler(CommandHandler("payments_pending", admin_pending_payments))
    application.add_handler(CommandHandler("set_explainer", admin_set_explainer))
    application.add_handler(CommandHandler("admin_help", admin_help))
    
    # Premium package management commands
    application.add_handler(CommandHandler("activate_premium", admin_activate_premium))
    application.add_handler(CommandHandler("deactivate_premium", admin_deactivate_premium))

    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VIDEO | filters.VOICE | filters.AUDIO, handle_media_upload))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.COMMAND, handle_invalid_command))
    application.add_error_handler(error_handler)

    # Job queue
    application.job_queue.run_repeating(daily_reminder, interval=86400, first=30)
    application.job_queue.run_repeating(low_stock_alert, interval=3600, first=60)

    logger.info("🚀 Starting bot with polling...")
    await application.initialize()
    bot = await application.bot.get_me()
    telegram_bot_username = bot.username
    logger.info("Telegram token verified for @%s (id=%s)", bot.username, bot.id)

    # A bot cannot receive polling updates while a webhook remains configured.
    # Do this explicitly so the deployment log confirms the transition.
    await application.bot.delete_webhook(drop_pending_updates=False)
    logger.info("Telegram webhook cleared; polling can receive updates.")
    await application.start()
    await application.updater.start_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES,
    )
    telegram_polling_ready = True
    logger.info("Telegram polling is active for @%s", bot.username)
    try:
        await asyncio.Event().wait()
    finally:
        telegram_polling_ready = False
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


def main():
    init_database()
    keep_alive()
    # asyncio.run creates and installs the event loop explicitly. This avoids
    # Application.run_polling() relying on the removed implicit loop in Python 3.14.
    asyncio.run(run_bot())


# ====================== ENTRY POINT ======================
if __name__ == "__main__":
    main()
