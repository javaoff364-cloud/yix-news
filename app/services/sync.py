import asyncio
import traceback

from app.news.rss import get_latest_news
from app.database.db import save_news


_sync_lock = asyncio.Lock()


async def sync_news():
    """
    Yangiliklarni yuklaydi va bazaga saqlaydi.
    Bir vaqtning o'zida faqat bitta sync ishlaydi.
    """

    if _sync_lock.locked():
        print("⏳ Sync allaqachon ishlayapti. Bu sync o'tkazib yuborildi.")
        return

    async with _sync_lock:
        print("🔄 NEWS SYNC BOSHLANDI...")

        try:
            news_items = await asyncio.wait_for(
                get_latest_news(),
                timeout=120
            )

            if not news_items:
                print("ℹ️ Yangi yangilik topilmadi.")
                return

            print(f"📰 {len(news_items)} ta yangilik olindi.")

            saved_count = 0

            for news in news_items:
                try:
                    result = await asyncio.wait_for(
                        save_news(news),
                        timeout=10
                    )

                    if result:
                        saved_count += 1

                except asyncio.TimeoutError:
                    print("⏰ Yangilikni bazaga saqlash timeout bo'ldi.")

                except Exception as e:
                    print(f"⚠️ Yangilikni saqlashda xato: {e}")

            print(
                f"✅ NEWS SYNC TUGADI | "
                f"Topildi: {len(news_items)} | "
                f"Saqlandi: {saved_count}"
            )

        except asyncio.TimeoutError:
            print("⏰ NEWS SYNC 120 sekunddan oshdi. To'xtatildi.")

        except asyncio.CancelledError:
            print("🛑 NEWS SYNC bekor qilindi.")
            raise

        except Exception as e:
            print(f"❌ NEWS SYNC XATOSI: {e}")
            traceback.print_exc()


async def _safe_sync():
    """
    Background sync xato bersa ham asosiy bot ishlashda davom etadi.
    """

    try:
        await sync_news()

    except asyncio.CancelledError:
        raise

    except Exception as e:
        print(f"❌ Background sync exception: {e}")
        traceback.print_exc()


def _sync_task_done(task):
    """
    Background task ichidagi exceptionni ushlab qoladi.
    """

    try:
        task.result()

    except asyncio.CancelledError:
        pass

    except Exception as e:
        print(f"❌ Background sync task xatosi: {e}")


async def auto_sync(interval: int = 600):
    """
    Har 10 daqiqada yangiliklarni yangilaydi.
    """

    print(
        f"⏱️ AUTO SYNC ACTIVE | "
        f"Har {interval} sekundda"
    )

    while True:
        try:
            await asyncio.sleep(interval)

            print(
                "⏰ Interval tugadi. "
                "Auto sync boshlanmoqda..."
            )

            task = asyncio.create_task(
                _safe_sync()
            )

            task.add_done_callback(
                _sync_task_done
            )

        except asyncio.CancelledError:
            print("🛑 AUTO SYNC TO'XTATILDI.")
            raise

        except Exception as e:
            print(f"❌ AUTO SYNC LOOP XATOSI: {e}")

            await asyncio.sleep(5)
