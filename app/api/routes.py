from aiohttp import web

from app.api.auth import (
    init_api_db,
    verify_telegram,
    create_user,
    create_session,
    create_login_token,
    consume_login_token,
    get_phone_status,
    get_user_by_session,
    get_user_by_telegram,
    get_user_by_key,
    regenerate_key,
    consume_request,
    DAILY_LIMIT,
)
from app.config import BOT_TOKEN
from app.database.db import get_news


ALLOWED_ORIGIN = "https://yix-news-web.vercel.app"


@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)

    response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    response.headers["Access-Control-Allow-Headers"] = (
        "Authorization, Content-Type, X-Session-Token"
    )
    response.headers["Access-Control-Allow-Methods"] = (
        "GET, POST, OPTIONS"
    )

    return response


def get_api_key(request):
    header = request.headers.get("Authorization", "")

    if not header.startswith("Bearer "):
        return None

    return header[7:].strip()


def get_session_token(request):
    header = request.headers.get("X-Session-Token", "")

    if header:
        return header.strip()

    return None


def auth_user(request):
    key = get_api_key(request)

    if not key:
        return None

    return get_user_by_key(key)


def session_user(request):
    token = get_session_token(request)

    if not token:
        return None

    return get_user_by_session(token)


async def api_telegram_login(request):
    try:
        data = await request.json()
        raw = data.get("auth")

        if not raw:
            return web.json_response(
                {
                    "success": False,
                    "error": "Telegram auth data required"
                },
                status=400
            )

        telegram = verify_telegram(raw, BOT_TOKEN)

        if not telegram:
            return web.json_response(
                {
                    "success": False,
                    "error": "Invalid or expired Telegram authentication"
                },
                status=401
            )

        telegram_id = int(telegram["id"])

        existing_user = get_user_by_telegram(telegram_id)

        new_account = False
        api_key = None

        if not existing_user:
            api_key = create_user(telegram)
            new_account = True

        session_token = create_session(telegram_id)

        user = get_user_by_telegram(telegram_id)

        response = {
            "success": True,
            "new_account": new_account,
            "session_token": session_token,
            "expires_in": 86400,
            "limit": DAILY_LIMIT,
            "user": user,
        }

        if api_key:
            response["api_key"] = api_key
            response["api_key_message"] = (
                "Save this API key. "
                "It will not be shown again unless regenerated."
            )

        return web.json_response(response)

    except Exception as e:
        print("API LOGIN ERROR:", e)

        return web.json_response(
            {
                "success": False,
                "error": "Authentication error"
            },
            status=500
        )


async def api_session_me(request):
    user = session_user(request)

    if not user:
        return web.json_response(
            {
                "success": False,
                "error": "Session expired or unauthorized"
            },
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
    user = session_user(request)

    if not user:
        return web.json_response(
            {
                "success": False,
                "error": "Session expired or unauthorized"
            },
            status=401
        )

    new_key = regenerate_key(user["telegram_id"])

    return web.json_response({
        "success": True,
        "api_key": new_key,
        "warning": (
            "Save this API key. "
            "The previous API key is now invalid."
        )
    })


async def api_me(request):
    user = auth_user(request)

    if not user:
        return web.json_response(
            {
                "success": False,
                "error": "Unauthorized"
            },
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


async def protected_news(request):
    user = auth_user(request)

    if not user:
        return web.json_response(
            {
                "success": False,
                "error": "API key required",
                "message": (
                    "Use Authorization: Bearer YIX_xxx"
                )
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

    except Exception as e:
        print("SITE NEWS ERROR:", e)

        return web.json_response(
            {
                "success": False,
                "error": "News API error"
            },
            status=500
        )


# =========================================================
# WEB -> TELEGRAM LOGIN
# =========================================================

async def api_auth_start(request):
    """
    Website login tugmasi bosilganda bir martalik
    Telegram login token yaratadi.
    """
    try:
        token = create_login_token(0)

        telegram_url = (
            "https://t.me/YIXNewsBot?start="
            + token
        )

        response = web.json_response({
            "success": True,
            "login_token": token,
            "telegram_url": telegram_url,
            "expires_in": 300
        })

        response.headers["Cache-Control"] = "no-store"
        return response

    except Exception as e:
        print("WEB LOGIN START ERROR:", e)

        return web.json_response({
            "success": False,
            "error": "Login boshlanmadi"
        }, status=500)


async def api_auth_complete(request):
    """
    Telegram bot telefon tasdiqlagandan keyin yuborgan
    bir martalik tokenni website session'iga aylantiradi.
    """
    try:
        data = await request.json()
        token = data.get("token")

        if not token:
            return web.json_response({
                "success": False,
                "error": "Login token required"
            }, status=400)

        telegram_id = consume_login_token(token)

        if not telegram_id:
            return web.json_response({
                "success": False,
                "error": "Token expired or already used"
            }, status=401)

        user = get_user_by_telegram(telegram_id)

        if not user:
            return web.json_response({
                "success": False,
                "error": "Account not found"
            }, status=404)

        session_token = create_session(telegram_id)

        phone = get_phone_status(telegram_id)

        response = {
            "success": True,
            "session_token": session_token,
            "expires_in": 86400,
            "user": {
                **user,
                "phone_number": phone["phone_number"],
                "phone_verified": phone["phone_verified"]
            },
            "daily_limit": DAILY_LIMIT,
            "remaining": max(
                DAILY_LIMIT - user["requests_today"],
                0
            )
        }

        response_obj = web.json_response(response)
        response_obj.headers["Cache-Control"] = "no-store"

        return response_obj

    except Exception as e:
        print("WEB LOGIN COMPLETE ERROR:", e)

        return web.json_response({
            "success": False,
            "error": "Login yakunlanmadi"
        }, status=500)



def setup_api(app):
    init_api_db()

    if cors_middleware not in app.middlewares:
        app.middlewares.append(cors_middleware)

    app.router.add_post(
        "/api/auth/start",
        api_auth_start
    )

    app.router.add_post(
        "/api/auth/complete",
        api_auth_complete
    )

    app.router.add_post(
        "/api/auth/telegram",
        api_telegram_login
    )

    app.router.add_get(
        "/api/session/me",
        api_session_me
    )

    app.router.add_post(
        "/api/session/regenerate",
        api_regenerate
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
