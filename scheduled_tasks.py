import logging
import asyncio
from datetime import datetime, time
from telegram.ext import Application
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from db import get_analytics, get_conn, return_conn
from config import ADMIN_ID
import psycopg

logger = logging.getLogger(__name__)

class ScheduledTasks:
    """Handle scheduled background tasks"""

    def __init__(self, application: Application):
        self.application = application
        self.scheduler = AsyncIOScheduler()
        self.setup_tasks()

    def setup_tasks(self):
        """Setup all scheduled tasks"""

        # Daily analytics report at 9 AM
        self.scheduler.add_job(
            self.daily_analytics_report,
            CronTrigger(hour=9, minute=0),
            id='daily_analytics',
            name='Daily Analytics Report'
        )

        # Database cleanup every 6 hours
        self.scheduler.add_job(
            self.cleanup_expired_data,
            IntervalTrigger(hours=6),
            id='cleanup_expired',
            name='Cleanup Expired Data'
        )

        # User engagement check every hour
        self.scheduler.add_job(
            self.check_user_engagement,
            IntervalTrigger(hours=1),
            id='user_engagement',
            name='User Engagement Check'
        )

        # Backup reminders every 2 hours during active hours (9 AM - 9 PM)
        self.scheduler.add_job(
            self.send_backup_reminders,
            IntervalTrigger(hours=2),
            id='backup_reminders',
            name='Backup Reminders'
        )

        logger.info("Scheduled tasks initialized")

    async def daily_analytics_report(self):
        """Send daily analytics report to admin"""
        try:
            analytics = get_analytics()

            if not analytics:
                logger.error("Failed to get analytics for daily report")
                return

            users = analytics["users"]
            payments = analytics["payments"]

            report = (
                "📊 DAILY ANALYTICS REPORT\n"
                f"Date: {datetime.now().strftime('%Y-%m-%d')}\n\n"
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

            await self.application.bot.send_message(
                chat_id=ADMIN_ID,
                text=report
            )

            logger.info("Daily analytics report sent to admin")

        except Exception as e:
            logger.error(f"Error sending daily analytics report: {e}")

    async def cleanup_expired_data(self):
        """Clean up expired tasks and old data"""
        try:
            conn = get_conn()
            cursor = conn.cursor()

            # Delete expired tasks
            deleted_tasks = cursor.execute("""
                DELETE FROM tasks
                WHERE expires_at < CURRENT_TIMESTAMP
            """).rowcount

            # Delete old interactions (older than 30 days)
            deleted_interactions = cursor.execute("""
                DELETE FROM interactions
                WHERE timestamp < CURRENT_TIMESTAMP - INTERVAL '30 days'
            """).rowcount

            conn.commit()
            return_conn(conn)

            logger.info(f"Cleanup completed: {deleted_tasks} expired tasks, {deleted_interactions} old interactions")

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    async def check_user_engagement(self):
        """Check for inactive users and send reminders"""
        try:
            conn = get_conn()
            cursor = conn.cursor()

            # Find users who haven't interacted in 7 days and have pending payments
            cursor.execute("""
                SELECT DISTINCT u.chat_id, u.username, u.payment_status
                FROM users u
                LEFT JOIN interactions i ON u.chat_id = i.chat_id
                WHERE u.payment_status IN ('new', 'payment_uploaded')
                AND (i.timestamp IS NULL OR i.timestamp < CURRENT_TIMESTAMP - INTERVAL '7 days')
                LIMIT 10  -- Limit to avoid spam
            """)

            inactive_users = cursor.fetchall()
            return_conn(conn)

            for user in inactive_users:
                try:
                    reminder_text = (
                        "👋 Hi! We noticed you started your Glamour journey but haven't completed registration yet.\n\n"
                        "Don't miss out on earning opportunities! Complete your payment and join our community.\n\n"
                        "Use /start to continue your registration."
                    )

                    await self.application.bot.send_message(
                        chat_id=user['chat_id'],
                        text=reminder_text
                    )

                    logger.info(f"Engagement reminder sent to user {user['chat_id']}")

                    # Small delay to avoid hitting rate limits
                    await asyncio.sleep(1)

                except Exception as e:
                    logger.error(f"Failed to send reminder to user {user['chat_id']}: {e}")

        except Exception as e:
            logger.error(f"Error checking user engagement: {e}")

    async def send_backup_reminders(self):
        """Send reminders during active hours"""
        current_hour = datetime.now().hour

        # Only send during active hours (9 AM - 9 PM)
        if 9 <= current_hour <= 21:
            try:
                conn = get_conn()
                cursor = conn.cursor()

                # Find registered users who haven't logged in today
                cursor.execute("""
                    SELECT chat_id, username
                    FROM users
                    WHERE payment_status = 'registered'
                    AND chat_id NOT IN (
                        SELECT DISTINCT chat_id
                        FROM interactions
                        WHERE DATE(timestamp) = CURRENT_DATE
                    )
                    LIMIT 20  -- Limit batch size
                """)

                users_to_remind = cursor.fetchall()
                return_conn(conn)

                reminder_text = (
                    "💰 Don't forget to check your daily tasks!\n\n"
                    "Complete simple activities and earn money:\n"
                    "• 📱 Take a walk\n"
                    "• 🎮 Play games\n"
                    "• 📸 Share posts\n"
                    "• 👥 Invite friends\n\n"
                    "Use /menu to see available tasks."
                )

                for user in users_to_remind:
                    try:
                        await self.application.bot.send_message(
                            chat_id=user['chat_id'],
                            text=reminder_text
                        )

                        # Small delay between messages
                        await asyncio.sleep(0.5)

                    except Exception as e:
                        logger.error(f"Failed to send backup reminder to user {user['chat_id']}: {e}")

                if users_to_remind:
                    logger.info(f"Sent backup reminders to {len(users_to_remind)} users")

            except Exception as e:
                logger.error(f"Error sending backup reminders: {e}")

    def start(self):
        """Start the scheduler"""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduled tasks started")

    def stop(self):
        """Stop the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Scheduled tasks stopped")

    async def manual_trigger(self, task_name: str):
        """Manually trigger a scheduled task for testing"""
        tasks = {
            'daily_analytics': self.daily_analytics_report,
            'cleanup': self.cleanup_expired_data,
            'engagement': self.check_user_engagement,
            'reminders': self.send_backup_reminders
        }

        if task_name in tasks:
            await tasks[task_name]()
            return f"Task '{task_name}' triggered successfully"
        else:
            return f"Unknown task: {task_name}"
