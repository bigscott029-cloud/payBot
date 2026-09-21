import datetime
import logging
import os
import uuid
import requests
from psycopg.types.json import Jsonb
from db import get_conn, return_conn
from config import FLUTTERWAVE_REDIRECT_URL, FLUTTERWAVE_SECRET_KEY, PUBLIC_BASE_URL
from redis_cache import cache

logger = logging.getLogger(__name__)

PAYMENT_TYPE_REGISTRATION = 'registration'
PAYMENT_TYPE_COUPON = 'coupon'


def create_payment(
    chat_id,
    payment_type,
    package,
    quantity,
    total_amount,
    payment_account,
    is_upgrade=False,
    status='pending_payment',
    method='manual',
    tx_ref=None,
):
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO payments (
                chat_id, type, package, quantity, total_amount, payment_account,
                is_upgrade, status, method, tx_ref, timestamp
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                chat_id,
                payment_type,
                package,
                quantity,
                total_amount,
                payment_account,
                is_upgrade,
                status,
                method,
                tx_ref,
                datetime.datetime.now(),
            ),
        )
        payment_id = cursor.fetchone()['id']
        return payment_id
    except Exception as exc:
        logger.error(f"Error creating payment: {exc}")
        raise
    finally:
        return_conn(conn)


def get_payment(payment_id):
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM payments WHERE id=%s", (payment_id,))
        return cursor.fetchone()
    except Exception as exc:
        logger.error(f"Error fetching payment {payment_id}: {exc}")
        return None
    finally:
        return_conn(conn)


def update_payment_status(payment_id, status, approved_at=None):
    conn = get_conn()
    try:
        cursor = conn.cursor()
        if approved_at:
            cursor.execute(
                "UPDATE payments SET status=%s, approved_at=%s WHERE id=%s",
                (status, approved_at, payment_id),
            )
        else:
            cursor.execute(
                "UPDATE payments SET status=%s WHERE id=%s",
                (status, payment_id),
            )
    except Exception as exc:
        logger.error(f"Error updating payment status {payment_id}: {exc}")
        raise
    finally:
        return_conn(conn)


def approve_payment(payment_id):
    # Approval is the business event that earns referral commission. Never run it twice.
    existing = get_payment(payment_id)
    if not existing:
        return None, []
    if existing['status'] != 'pending_payment':
        logger.warning("Ignoring duplicate approval for payment %s (status=%s)", payment_id, existing['status'])
        return existing, []
    update_payment_status(payment_id, 'approved', approved_at=datetime.datetime.now())
    payment = get_payment(payment_id)
    notifications = []
    if payment and payment.get('chat_id'):
        from db import process_referral_commissions
        notifications = process_referral_commissions(payment['chat_id'])
    return payment, notifications


def reject_payment(payment_id):
    update_payment_status(payment_id, 'rejected')


def list_pending_payments():
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM payments WHERE status='pending_payment' ORDER BY timestamp DESC")
        return cursor.fetchall()
    except Exception as exc:
        logger.error(f"Error listing pending payments: {exc}")
        return []
    finally:
        return_conn(conn)


def initialize_flutterwave_payment(chat_id, plan, amount, email, name="Telegram member", payment_type=PAYMENT_TYPE_COUPON):
    """Create a unique Flutterwave checkout and retain its reference locally."""
    if not FLUTTERWAVE_SECRET_KEY or not PUBLIC_BASE_URL:
        raise RuntimeError("Flutterwave is not configured. Set FLUTTERWAVE_SECRET_KEY and PUBLIC_BASE_URL.")
    tx_ref = f"everai-{chat_id}-{uuid.uuid4().hex[:12]}"
    redirect_url = FLUTTERWAVE_REDIRECT_URL or f"{PUBLIC_BASE_URL}/flutterwave/callback"
    payload = {
        "tx_ref": tx_ref, "amount": amount, "currency": "NGN",
        "redirect_url": redirect_url,
        "customer": {"email": email, "name": name},
        "customizations": {"title": "EverAI Verified Access Plan", "description": f"{plan} access code"},
        "meta": {"telegram_chat_id": str(chat_id), "plan": plan},
    }
    response = requests.post("https://api.flutterwave.com/v3/payments", json=payload,
                             headers={"Authorization": f"Bearer {FLUTTERWAVE_SECRET_KEY}"}, timeout=20)
    response.raise_for_status()
    link = response.json().get("data", {}).get("link")
    if not link:
        raise RuntimeError("Flutterwave did not return a payment link.")
    payment_id = create_payment(chat_id, payment_type, plan, 1, amount, "Flutterwave",
                                status="pending_payment", method="flutterwave", tx_ref=tx_ref)
    return payment_id, tx_ref, link


def verify_flutterwave_payment(tx_ref):
    """Verify by our own reference; never trust browser callback parameters."""
    if not FLUTTERWAVE_SECRET_KEY:
        raise RuntimeError("Flutterwave secret key is not configured.")
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM payments WHERE tx_ref=%s", (tx_ref,))
        payment = cursor.fetchone()
        if not payment:
            return None, "unknown"
        if payment['status'] in ('completed', 'pending_code'):
            return payment, payment['status']
        response = requests.get("https://api.flutterwave.com/v3/transactions/verify_by_reference",
            params={"tx_ref": tx_ref}, headers={"Authorization": f"Bearer {FLUTTERWAVE_SECRET_KEY}"}, timeout=20)
        response.raise_for_status()
        data = response.json().get("data", {})
        cursor.execute("INSERT INTO payment_events (payment_id, tx_ref, event_type, payload) VALUES (%s, %s, %s, %s)",
                       (payment['id'], tx_ref, "flutterwave_verification", Jsonb(data)))
        valid = (data.get("status") == "successful" and data.get("currency") == "NGN"
                 and int(float(data.get("amount", 0))) == payment['total_amount']
                 and data.get("tx_ref") == tx_ref)
        if valid:
            if payment['type'] == PAYMENT_TYPE_REGISTRATION:
                cursor.execute("UPDATE payments SET status='approved', approved_at=%s WHERE id=%s",
                               (datetime.datetime.now(), payment['id']))
                cursor.execute("UPDATE users SET payment_status='payment_approved', package=%s WHERE chat_id=%s",
                               (payment['package'], payment['chat_id']))
                payment['status'] = 'approved'
                return payment, 'registration_details'
            cursor.execute("UPDATE payments SET status='pending_code', approved_at=%s WHERE id=%s",
                           (datetime.datetime.now(), payment['id']))
            cursor.execute("UPDATE users SET payment_status='paid_pending_code', package=%s WHERE chat_id=%s",
                           (payment['package'], payment['chat_id']))
            payment['status'] = 'pending_code'
            return payment, 'pending_code'
        return payment, data.get("status", "pending")
    finally:
        return_conn(conn)


def add_access_code(code, plan=None, expires_at=None):
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO access_codes (code, plan, expires_at) VALUES (%s, %s, %s) RETURNING id",
                       (code.strip(), plan, expires_at))
        return cursor.fetchone()['id']
    finally:
        return_conn(conn)


def revoke_access_code(code, reason="Revoked by admin"):
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""UPDATE access_codes SET status='revoked', revoked_at=%s, revoke_reason=%s
                          WHERE code=%s AND status != 'revoked' RETURNING *""",
                       (datetime.datetime.now(), reason, code.strip()))
        return cursor.fetchone()
    finally:
        return_conn(conn)


def get_code_stock():
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""SELECT COALESCE(plan, 'any') AS plan, status, COUNT(*) AS count
                          FROM access_codes GROUP BY plan, status ORDER BY plan, status""")
        stock = cursor.fetchall()
        cursor.execute("SELECT package, COUNT(*) AS count FROM payments WHERE status='pending_code' GROUP BY package ORDER BY package")
        waiting = cursor.fetchall()
        return stock, waiting
    finally:
        return_conn(conn)


def payment_export_rows():
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("""SELECT id, chat_id, package, total_amount, method, status, tx_ref, timestamp, approved_at
                          FROM payments ORDER BY timestamp DESC""")
        return cursor.fetchall()
    finally:
        return_conn(conn)


def allocate_access_code(payment_id):
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM payments WHERE id=%s", (payment_id,))
        payment = cursor.fetchone()
        if not payment or payment['status'] not in ('approved', 'pending_code', 'completed'):
            return None
        cursor.execute("""SELECT * FROM access_codes WHERE status='available'
                          AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
                          AND (plan IS NULL OR plan=%s) ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1""",
                       (payment['package'],))
        code = cursor.fetchone()
        if not code:
            return None
        cursor.execute("""UPDATE access_codes SET status='issued', payment_id=%s, issued_to=%s, issued_at=%s
                          WHERE id=%s AND status='available' RETURNING code""",
                       (payment_id, payment['chat_id'], datetime.datetime.now(), code['id']))
        if not cursor.fetchone():
            return None
        cursor.execute("UPDATE payments SET status='completed', approved_at=%s WHERE id=%s",
                       (datetime.datetime.now(), payment_id))
        cursor.execute("UPDATE users SET payment_status='registered' WHERE chat_id=%s", (payment['chat_id'],))
        # This short-lived record helps support/auditing without keeping an available code in cache.
        cache.set(f"issued-code:{code['code']}", {"payment_id": payment_id, "chat_id": payment['chat_id']}, ttl=7 * 86400)
        return code['code'], payment
    finally:
        return_conn(conn)


def fulfill_waiting_codes():
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM payments WHERE status='pending_code' ORDER BY timestamp")
        ids = [row['id'] for row in cursor.fetchall()]
    finally:
        return_conn(conn)
    return [(payment_id, allocation) for payment_id in ids if (allocation := allocate_access_code(payment_id))]
