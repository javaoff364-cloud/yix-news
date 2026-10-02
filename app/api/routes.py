from aiohttp import web

from app.api.auth import (
    init_api_db,
    verify_telegram,
    create_or_update_user,
    get_user_by_key,
    regenerate_key,
    consume_request,
    DAILY_LIMIT,
)
from app.config import BOT_TOKEN
from app.database.db import get_news


def get_api_key(request):
    header = request.headers.get("Authorization", "")

    if not header.startswith("Bearer "):
        return None

    return header[7:].strip()


def auth_user(request):
    key = get_api_key(request)

    if not key:
        return None

    return get_user_by_key(key)


async def api_telegram_login(request):
    try:
        data = await request.json()

        raw = data.get("auth")

        if not raw:
            return web.json_response(
                {"success": False, "error": "Telegram auth data required"},
                status=400
            )

        if isinstance(raw, dict):
            from urllib.parse import urlencode
            raw = urlencode(raw)

        telegram = verify_telegram(raw, BOT_TOKEN)

        if not telegram:
            return web.json_response(
                {"success": False, "error": "Invalid Telegram authentication"},
                status=401
            )

        api_key = create_or_update_user(telegram)

        if api_key is None:
            user = get_user_by_key_from_telegram(
                int(telegram["id"])
            )

            return web.json_response({
                "success": True,
                "existing": True,
                "message": "Account already exists. Use your existing API key or regenerate it.",
                "user": user
            })

        return web.json_response({
            "success": True,
            "existing": False,
            "api_key": api_key,
            "limit": DAILY_LIMIT,
            "user": {
                "telegram_id": int(telegram["id"]),
                "username": telegram.get("username", ""),
                "first_name": telegram.get("first_name", "")
            }
        })

    except Exception as e:
        print("API LOGIN ERROR:", e)

        return web.json_response(
            {"success": False, "error": "Authentication error"},
            status=500
        )


def get_user_by_telegram(telegram_id):
    import sqlite3

    with sqlite3.connect("api_users.db") as db:
        row = db.execute("""
            SELECT telegram_id, username, first_name,
                   last_name, requests_today
            FROM users
            WHERE telegram_id=?
        """, (telegram_id,)).fetchone()

    if not row:
        return None

    return {
        "telegram_id": row[0],
        "username": row[1],
        "first_name": row[2],
        "last_name": row[3],
        "requests_today": row[4],
        "daily_limit": DAILY_LIMIT,
    }


get_user_by_telegram = get_user_by_telegram


async def api_me(request):
    user = auth_user(request)

    if not user:
        return web.json_response(
            {"success": False, "error": "Unauthorized"},
            status=401
        )

    return web.json_response({
        "success": True,
        "user": user,
        "daily_limit": DAILY_LIMIT,
        "remaining": max(
            DAILY_LIMIT - user["requests_today"],
            0
        )
    })


async def api_regenerate(request):
    user = auth_user(request)

    if not user:
        return web.json_response(
            {"success": False, "error": "Unauthorized"},
            status=401
        )

    new_key = regenerate_key(user["telegram_id"])

    return web.json_response({
        "success": True,
        "api_key": new_key,
        "warning": "Save this API key. It will not be shown again."
    })


async def protected_news(request):
    user = auth_user(request)

    if not user:
        return web.json_response(
            {
                "success": False,
                "error": "API key required",
                "message": "Use Authorization: Bearer YIX_xxx"
            },
            status=401
        )

    if not consume_request(user["telegram_id"]):
        return web.json_response(
            {
                "success": False,
                "error": "Daily API limit reached",
                "limit": DAILY_LIMIT
            },
            status=429
        )

    try:
        category = request.query.get("category")

        limit = min(
            int(request.query.get("limit", "30")),
            100
        )

        offset = max(
            int(request.query.get("offset", "0")),
            0
        )

        news = await get_news(
            category=category,
            limit=limit,
            offset=offset
        )

        return web.json_response({
            "success": True,
            "count": len(news),
            "limit": limit,
            "offset": offset,
            "news": news
        })

    except Exception as e:
        print("PROTECTED API ERROR:", e)

        return web.json_response(
            {
                "success": False,
                "error": "News API error"
            },
            status=500
        )


async def site_news(request):
    try:
        category = request.query.get("category")

        limit = min(
            int(request.query.get("limit", "50")),
            100
        )

        offset = max(
            int(request.query.get("offset", "0")),
            0
        )

        news = await get_news(
            category=category,
            limit=limit,
            offset=offset
        )

        return web.json_response({
            "success": True,
            "count": len(news),
            "news": news
        })

    except Exception:
        return web.json_response(
            {
                "success": False,
                "error": "News API error"
            },
            status=500
        )


def setup_api(app):
    init_api_db()

    app.router.add_post(
        "/api/auth/telegram",
        api_telegram_login
    )

    app.router.add_get(
        "/api/me",
        api_me
    )

    app.router.add_post(
        "/api/key/regenerate",
        api_regenerate
    )

    app.router.add_get(
        "/api/news",
        protected_news
    )

    app.router.add_get(
        "/api/site-news",
        site_news
    )

    print("🔑 API authentication enabled")
