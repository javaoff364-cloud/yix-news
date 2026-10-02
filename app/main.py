import asyncio
import os

from aiohttp import web
from aiogram import Bot, Dispatcher

from app.config import BOT_TOKEN
from app.database.db import init_db, get_news
from app.handlers import router
from app.services.sync import sync_news, auto_sync
from app.services.channel_post import channel_auto_post


async def health(request):
    return web.Response(text="YIX News is running")


async def api_news(request):
    try:
        category = request.query.get("category")
        limit = min(int(request.query.get("limit", "30")), 50)
        offset = max(int(request.query.get("offset", "0")), 0)

        news = await get_news(
            category=category,
            limit=limit,
            offset=offset,
        )

        return web.json_response(
            {
                "success": True,
                "count": len(news),
                "news": news,
            },
            headers={
                "Access-Control-Allow-Origin": "*"
            }
        )

    except Exception as e:
        print(f"❌ API NEWS ERROR: {e}")

        return web.json_response(
            {
                "success": False,
                "error": "News API error"
            },
            status=500,
            headers={
                "Access-Control-Allow-Origin": "*"
            }
        )


async def start_web_server():
    port = int(os.getenv("PORT", "10000"))

    app = web.Application()

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/api/news", api_news)

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    print(f"🌐 Web server: 0.0.0.0:{port}")

    return runner


async def run_initial_sync():
    """
    Bot ishga tushgandan keyin yangiliklarni
    background'da yuklaydi.
    """

    try:
        print("🔄 Initial news sync background'da boshlandi...")

        await sync_news()

        print("✅ Initial news sync tugadi.")

    except asyncio.CancelledError:
        print("🛑 Initial sync bekor qilindi.")
        raise

    except Exception as e:
        print(f"⚠️ Initial sync xatosi: {e}")


async def main():
    print("🚀 YIX NEWS STARTING...")

    # Database
    await init_db()

    print("✅ Database initialized")

    # Render web server
    web_runner = await start_web_server()

    # Telegram bot
    bot = Bot(BOT_TOKEN)

    dp = Dispatcher()

    dp.include_router(router)

    # -------------------------------------------------
    # BACKGROUND TASKS
    # -------------------------------------------------

    initial_sync_task = asyncio.create_task(
        run_initial_sync()
    )

    sync_task = asyncio.create_task(
        auto_sync(600)
    )

    channel_task = asyncio.create_task(
        channel_auto_post(bot)
    )

    try:
        await bot.delete_webhook(
            drop_pending_updates=True
        )

        print("🤖 YIX NEWS BOT ISHLAYAPTI!")
        print("📡 Telegram polling started")
        print("⏱️ Auto sync: har 10 daqiqada")
        print("📢 Channel auto-post: ACTIVE")

        # Bot polling asosiy jarayon bo'ladi.
        # Background tasklar unga xalaqit bermaydi.
        await dp.start_polling(bot)

    except Exception as e:
        print(f"❌ BOT POLLING XATOSI: {e}")

        raise

    finally:
        print("🛑 YIX NEWS SHUTDOWN...")

        for task in (
            initial_sync_task,
            sync_task,
            channel_task,
        ):
            task.cancel()

        await asyncio.gather(
            initial_sync_task,
            sync_task,
            channel_task,
            return_exceptions=True
        )

        await web_runner.cleanup()

        await bot.session.close()

        print("✅ YIX NEWS SHUTDOWN COMPLETE")


if __name__ == "__main__":
    asyncio.run(main())
