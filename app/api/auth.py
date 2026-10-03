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
