import hashlib
import hmac
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qsl

DB_PATH = Path("api_users.db")

DAILY_LIMIT = 1000
SESSION_TTL = 86400


def init_api_db():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                api_key_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                requests_today INTEGER DEFAULT 0,
                usage_date TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                telegram_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            )
        """)

        # Telegram bot orqali web-login uchun bir martalik tokenlar
        db.execute("""
            CREATE TABLE IF NOT EXISTS login_tokens (
                token_hash TEXT PRIMARY KEY,
                telegram_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                used INTEGER DEFAULT 0,
                one_time_api_key TEXT
            )
        """)

        login_columns = {
            row[1]
            for row in db.execute(
                "PRAGMA table_info(login_tokens)"
            ).fetchall()
        }

        if "one_time_api_key" not in login_columns:
            db.execute(
                "ALTER TABLE login_tokens ADD COLUMN one_time_api_key TEXT"
            )

        # Telefon raqami mavjud eski users bazasiga xavfsiz qo'shiladi.
        columns = {
            row[1]
            for row in db.execute(
                "PRAGMA table_info(users)"
            ).fetchall()
        }

        if "phone_number" not in columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN phone_number TEXT"
            )

        if "phone_verified" not in columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN phone_verified INTEGER DEFAULT 0"
            )

        db.commit()


def hash_value(value):
    return hashlib.sha256(value.encode()).hexdigest()


def generate_key():
    return "YIX_" + secrets.token_urlsafe(32)


def generate_session():
    return "YIXSESSION_" + secrets.token_urlsafe(32)


def verify_telegram(data, bot_token):
    try:
        if isinstance(data, dict):
            auth_data = {
                str(k): str(v)
                for k, v in data.items()
            }
        else:
            auth_data = dict(parse_qsl(data))

        received_hash = auth_data.pop("hash", None)

        if not received_hash:
            return None

        check_string = "\n".join(
            f"{key}={auth_data[key]}"
            for key in sorted(auth_data)
        )

        secret_key = hashlib.sha256(
            bot_token.encode()
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return None

        auth_date = int(auth_data.get("auth_date", 0))
        now = int(time.time())

        if auth_date <= 0:
            return None

        if auth_date > now + 60:
            return None

        if now - auth_date > SESSION_TTL:
            return None

        if "id" not in auth_data:
            return None

        return auth_data

    except Exception as e:
        print("TELEGRAM VERIFY ERROR:", e)
        return None


def get_user_by_telegram(telegram_id):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("""
            SELECT telegram_id, username, first_name,
                   last_name, requests_today, usage_date
            FROM users
            WHERE telegram_id=?
        """, (telegram_id,)).fetchone()

    if not row:
        return None

    telegram_id, username, first_name, last_name, requests, usage_date = row

    today = time.strftime("%Y-%m-%d")

    if usage_date != today:
        with sqlite3.connect(DB_PATH) as db:
            db.execute("""
                UPDATE users
                SET requests_today=0, usage_date=?
                WHERE telegram_id=?
            """, (today, telegram_id))
            db.commit()

        requests = 0

    return {
        "telegram_id": telegram_id,
        "username": username or "",
        "first_name": first_name or "",
        "last_name": last_name or "",
        "requests_today": requests or 0,
        "daily_limit": DAILY_LIMIT,
    }


def create_user(data):
    telegram_id = int(data["id"])
    api_key = generate_key()

    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            INSERT INTO users
            (
                telegram_id,
                username,
                first_name,
                last_name,
                api_key_hash,
                created_at,
                requests_today,
                usage_date
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, date('now'))
        """, (
            telegram_id,
            data.get("username", ""),
            data.get("first_name", ""),
            data.get("last_name", ""),
            hash_value(api_key),
            int(time.time())
        ))

        db.commit()

    return api_key


def create_session(telegram_id):
    token = generate_session()
    token_hash = hash_value(token)

    now = int(time.time())
    expires = now + SESSION_TTL

    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            INSERT INTO sessions
            (
                token_hash,
                telegram_id,
                created_at,
                expires_at
            )
            VALUES (?, ?, ?, ?)
        """, (
            token_hash,
            telegram_id,
            now,
            expires
        ))

        db.commit()

    return token


def get_user_by_session(token):
    if not token:
        return None

    token_hash = hash_value(token)
    now = int(time.time())

    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("""
            SELECT telegram_id
            FROM sessions
            WHERE token_hash=?
            AND expires_at>?
        """, (token_hash, now)).fetchone()

    if not row:
        return None

    return get_user_by_telegram(row[0])



def revoke_session(token):
    """Bitta web sessionni darhol bekor qiladi."""
    if not token:
        return False

    token_hash = hash_value(token)

    with sqlite3.connect(DB_PATH) as db:
        result = db.execute("""
            DELETE FROM sessions
            WHERE token_hash=?
        """, (token_hash,))

        db.commit()

    return result.rowcount > 0


def revoke_all_sessions(telegram_id):
    """Foydalanuvchining barcha web sessionlarini bekor qiladi."""
    with sqlite3.connect(DB_PATH) as db:
        result = db.execute("""
            DELETE FROM sessions
            WHERE telegram_id=?
        """, (int(telegram_id),))

        db.commit()

    return result.rowcount

def regenerate_key(telegram_id):
    api_key = generate_key()

    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            UPDATE users
            SET api_key_hash=?
            WHERE telegram_id=?
        """, (
            hash_value(api_key),
            telegram_id
        ))

        db.commit()

    return api_key


def get_user_by_key(api_key):
    if not api_key:
        return None

    key_hash = hash_value(api_key)

    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("""
            SELECT telegram_id, username, first_name,
                   last_name, requests_today, usage_date
            FROM users
            WHERE api_key_hash=?
        """, (key_hash,)).fetchone()

    if not row:
        return None

    telegram_id, username, first_name, last_name, requests, usage_date = row

    today = time.strftime("%Y-%m-%d")

    if usage_date != today:
        with sqlite3.connect(DB_PATH) as db:
            db.execute("""
                UPDATE users
                SET requests_today=0,
                    usage_date=?
                WHERE telegram_id=?
            """, (today, telegram_id))

            db.commit()

        requests = 0

    return {
        "telegram_id": telegram_id,
        "username": username or "",
        "first_name": first_name or "",
        "last_name": last_name or "",
        "requests_today": requests or 0,
    }


def consume_request(telegram_id):
    today = time.strftime("%Y-%m-%d")

    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("""
            SELECT requests_today, usage_date
            FROM users
            WHERE telegram_id=?
        """, (telegram_id,)).fetchone()

        if not row:
            return False

        requests, usage_date = row

        if usage_date != today:
            requests = 0

        if requests >= DAILY_LIMIT:
            return False

        db.execute("""
            UPDATE users
            SET requests_today=?,
                usage_date=?
            WHERE telegram_id=?
        """, (
            requests + 1,
            today,
            telegram_id
        ))

        db.commit()

    return True


# =========================================================
# WEB LOGIN TOKENS
# =========================================================

LOGIN_TOKEN_TTL = 300  # 5 daqiqa


def create_login_token(telegram_id=0, one_time_api_key=None):
    """Website login uchun bir martalik token yaratadi."""
    token = "YIXLOGIN_" + secrets.token_urlsafe(32)
    token_hash = hash_value(token)

    now = int(time.time())
    expires = now + LOGIN_TOKEN_TTL

    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "DELETE FROM login_tokens WHERE expires_at <= ?",
            (now,)
        )

        db.execute("""
            INSERT INTO login_tokens
            (
                token_hash,
                telegram_id,
                created_at,
                expires_at,
                used,
                one_time_api_key
            )
            VALUES (?, ?, ?, ?, 0, ?)
        """, (
            token_hash,
            int(telegram_id),
            now,
            expires,
            one_time_api_key
        ))

        db.commit()

    return token


def get_login_token(token):
    """Login token holatini o'qiydi, lekin ishlatib yubormaydi."""
    if not token:
        return None

    token_hash = hash_value(token)
    now = int(time.time())

    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("""
            SELECT telegram_id, used
            FROM login_tokens
            WHERE token_hash=?
              AND expires_at>?
        """, (
            token_hash,
            now
        )).fetchone()

    if not row:
        return None

    return {
        "telegram_id": int(row[0]),
        "used": bool(row[1])
    }


def bind_login_token(token, telegram_id):
    """Website yaratgan tokenni Telegram akkauntiga bog'laydi."""
    if not token:
        return False

    token_hash = hash_value(token)
    now = int(time.time())

    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("""
            SELECT used
            FROM login_tokens
            WHERE token_hash=?
              AND expires_at>?
        """, (
            token_hash,
            now
        )).fetchone()

        if not row or row[0]:
            return False

        db.execute("""
            UPDATE login_tokens
            SET telegram_id=?
            WHERE token_hash=?
        """, (
            int(telegram_id),
            token_hash
        ))

        db.commit()

    return True


def consume_login_token(token):
    """Login tokenni atomik ravishda bir marta ishlatadi."""
    if not token:
        return None

    token_hash = hash_value(token)
    now = int(time.time())

    with sqlite3.connect(DB_PATH) as db:
        db.execute("BEGIN IMMEDIATE")

        row = db.execute("""
            SELECT telegram_id, one_time_api_key
            FROM login_tokens
            WHERE token_hash=?
              AND expires_at>?
              AND used=0
              AND telegram_id>0
        """, (
            token_hash,
            now
        )).fetchone()

        if not row:
            db.rollback()
            return None

        telegram_id = int(row[0])
        one_time_api_key = row[1]

        result = db.execute("""
            UPDATE login_tokens
            SET used=1
            WHERE token_hash=?
              AND used=0
              AND expires_at>?
        """, (
            token_hash,
            now
        ))

        if result.rowcount != 1:
            db.rollback()
            return None

        db.commit()

    return {
        "telegram_id": telegram_id,
        "one_time_api_key": one_time_api_key
    }

def update_phone(telegram_id, phone_number):
    """Foydalanuvchi o'zi yuborgan Telegram kontaktini saqlaydi."""
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""
            UPDATE users
            SET phone_number=?,
                phone_verified=1
            WHERE telegram_id=?
        """, (
            phone_number,
            int(telegram_id)
        ))

        db.commit()


def get_phone_status(telegram_id):
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("""
            SELECT phone_number, phone_verified
            FROM users
            WHERE telegram_id=?
        """, (int(telegram_id),)).fetchone()

    if not row:
        return {
            "phone_number": "",
            "phone_verified": False
        }

    return {
        "phone_number": row[0] or "",
        "phone_verified": bool(row[1])
    }

