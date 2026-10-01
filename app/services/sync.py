import asyncio
from app.news.rss import get_latest_news
from app.database.db import save_news_bulk


async def sync_news():
    print("\n🔄 YIX NEWS: yangiliklar yangilanmoqda...")

    try:
        articles = await get_latest_news()

        if not articles:
            print("⚠️ Hech qanday yangilik topilmadi")
            return 0

        added = await save_news_bulk(articles)

        print(
            f"✅ RSS: {len(articles)} ta maqola olindi"
        )

        print(
            f"💾 Database: {added} ta yangi maqola qo‘shildi"
        )

        print(
            f"♻️ Duplicate: "
            f"{len(articles) - added} ta mavjud"
        )

        return added

    except Exception as e:
        print(f"❌ Sync xatosi: {e}")
        return 0


async def auto_sync(interval=600):
    """
    Har 10 daqiqada yangiliklarni yangilaydi.
    """

    while True:

        try:
            await sync_news()

        except Exception as e:
            print(f"❌ Auto sync xatosi: {e}")

        print(
            f"⏳ Keyingi yangilanish: "
            f"{interval // 60} daqiqadan keyin"
        )

        await asyncio.sleep(interval)
