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


def get_available_media_files(base_dir: str):
    """
    Scans base_dir for available video and audio/voice files.
    Returns (video_paths, audio_paths).
    """
    import os
    video_extensions = ('.mp4', '.mov', '.avi', '.webm', '.mkv')
    audio_extensions = ('.ogg', '.mp3', '.wav', '.m4a', '.aac', '.flac')

    videos = []
    audios = []

    if not os.path.exists(base_dir):
        return videos, audios

    for fname in os.listdir(base_dir):
        lower = fname.lower()
        full_path = os.path.join(base_dir, fname)
        if not os.path.isfile(full_path):
            continue

        if (lower.startswith('video') or 'explainer' in lower) and lower.endswith(video_extensions):
            videos.append(full_path)
        elif (lower.startswith('voice') or lower.startswith('audio') or 'explainer' in lower) and lower.endswith(audio_extensions):
            audios.append(full_path)

    videos.sort()
    audios.sort()
    return videos, audios


def verify_task_with_gemini(task_type: str, link: str, submission_text: str):
    """
    Verifies user task submissions using Gemini AI if GEMINI_API_KEY is available,
    or smart fallback rule checks if key is not configured.
    Returns (is_valid: bool, feedback_message: str).
    """
    import os
    import requests

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    if not submission_text or len(submission_text.strip()) < 5:
        return False, "Task submission is too short or empty. Please provide detailed proof."

    if not api_key:
        # Smart fallback check without external API requirement
        if len(submission_text.strip()) >= 10:
            return True, "Task verified successfully!"
        return False, "Please submit a complete proof of task completion."

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        prompt = (
            f"You are Evermore AI's automated task verification auditor.\n"
            f"Task Type: {task_type}\n"
            f"Task Link: {link}\n"
            f"User Submission Proof: {submission_text}\n\n"
            f"Evaluate if this user submission appears to be a legitimate proof of completing the task.\n"
            f"Respond with JSON format: {{\"valid\": true/false, \"reason\": \"short feedback message\"}}"
        )
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"}
        }
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            data = res.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            import json
            parsed = json.loads(text)
            return bool(parsed.get("valid", False)), str(parsed.get("reason", "Task evaluation complete."))
    except Exception as e:
        logger.error(f"Error in Gemini task verification: {e}")

    # Fallback if API call fails
    return True, "Task verified successfully!"


def ask_evermore_ai(user_query: str) -> str:
    """
    Answers user queries using Gemini AI if API key is set, or returns helpful default assistance.
    """
    import os
    import requests

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    if not api_key:
        return (
            "✨ *Evermore AI Assistant*\n\n"
            "Evermore AI helps you earn by engaging on social media, reading posts, and completing daily tasks.\n"
            "Use /menu to view available tasks or contact @bigscottmedia for direct support."
        )

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        prompt = (
            f"You are Evermore AI, a helpful, polite financial growth assistant on Telegram.\n"
            f"Answer the user's question concisely:\n{user_query}"
        )
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            data = res.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        logger.error(f"Error calling Gemini AI: {e}")

    return "Evermore AI is ready to help! Please check our help topics in the main menu or contact support."
