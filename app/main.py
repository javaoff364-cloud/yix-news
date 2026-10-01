import asyncio
import os
from aiohttp import web

from aiogram import Bot, Dispatcher

from app.config import BOT_TOKEN
from app.database.db import init_db
from app.handlers import router
from app.services.sync import sync_news, auto_sync
from app.services.channel_post import channel_auto_post


async def health(request):
    return web.Response(text="YIX News is running")


async def start_web_server():
    port = int(os.getenv("PORT", "10000"))

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port,
    )

    await site.start()

    print(f"🌐 Web server: http://0.0.0.0:{port}")

    return runner


async def main():
    print("🚀 YIX NEWS STARTING...")

    await init_db()
    print("✅ Database initialized")

    # Web server — Render uchun
    web_runner = await start_web_server()

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(router)

    print("🔄 Initial news sync...")
    try:
        await sync_news()
        print("✅ Initial news sync completed")
    except Exception as e:
        print(f"⚠️ Initial sync error: {e}")

    sync_task = asyncio.create_task(
        auto_sync(600)
    )

    channel_task = asyncio.create_task(
        channel_auto_post(bot)
    )

    try:
        await bot.delete_webhook(drop_pending_updates=True)

        print("🤖 YIX NEWS BOT ISHLAYAPTI!")
        print("📡 Telegram polling started")

        await dp.start_polling(bot)

    finally:
        sync_task.cancel()
        channel_task.cancel()

        await web_runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
