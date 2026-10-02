import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qsl

DB_PATH = Path("api_users.db")

DAILY_LIMIT = 1000


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
        db.commit()


def hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()


def generate_key():
    return "YIX_" + secrets.token_urlsafe(32)


def verify_telegram(data, bot_token):
    try:
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

        if time.time() - auth_date > 86400:
            return None

        return auth_data

    except Exception:
        return None


def create_or_update_user(data):
    telegram_id = int(data["id"])

    with sqlite3.connect(DB_PATH) as db:
        row = db.execute(
            "SELECT api_key_hash FROM users WHERE telegram_id=?",
            (telegram_id,)
        ).fetchone()

        if row:
            return None

        api_key = generate_key()

        db.execute("""
            INSERT INTO users
            (telegram_id, username, first_name, last_name,
             api_key_hash, created_at, requests_today, usage_date)
            VALUES (?, ?, ?, ?, ?, ?, 0, date('now'))
        """, (
            telegram_id,
            data.get("username", ""),
            data.get("first_name", ""),
            data.get("last_name", ""),
            hash_key(api_key),
            int(time.time())
        ))

        db.commit()

    return api_key


def regenerate_key(telegram_id):
    api_key = generate_key()

    with sqlite3.connect(DB_PATH) as db:
        db.execute(
            "UPDATE users SET api_key_hash=? WHERE telegram_id=?",
            (hash_key(api_key), telegram_id)
        )
        db.commit()

    return api_key


def get_user_by_key(api_key):
    key_hash = hash_key(api_key)

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
                SET requests_today=0, usage_date=?
                WHERE telegram_id=?
            """, (today, telegram_id))
            db.commit()

        requests = 0

    return {
        "telegram_id": telegram_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "requests_today": requests,
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
