import asyncio

from aiogram import Bot, Dispatcher

from app.config import BOT_TOKEN
from app.database.db import init_db
from app.handlers import router
from app.services.sync import sync_news, auto_sync
from app.services.channel_post import channel_auto_post


async def main():
    await init_db()

    await sync_news()

    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(router)

    sync_task = asyncio.create_task(
        auto_sync(600)
    )

    channel_task = asyncio.create_task(
        channel_auto_post(bot)
    )

    try:
        await bot.delete_webhook(drop_pending_updates=True)

        print("🚀 YIX NEWS BOT ISHLAYAPTI...")

        await dp.start_polling(bot)

    finally:
        sync_task.cancel()
        channel_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
