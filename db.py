import logging
import os
import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout
from functools import lru_cache
import urllib.parse as urlparse
from config import DATABASE_URL

logger = logging.getLogger(__name__)

# Normalize DATABASE_URL for psycopg3 & Render
url = DATABASE_URL or ""
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql://", 1)

# Ensure sslmode is present for remote connections if not specified
if url and "sslmode=" not in url and "localhost" not in url and "127.0.0.1" not in url:
    if "?" in url:
        url += "&sslmode=require"
    else:
        url += "?sslmode=require"

parsed_database_url = urlparse.urlparse(url)
if parsed_database_url.hostname:
    logger.info(
        "Database endpoint configured: %s:%s",
        parsed_database_url.hostname,
        parsed_database_url.port or 5432,
    )

try:
    # Supabase's transaction pooler (port 6543) does not support prepared
    # statements. Disable Psycopg's automatic preparation to support it.
    pool = ConnectionPool(
        url,
        open=True,
        min_size=1,
        max_size=int(os.getenv("DATABASE_POOL_MAX_SIZE", "4")),
        timeout=10.0,
        kwargs={
            "row_factory": psycopg.rows.dict_row,
            "autocommit": True,
            "connect_timeout": 10,
            "prepare_threshold": None,
        }
    )
    logger.info("Database connection pool initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize connection pool: {e}")
    pool = None

def get_conn():
    """Get a connection from the pool"""
    if pool is None:
        raise RuntimeError("Database connection pool is not initialized. Please verify your DATABASE_URL environment variable.")
    try:
        return pool.getconn(timeout=10.0)
    except PoolTimeout:
        logger.error("Database connection timeout (10s)! Unable to connect to PostgreSQL. Please check DATABASE_URL environment variable on Render.")
        raise RuntimeError("Database connection timed out. Please check your DATABASE_URL in Render environment settings.") from None
    except Exception as e:
        logger.error(f"Failed to get database connection: {e}")
        raise

def return_conn(conn):
    """Return connection to the pool"""
    if pool and conn:
        try:
            pool.putconn(conn)
        except Exception as e:
            logger.error(f"Failed to return connection: {e}")

def init_database():
    """Initialize database tables"""
    try:
        conn = get_conn()
    except Exception as e:
        logger.error(f"Cannot initialize database tables due to connection failure: {e}")
        return False

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
        cursor.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS tx_ref TEXT UNIQUE")

        # Coupons table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
                id SERIAL PRIMARY KEY,
                payment_id INTEGER,
                code TEXT UNIQUE,
                FOREIGN KEY (payment_id) REFERENCES payments(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS access_codes (
                id SERIAL PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                plan TEXT,
                status TEXT NOT NULL DEFAULT 'available',
                payment_id INTEGER REFERENCES payments(id),
                issued_to BIGINT REFERENCES users(chat_id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                issued_at TIMESTAMP
            )
        """)
        cursor.execute("ALTER TABLE access_codes ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP")
        cursor.execute("ALTER TABLE access_codes ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMP")
        cursor.execute("ALTER TABLE access_codes ADD COLUMN IF NOT EXISTS revoke_reason TEXT")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS payment_events (
                id SERIAL PRIMARY KEY,
                payment_id INTEGER REFERENCES payments(id),
                tx_ref TEXT,
                event_type TEXT NOT NULL,
                payload JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

        # Bot Settings table (for dynamic media file_ids, system flags)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Referral Commissions audit table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS referral_commissions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                from_user_id BIGINT,
                level INTEGER,
                amount REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    """Best-effort analytics logging; it must never break a bot response."""
    conn = None
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO interactions (chat_id, action) VALUES (%s, %s)
        """, (chat_id, action))
    except Exception as e:
        logger.warning("Could not log interaction '%s' for %s: %s", action, chat_id, e)
    finally:
        if conn:
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
                COUNT(DISTINCT CASE WHEN payment_status IN ('paid_pending_code', 'registered') THEN chat_id END) as code_buyers,
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
        cursor.execute("SELECT COUNT(*) AS engagements FROM interactions")
        engagement_stats = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) AS issued_codes FROM access_codes WHERE status='issued'")
        code_stats = cursor.fetchone()
        
        return {
            "users": user_stats,
            "payments": payment_stats,
            "engagements": engagement_stats['engagements'],
            "issued_codes": code_stats['issued_codes'],
            "timestamp": __import__('datetime').datetime.now()
        }
    except psycopg.Error as e:
        logger.error(f"Database error in get_analytics: {e}")
        return None
    finally:
        return_conn(conn)


def get_setting(key: str, default=None):
    """Retrieve setting value from bot_settings table"""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM bot_settings WHERE key=%s", (key,))
        row = cursor.fetchone()
        return row['value'] if row else default
    except psycopg.Error as e:
        logger.error(f"Database error in get_setting ({key}): {e}")
        return default
    finally:
        return_conn(conn)


def set_setting(key: str, value: str):
    """Set or update setting in bot_settings table"""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO bot_settings (key, value, updated_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=CURRENT_TIMESTAMP
        """, (key, value))
        logger.info(f"Setting updated: {key}={value}")
    except psycopg.Error as e:
        logger.error(f"Database error in set_setting ({key}): {e}")
    finally:
        return_conn(conn)


def process_referral_commissions(chat_id: int):
    """
    Processes multi-tier referral commissions when a user payment is approved:
    - Level 1 (Direct Upline): ₦1000
    - Level 2 (1st Indirect Upline): ₦400
    - Level 3 (2nd Indirect Upline): ₦100
    Returns list of notification dicts: [{'chat_id': id, 'amount': amt, 'level': lvl}]
    """
    notifications = []
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT referred_by, username FROM users WHERE chat_id=%s", (chat_id,))
        payer = cursor.fetchone()
        if not payer:
            return notifications

        payer_username = payer['username'] or f"User {chat_id}"
        current_user_id = chat_id
        current_upline_id = payer['referred_by']

        # Level amounts in NGN
        tier_amounts = {1: 1000.0, 2: 400.0, 3: 100.0}

        for level in (1, 2, 3):
            if not current_upline_id:
                break

            amount = tier_amounts.get(level, 0.0)
            if amount > 0:
                # Credit balance to upline
                cursor.execute("""
                    UPDATE users SET balance = balance + %s WHERE chat_id=%s
                """, (amount, current_upline_id))

                # Log commission
                cursor.execute("""
                    INSERT INTO referral_commissions (user_id, from_user_id, level, amount)
                    VALUES (%s, %s, %s, %s)
                """, (current_upline_id, chat_id, level, amount))

                notifications.append({
                    'chat_id': current_upline_id,
                    'amount': amount,
                    'level': level,
                    'payer_username': payer_username
                })

            # Fetch next level upline
            cursor.execute("SELECT referred_by FROM users WHERE chat_id=%s", (current_upline_id,))
            next_upline = cursor.fetchone()
            current_upline_id = next_upline['referred_by'] if next_upline else None

        logger.info(f"Processed multi-tier referral commissions for payer {chat_id}: {notifications}")
        return notifications
    except psycopg.Error as e:
        logger.error(f"Database error in process_referral_commissions for {chat_id}: {e}")
        return []
    finally:
        return_conn(conn)
