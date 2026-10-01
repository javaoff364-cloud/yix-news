import asyncio
from datetime import datetime

from aiogram import Bot
from aiogram.types import BufferedInputFile

from app.database.db import (
    get_active_channels,
    get_next_unposted_news,
    mark_news_posted,
)


POST_INTERVALS = {
    300: "5 daqiqa",
    600: "10 daqiqa",
    1800: "30 daqiqa",
    3600: "1 soat",
    10800: "3 soat",
    21600: "6 soat",
    43200: "12 soat",
    86400: "24 soat",
}


def format_channel_post(article: dict) -> str:
    title = article.get("title", "Yangilik")
    summary = article.get("summary", "")
    source = article.get("source", "Noma'lum")
    link = article.get("link", "")

    if len(summary) > 700:
        summary = summary[:700].rsplit(" ", 1)[0] + "..."

    text = f"📰 <b>{title}</b>\n\n"

    if summary:
        text += f"{summary}\n\n"

    text += f"📡 <b>Manba:</b> {source}\n"

    if link:
        text += f'\n🔗 <a href="{link}">Batafsil o‘qish</a>'

    return text


async def post_to_channel(bot: Bot, channel, article):
    channel_id = channel["channel_id"]

    try:
        image = article.get("image")
        caption = format_channel_post(article)

        if image:
            try:
                await bot.send_photo(
                    chat_id=channel_id,
                    photo=image,
                    caption=caption,
                    parse_mode="HTML",
                )
            except Exception:
                await bot.send_message(
                    chat_id=channel_id,
                    text=caption,
                    parse_mode="HTML",
                    disable_web_page_preview=False,
                )
        else:
            await bot.send_message(
                chat_id=channel_id,
                text=caption,
                parse_mode="HTML",
                disable_web_page_preview=False,
            )

        await mark_news_posted(
            channel_id=channel_id,
            news_id=article["id"],
        )

        print(
            f"📢 Auto-post: {channel_id} → "
            f"{article['title'][:60]}"
        )

        return True

    except Exception as e:
        print(
            f"❌ Kanal post xatosi "
            f"{channel_id}: {e}"
        )
        return False


async def channel_auto_post(bot: Bot):
    """
    Barcha aktiv kanallarni tekshiradi.
    Har bir kanal o'z intervaliga ko'ra yangilik oladi.
    """

    while True:
        try:
            channels = await get_active_channels()

            now = datetime.utcnow()

            for channel in channels:
                last_post = channel.get("last_post_at")

                interval = channel.get(
                    "interval",
                    1800,
                )

                if last_post:
                    elapsed = (
                        now - last_post
                    ).total_seconds()

                    if elapsed < interval:
                        continue

                article = await get_next_unposted_news(
                    channel["channel_id"]
                )

                if not article:
                    continue

                success = await post_to_channel(
                    bot,
                    channel,
                    article,
                )

                if success:
                    await asyncio.sleep(1)

        except Exception as e:
            print(f"❌ Channel auto-post xatosi: {e}")

        await asyncio.sleep(30)
