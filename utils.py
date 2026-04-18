import secrets
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def validate_email(email: str) -> bool:
    """Validate email format"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

def validate_phone(phone: str) -> bool:
    """Validate phone number (basic)"""
    # Remove common formatting characters
    cleaned = re.sub(r'[\s\-\(\)]', '', phone)
    # Check if it contains only digits and has reasonable length (7-15 digits)
    return re.match(r'^\d{7,15}$', cleaned) is not None

def validate_username(username: str) -> bool:
    """Validate Telegram username"""
    if not username:
        return False
    # Telegram usernames are 5-32 characters, alphanumeric and underscores
    return re.match(r'^[a-zA-Z0-9_]{5,32}$', username) is not None

def sanitize_input(text: str, max_length: int = 500) -> str:
    """Sanitize user input to prevent injection"""
    if not isinstance(text, str):
        return ""
    
    # Remove potential SQL injection characters
    text = text.strip()
    
    # Limit length
    text = text[:max_length]
    
    # Remove control characters
    text = "".join(char for char in text if ord(char) >= 32 or char in '\n\t')
    
    return text

def sanitize_float(value: str) -> Optional[float]:
    """Safely convert string to float"""
    try:
        value = value.strip()
        # Remove common currency symbols
        value = value.replace('$', '').replace('₦', '').replace(',', '').strip()
        
        float_value = float(value)
        
        # Sanity check - prevent extremely large numbers
        if abs(float_value) > 1_000_000:
            logger.warning(f"Float value out of range: {float_value}")
            return None
        
        return float_value
    except (ValueError, AttributeError):
        return None

def generate_referral_code() -> str:
    """Generate secure referral code"""
    return secrets.token_urlsafe(8)[:12]

def generate_password(length: int = 12) -> str:
    """Generate secure random password"""
    return secrets.token_urlsafe(length)[:length]

def format_currency(amount: float, currency: str = "$") -> str:
    """Format amount as currency"""
    return f"{currency}{amount:.2f}"

def format_user_stats(user_data: dict) -> str:
    """Format user statistics for display"""
    return (
        "📊 Your Platform Stats:\n\n"
        f"• Package: {user_data.get('package') or 'Not selected'}\n"
        f"• Payment Status: {str(user_data.get('payment_status', '')).capitalize()}\n"
        f"• Streaks: {user_data.get('streaks', 0)}\n"
        f"• Invites: {user_data.get('invites', 0)}\n"
        f"• Balance: ${user_data.get('balance', 0):.2f}"
    )

def log_action(chat_id: int, action: str, details: str = ""):
    """Log important actions for audit trail"""
    timestamp = __import__('datetime').datetime.now().isoformat()
    logger.info(f"[AUDIT] chat_id={chat_id}, action={action}, details={details}, timestamp={timestamp}")

class RateLimiter:
    """Simple in-memory rate limiter"""
    def __init__(self, max_requests: int = 5, time_window: int = 60):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = {}
    
    def is_allowed(self, chat_id: int) -> bool:
        """Check if request is allowed"""
        import time
        current_time = time.time()
        
        if chat_id not in self.requests:
            self.requests[chat_id] = []
        
        # Remove old requests outside time window
        self.requests[chat_id] = [
            req_time for req_time in self.requests[chat_id]
            if current_time - req_time < self.time_window
        ]
        
        # Check if under limit
        if len(self.requests[chat_id]) < self.max_requests:
            self.requests[chat_id].append(current_time)
            return True
        
        return False
    
    def cleanup_old_entries(self):
        """Clean up old entries to prevent memory bloat"""
        import time
        current_time = time.time()
        self.requests = {
            chat_id: reqs for chat_id, reqs in self.requests.items()
            if any(current_time - req_time < self.time_window for req_time in reqs)
        }

# Global rate limiters
command_limiter = RateLimiter(max_requests=10, time_window=60)  # 10 commands per minute
withdrawal_limiter = RateLimiter(max_requests=1, time_window=3600)  # 1 withdrawal per hour
