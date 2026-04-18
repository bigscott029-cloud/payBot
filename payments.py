import datetime
import logging
from db import get_conn, return_conn

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
):
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO payments (
                chat_id, type, package, quantity, total_amount, payment_account,
                is_upgrade, status, method, timestamp
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    update_payment_status(payment_id, 'approved', approved_at=datetime.datetime.now())


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
