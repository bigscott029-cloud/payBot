import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import ADMIN_ID
from db import get_analytics, log_interaction, get_conn, return_conn
from payments import get_payment, approve_payment, reject_payment, list_pending_payments
import datetime

logger = logging.getLogger(__name__)

async def admin_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show platform analytics to admin"""
    chat_id = update.effective_chat.id
    
    if chat_id != ADMIN_ID:
        await update.message.reply_text("This command is restricted to the admin.")
        return
    
    log_interaction(chat_id, "admin_analytics")
    
    try:
        analytics = get_analytics()
        
        if not analytics:
            await update.message.reply_text("Failed to retrieve analytics.")
            return
        
        users = analytics["users"]
        payments = analytics["payments"]
        
        text = (
            "📊 PLATFORM ANALYTICS\n"
            f"Last Updated: {analytics['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            "👥 USER STATISTICS\n"
            f"• Total Users: {users['total_users']}\n"
            f"• Registered Users: {users['registered_users']}\n"
            f"• Total Platform Balance: ${users['total_balance']:.2f}\n"
            f"• Average User Balance: ${users['avg_balance']:.2f}\n\n"
            "💳 PAYMENT STATISTICS\n"
            f"• Total Payments: {payments['total_payments']}\n"
            f"• Completed Payments: {payments['completed_payments']}\n"
            f"• Pending Payments: {payments['pending_payments']}\n"
            f"• Total Revenue: ${payments['total_revenue']:.2f}\n"
        )
        
        keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="admin_analytics_refresh")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        logger.error(f"Error in admin_analytics: {e}")
        await update.message.reply_text(f"Error retrieving analytics: {e}")

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start broadcast message flow"""
    chat_id = update.effective_chat.id
    
    if chat_id != ADMIN_ID:
        await update.message.reply_text("This command is restricted to the admin.")
        return
    
    context.user_data['expecting'] = 'broadcast_message'
    await update.message.reply_text(
        "📢 BROADCAST MESSAGE\n\n"
        "Enter the message to send to all registered users:\n"
        "(Use /cancel to abort)"
    )
    log_interaction(chat_id, "broadcast_initiated")

async def admin_stats_by_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user distribution by package"""
    chat_id = update.effective_chat.id
    
    if chat_id != ADMIN_ID:
        await update.message.reply_text("This command is restricted to the admin.")
        return
    
    log_interaction(chat_id, "admin_stats_by_package")
    
    try:
        from db import get_conn, return_conn
        import psycopg
        
        conn = get_conn()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT package, COUNT(*) as count, 
                   COUNT(CASE WHEN payment_status='registered' THEN 1 END) as active
            FROM users
            GROUP BY package
        """)
        results = cursor.fetchall()
        return_conn(conn)
        
        if not results:
            await update.message.reply_text("No user data available.")
            return
        
        text = "📦 USERS BY PACKAGE\n\n"
        for row in results:
            package = row['package'] or 'None'
            text += f"• {package}: {row['count']} users (Active: {row['active']})\n"
        
        await update.message.reply_text(text)
        
    except Exception as e:
        logger.error(f"Error in admin_stats_by_package: {e}")
        await update.message.reply_text(f"Error: {e}")

async def admin_manual_payment_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to approve pending payments"""
    chat_id = update.effective_chat.id
    
    if chat_id != ADMIN_ID:
        await update.message.reply_text("This command is restricted to the admin.")
        return
    
    context.user_data['expecting'] = 'payment_approval'
    await update.message.reply_text(
        "💳 PAYMENT APPROVAL\n\n"
        "Enter payment ID to approve:\n"
        "(Use /cancel to abort)"
    )
    log_interaction(chat_id, "payment_approval_initiated")

async def admin_approve_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != ADMIN_ID:
        await update.message.reply_text("This command is restricted to the admin.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /approve_payment <payment_id>")
        return
    try:
        payment_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Payment ID must be a number.")
        return

    payment = get_payment(payment_id)
    if not payment:
        await update.message.reply_text("Payment not found.")
        return
    approve_payment(payment_id)
    conn = get_conn()
    try:
        cursor = conn.cursor()
        if payment['type'] == 'registration':
            cursor.execute(
                "UPDATE users SET payment_status=%s WHERE chat_id=%s",
                ('pending_details', payment['chat_id']),
            )
            await context.bot.send_message(
                payment['chat_id'],
                "✅ Your payment has been approved. Please send your full name to continue registration."
            )
    finally:
        return_conn(conn)
    await update.message.reply_text(f"Payment {payment_id} approved.")
    log_interaction(chat_id, "approve_payment")

async def admin_reject_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != ADMIN_ID:
        await update.message.reply_text("This command is restricted to the admin.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /reject_payment <payment_id>")
        return
    try:
        payment_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Payment ID must be a number.")
        return

    payment = get_payment(payment_id)
    if not payment:
        await update.message.reply_text("Payment not found.")
        return
    reject_payment(payment_id)
    await context.bot.send_message(
        payment['chat_id'],
        "❌ Your payment has been rejected by the admin. Please review the instructions and try again."
    )
    await update.message.reply_text(f"Payment {payment_id} rejected.")
    log_interaction(chat_id, "reject_payment")

async def admin_pending_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != ADMIN_ID:
        await update.message.reply_text("This command is restricted to the admin.")
        return
    payments = list_pending_payments()
    if not payments:
        await update.message.reply_text("No pending payments found.")
        return
    text = "📌 Pending Payments:\n\n"
    for payment in payments[:10]:
        text += (
            f"ID: {payment['id']} | User: {payment['chat_id']} | Package: {payment['package']} "
            f"| Method: {payment['method']} | Amount: ₦{payment['total_amount']}\n"
        )
    await update.message.reply_text(text)
    log_interaction(chat_id, "pending_payments")

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin help menu"""
    chat_id = update.effective_chat.id
    
    if chat_id != ADMIN_ID:
        return
    
    help_text = (
        "🔧 ADMIN COMMANDS\n\n"
        "/analytics - View platform analytics\n"
        "/stats_package - Users by package\n"
        "/broadcast - Send message to all users\n"
        "/payment_approve - Approve pending payment\n"
        "/approve_payment <payment_id> - Approve a payment immediately\n"
        "/reject_payment <payment_id> - Reject a payment immediately\n"
        "/payments_pending - List pending payments\n"
        "/add_task - Add new task\n"
        "/add_task <type> <link> <reward>\n"
        "\nExample:\n"
        "/add_task reading https://example.com 2.5"
    )
    
    await update.message.reply_text(help_text)
