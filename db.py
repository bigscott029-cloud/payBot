import logging
import psycopg
from psycopg_pool import ConnectionPool
from functools import lru_cache
import urllib.parse as urlparse
from config import DATABASE_URL

logger = logging.getLogger(__name__)

# Initialize connection pool
url = DATABASE_URL
if "sslmode=" not in url:
    if "?" in url:
        url += "&sslmode=require"
    else:
        url += "?sslmode=require"

try:
    pool = ConnectionPool(
        url,
        open=True,
        max_size=10,
        min_size=2,
        kwargs={"row_factory": psycopg.rows.dict_row, "autocommit": True}
    )
    logger.info("Database connection pool initialized")
except psycopg.Error as e:
    logger.error(f"Failed to initialize connection pool: {e}")
    raise

def get_conn():
    """Get a connection from the pool"""
    try:
        return pool.getconn()
    except Exception as e:
        logger.error(f"Failed to get connection: {e}")
        raise

def return_conn(conn):
    """Return connection to the pool"""
    try:
        pool.putconn(conn)
    except Exception as e:
        logger.error(f"Failed to return connection: {e}")

def init_database():
    """Initialize database tables"""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        
        # Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id BIGINT PRIMARY KEY,
                package TEXT,
                payment_status TEXT DEFAULT 'new',
                name TEXT,
                username TEXT,
                email TEXT,
                phone TEXT,
                password TEXT,
                join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                alarm_setting INTEGER DEFAULT 0,
                streaks INTEGER DEFAULT 0,
                invites INTEGER DEFAULT 0,
                balance REAL DEFAULT 0,
                screenshot_uploaded_at TIMESTAMP,
                approved_at TIMESTAMP,
                registration_date TIMESTAMP,
                referral_code TEXT UNIQUE,
                referred_by BIGINT
            )
        """)

        # Payments table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT,
                type TEXT,
                package TEXT,
                quantity INTEGER,
                total_amount INTEGER,
                payment_account TEXT,
                method TEXT DEFAULT 'manual',
                is_upgrade BOOLEAN DEFAULT FALSE,
                status TEXT DEFAULT 'pending_payment',
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMP,
                FOREIGN KEY (chat_id) REFERENCES users(chat_id)
            )
        """)
        cursor.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS method TEXT DEFAULT 'manual'")

        # Coupons table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
                id SERIAL PRIMARY KEY,
                payment_id INTEGER,
                code TEXT UNIQUE,
                FOREIGN KEY (payment_id) REFERENCES payments(id)
            )
        """)

        # Interactions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS interactions (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT,
                action TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (chat_id) REFERENCES users(chat_id)
            )
        """)

        # Tasks table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id SERIAL PRIMARY KEY,
                type TEXT,
                link TEXT,
                reward REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        """)

        # User_tasks table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_tasks (
                user_id BIGINT,
                task_id INTEGER,
                completed_at TIMESTAMP,
                PRIMARY KEY (user_id, task_id),
                FOREIGN KEY (user_id) REFERENCES users(chat_id),
                FOREIGN KEY (task_id) REFERENCES tasks(id)
            )
        """)

        logger.info("Database tables initialized successfully")
    except psycopg.Error as e:
        logger.error(f"Database initialization error: {e}")
        raise
    finally:
        return_conn(conn)

# Helper functions with caching
@lru_cache(maxsize=256)
def get_user_cached(chat_id):
    """Get user with caching (TTL should be implemented in production)"""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chat_id, username, package, payment_status, balance, 
                   streaks, invites, referral_code
            FROM users WHERE chat_id=%s
        """, (chat_id,))
        return cursor.fetchone()
    except psycopg.Error as e:
        logger.error(f"Database error in get_user_cached: {e}")
        return None
    finally:
        return_conn(conn)

def get_user(chat_id):
    """Get user directly from database"""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT chat_id, username, package, payment_status, balance, 
                   streaks, invites, referral_code
            FROM users WHERE chat_id=%s
        """, (chat_id,))
        return cursor.fetchone()
    except psycopg.Error as e:
        logger.error(f"Database error in get_user: {e}")
        return None
    finally:
        return_conn(conn)

def get_status(chat_id):
    """Get user payment status"""
    user = get_user(chat_id)
    return user["payment_status"] if user else None

def is_registered(chat_id):
    """Check if user is registered"""
    user = get_user(chat_id)
    return user and user["payment_status"] == 'registered'

def create_user(chat_id, username, referral_code, referred_by=None):
    """Create new user"""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (chat_id, username, referral_code, referred_by)
            VALUES (%s, %s, %s, %s)
        """, (chat_id, username, referral_code, referred_by))
        
        if referred_by:
            cursor.execute("""
                UPDATE users SET invites = invites + 1, balance = balance + 0.1
                WHERE chat_id=%s
            """, (referred_by,))
        
        get_user_cached.cache_clear()  # Clear cache
        logger.info(f"User created: {chat_id}")
    except psycopg.Error as e:
        logger.error(f"Database error in create_user: {e}")
        raise
    finally:
        return_conn(conn)

def log_interaction(chat_id, action):
    """Log user interaction"""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO interactions (chat_id, action) VALUES (%s, %s)
        """, (chat_id, action))
    except psycopg.Error as e:
        logger.error(f"Database error in log_interaction: {e}")
    finally:
        return_conn(conn)

def get_analytics():
    """Get platform analytics"""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        
        # Fetch analytics
        cursor.execute("""
            SELECT 
                COUNT(DISTINCT chat_id) as total_users,
                COUNT(DISTINCT CASE WHEN payment_status='registered' THEN chat_id END) as registered_users,
                COALESCE(SUM(balance), 0) as total_balance,
                COALESCE(AVG(balance), 0) as avg_balance
            FROM users
        """)
        user_stats = cursor.fetchone()
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total_payments,
                COUNT(CASE WHEN status='completed' THEN 1 END) as completed_payments,
                COUNT(CASE WHEN status='pending_payment' THEN 1 END) as pending_payments,
                COALESCE(SUM(total_amount), 0) as total_revenue
            FROM payments
        """)
        payment_stats = cursor.fetchone()
        
        return {
            "users": user_stats,
            "payments": payment_stats,
            "timestamp": __import__('datetime').datetime.now()
        }
    except psycopg.Error as e:
        logger.error(f"Database error in get_analytics: {e}")
        return None
    finally:
        return_conn(conn)
