import os
from dotenv import load_dotenv

load_dotenv()

# Telegram Bot Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")

# Links
GROUP_LINK = os.getenv("GROUP_LINK", "")
SITE_LINK = os.getenv("SITE_LINK", "")
AI_BOOST_LINK = os.getenv("AI_BOOST_LINK", "")
DAILY_TASK_LINK = os.getenv("DAILY_TASK_LINK", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://Glamour.onrender.com/app")
FLUTTERWAVE_PAYMENT_LINK = os.getenv("FLUTTERWAVE_PAYMENT_LINK", "https://flutterwave.com/pay/elideckker0c")

# Database Configuration
DATABASE_URL = os.getenv("DATABASE_URL")

# Payment Accounts
PAYMENT_ACCOUNTS = {
    "Nigeria (Opay)": "󰐕 Account: 6110749592\nBank: Opay\nName: Chike Eluem Olanrewaju",
    "Nigeria (Zenith)": "󰐕 Account: 2267515466\nBank: Zenith Bank\nName: Chike Eluem Olanrewaju",
    "Nigeria (MoniePoint)": "󰐕 Account: 5168745850\nBank: MoniePoint\nName: Chike Eluem Olanrewaju",
}

COUPON_PAYMENT_ACCOUNTS = {
    "Coupon Acct 1 (Opay)": "󰐕 Account: 6110749592\nBank: Opay\nName: Chike Eluem Olanrewaju",
    "Coupon Acct 2 (Zenith)": "󰐕 Account: 2267515466\nBank: Zenith Bank\nName: Chike Eluem Olanrewaju",
    "Coupon Acct 3 (MoniePoint)": "󰐕 Account: 5168745850\nBank: MoniePoint\nName: Chike Eluem Olanrewaju",
}

# FAQs
FAQS = {
    "what_is_ethereal": {
        "question": "What is Glamour?",
        "answer": "Glamour is a platform where you earn money by completing tasks like taking a walk, reading posts, playing games, sending Snapchat streaks, and inviting friends."
    },
    "payment_methods": {
        "question": "What payment methods are available?",
        "answer": "Payments can be made via bank transfer, mobile money, PayPal or Zelle for foreign accounts. Check the 'How to Pay' guide in the Help menu."
    },
    "task_rewards": {
        "question": "How are task rewards calculated?",
        "answer": "Rewards vary by task type. For example, reading posts earns $2.5 per 10 words, Candy Crush tasks earn $5 daily, and Snapchat streaks can earn up to $20."
    }
}

# Help Topics
HELP_TOPICS = {
    "how_to_pay": {"label": "How to Pay", "type": "video", "url": "https://youtu.be/ (will be available soon)"},
    "register": {"label": "Registration Process", "type": "text", "text": (
        "1. /start → choose package\n"
        "2. Pay via your selected account → upload screenshot\n"
        "3. Provide your details (name, email, phone, Telegram username)\n"
        "4. Wait for admin approval\n"
        "5. Join the group and start earning! 🎉"
    )},
    "daily_tasks": {"label": "Daily Tasks", "type": "video", "url": "https://youtu.be/ (will be available soon)"},
    "reminder": {"label": "Toggle Reminder", "type": "toggle"},
    "faq": {"label": "FAQs", "type": "faq"},
    "apply_coach": {"label": "Apply to become Coach", "type": "text", "text": (
        "Please contact the Admin @bigscottmedia to discuss your application process"
    )},
    "password_recovery": {"label": "Password Recovery", "type": "input", "text": "Please provide your registered email to request password recovery:"},
}

# Validation
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required in environment (.env)")
if not ADMIN_ID:
    raise ValueError("ADMIN_ID is required in environment (.env)")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is required in environment (.env)")
