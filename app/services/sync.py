import asyncio
import traceback

from app.news.rss import fetch_all_news
from app.database.db import save_news


_sync_lock = asyncio.Lock()


async def sync_news():
    """
    Yangiliklarni yuklash va bazaga saqlash.

    Bir vaqtning o'zida faqat bitta sync ishlaydi.
    Xatolar Telegram bot pollingiga ta'sir qilmaydi.
    """

    if _sync_lock.locked():
        print("⏳ Sync allaqachon ishlayapti. O'tkazib yuborildi.")
        return

    async with _sync_lock:
        print("🔄 NEWS SYNC BOSHLANDI...")

        try:
            # RSS yig'ish maksimum 90 soniya.
            news_items = await asyncio.wait_for(
                fetch_all_news(),
                timeout=90
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
                    print("⏰ Bitta yangilikni saqlash timeout bo'ldi.")

                except Exception as e:
                    print(f"⚠️ Yangilikni saqlash xatosi: {e}")

            print(
                f"✅ SYNC TUGADI | "
                f"Topildi: {len(news_items)} | "
                f"Saqlandi: {saved_count}"
            )

        except asyncio.TimeoutError:
            print("⏰ NEWS SYNC 90 soniyadan oshdi.")

        except asyncio.CancelledError:
            print("🛑 NEWS SYNC bekor qilindi.")
            raise

        except Exception as e:
            print(f"❌ NEWS SYNC XATOSI: {e}")
            traceback.print_exc()


async def _run_sync_safely():
    """
    Sync task xatosi auto_sync loopini o'ldirmasligi uchun wrapper.
    """

    try:
        await sync_news()

    except asyncio.CancelledError:
        raise

    except Exception as e:
        print(f"❌ Background sync xatosi: {e}")
        traceback.print_exc()


async def auto_sync(interval: int = 600):
    """
    Har 10 daqiqada yangiliklarni yangilaydi.

    Muhim:
    auto_sync Telegram pollingni kutib turmaydi.
    Sync xatosi botni to'xtatmaydi.
    """

    print(
        f"⏱️ AUTO SYNC ISHLADI | "
        f"Interval: {interval} sekund"
    )

    while True:
        try:
            await asyncio.sleep(interval)

            print("⏰ 10 daqiqa o'tdi. Yangi sync boshlanmoqda...")

            # Alohida task.
            task = asyncio.create_task(
                _run_sync_safely()
            )

            # Task exceptionlari yo'qolib ketmasligi uchun
            # done callback qo'yamiz.
            task.add_done_callback(
                _background_task_done
            )

        except asyncio.CancelledError:
            print("🛑 AUTO SYNC TO'XTATILDI.")
            raise

        except Exception as e:
            print(f"❌ AUTO SYNC LOOP XATOSI: {e}")

            # Loop o'lmasligi uchun.
            await asyncio.sleep(5)


def _background_task_done(task):
    """
    Background task tugaganda xatoni logga chiqaradi.
    """

    try:
        task.result()

    except asyncio.CancelledError:
        pass

    except Exception as e:
        print(f"❌ Background task exception: {e}")
