import re
import aiohttp

from urllib.parse import urlparse, parse_qs, unquote
from io import BytesIO

from PIL import Image

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    BufferedInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    KeyboardButton,
    KeyboardButtonRequestChat,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from app.config import ADMIN_IDS
from app.database.db import (
    get_news,
    search_news,
    save_article,
    remove_article,
    is_saved,
    get_saved_articles,
    get_setting,
    toggle_notifications,
    set_default_category,
    get_news_count,
    add_user_channel,
    get_user_channels,
    set_channel_interval,
    set_channel_enabled,
    remove_user_channel,
)
from app.services.sync import sync_news
from app.news.sources import SOURCES


router = Router()

user_news_state = {}
search_users = set()

SEARCH_PAGE_SIZE = 10


CATEGORIES = {
    "uzbekistan": "🇺🇿 O‘zbekiston",
    "world": "🌍 Dunyo",
    "technology": "💻 Texnologiya",
    "sport": "⚽ Sport",
    "economy": "💰 Iqtisodiyot",
    "science": "🔬 Fan",
    "gaming": "🎮 Gaming",
    "cinema": "🎬 Kino",
}


# =========================================================
# HELPERS
# =========================================================

def shorten(text, limit=500):
    if not text:
        return ""

    text = re.sub(r"\s+", " ", str(text)).strip()

    if len(text) <= limit:
        return text

    return text[:limit - 3].rstrip() + "..."


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


async def safe_delete(message):
    try:
        await message.delete()
    except Exception as e:
        print(f"⚠️ Message delete skipped: {e}")


async def safe_callback_answer(callback, text=None, show_alert=False):
    try:
        if text:
            await callback.answer(text, show_alert=show_alert)
        else:
            await callback.answer()
    except Exception as e:
        print(f"⚠️ Telegram callback xatosi: {e}")


def normalize_query(query):
    query = (query or "").strip().lower()

    query = re.sub(r"[^\w\s'-]", " ", query, flags=re.UNICODE)
    query = re.sub(r"\s+", " ", query)

    return query.strip()


def query_words(query):
    query = normalize_query(query)

    words = query.split()

    # Juda qisqa va foydasiz so'zlarni kamaytiramiz.
    stop_words = {
        "va", "ham", "bu", "shu", "uchun",
        "bilan", "bir", "the", "a", "an",
        "of", "to", "in", "on", "is", "are",
    }

    return [
        word for word in words
        if len(word) >= 2 and word not in stop_words
    ]


def article_search_score(article, words):
    """
    Qidiruv natijasining moslik ballini hisoblaydi.

    Sarlavha > manba > summary > content
    """

    title = str(article.get("title", "")).lower()
    source = str(article.get("source", "")).lower()
    summary = str(article.get("summary", "")).lower()
    content = str(
        article.get("content", article.get("description", ""))
    ).lower()

    score = 0

    for word in words:

        # Sarlavha eng katta og'irlikka ega
        if word in title:
            score += 100

            # So'z sarlavhaning boshida bo'lsa qo'shimcha ball
            if title.startswith(word):
                score += 30

        # Manba
        if word in source:
            score += 35

        # Summary
        if word in summary:
            score += 20

        # Content
        if word in content:
            score += 5

    # Barcha so'zlar sarlavhada bo'lsa
    if words and all(word in title for word in words):
        score += 100

    return score


def rank_search_results(articles, query):
    words = query_words(query)

    if not words:
        return articles

    scored = []

    for article in articles:
        score = article_search_score(article, words)

        if score > 0:
            scored.append((score, article))

    scored.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return [article for _, article in scored]


def main_menu(user_id=None):
    buttons = [
        [
            InlineKeyboardButton(
                text="📰 So‘nggi yangiliklar",
                callback_data="latest"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔥 Muhim yangiliklar",
                callback_data="important"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔎 Kuchli qidiruv",
                callback_data="search"
            )
        ],
        [
            InlineKeyboardButton(
                text="📂 Kategoriyalar",
                callback_data="categories"
            )
        ],
        [
            InlineKeyboardButton(
                text="💾 Saqlanganlar",
                callback_data="saved"
            )
        ],
        [
            InlineKeyboardButton(
                text="📢 Kanallar",
                callback_data="channel_auto"
            )
        ],
        [
            InlineKeyboardButton(
                text="⚙️ Sozlamalar",
                callback_data="settings"
            )
        ],
    ]

    if user_id in ADMIN_IDS:
        buttons.append([
            InlineKeyboardButton(
                text="👑 Admin panel",
                callback_data="admin"
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def categories_keyboard():
    rows = []

    for key, name in CATEGORIES.items():
        rows.append([
            InlineKeyboardButton(
                text=name,
                callback_data=f"cat:{key}"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="back"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def search_keyboard(page, total_pages):
    rows = []

    nav = []

    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"searchpage:{page - 1}"
            )
        )

    nav.append(
        InlineKeyboardButton(
            text=f"{page + 1}/{max(total_pages, 1)}",
            callback_data="noop"
        )
    )

    if page < total_pages - 1:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"searchpage:{page + 1}"
            )
        )

    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton(
            text="🔎 Yangi qidiruv",
            callback_data="search"
        )
    ])

    rows.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="back"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def news_keyboard(state_key, index, total, saved=False):
    rows = []

    nav = []

    if index > 0:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"nav:prev:{state_key}:{index}"
            )
        )

    nav.append(
        InlineKeyboardButton(
            text=f"{index + 1}/{total}",
            callback_data="noop"
        )
    )

    if index < total - 1:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"nav:next:{state_key}:{index}"
            )
        )

    rows.append(nav)

    article = user_news_state.get(state_key, {}).get("articles", [])

    if article and 0 <= index < len(article):
        link = article[index].get("link")

        if link:
            rows.append([
                InlineKeyboardButton(
                    text="🔗 Manbani ochish",
                    url=link
                )
            ])

    rows.append([
        InlineKeyboardButton(
            text="💾 Saqlangan" if saved else "💾 Saqlash",
            callback_data=(
                f"unsave:{state_key}:{index}"
                if saved
                else f"save:{state_key}:{index}"
            )
        )
    ])

    rows.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="back"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def article_caption(article):
    title = article.get("title", "Yangilik")
    source = article.get("source", "Noma'lum")
    summary = shorten(article.get("summary", ""), 500)

    text = f"📰 *{title}*\n\n"

    if summary:
        text += f"{summary}\n\n"

    text += f"📡 *Manba:* {source}"

    return text


def unwrap_image_url(url):
    if not url:
        return None

    try:
        parsed = urlparse(url)

        if parsed.path.endswith("/_next/image"):
            query = parse_qs(parsed.query)
            original = query.get("url")

            if original:
                return unquote(original[0])

    except Exception:
        pass

    return url


async def download_image(url):
    if not url:
        return None

    urls = []

    original = unwrap_image_url(url)

    if original:
        urls.append(original)

    if url not in urls:
        urls.append(url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/140.0 Mobile Safari/537.36"
        ),
        "Accept": (
            "image/avif,image/webp,image/apng,"
            "image/*,*/*;q=0.8"
        ),
    }

    timeout = aiohttp.ClientTimeout(total=20)

    for image_url in urls:
        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers=headers
            ) as session:

                async with session.get(
                    image_url,
                    allow_redirects=True
                ) as response:

                    if response.status != 200:
                        continue

                    data = await response.read()

                    if not data:
                        continue

                    if len(data) > 15 * 1024 * 1024:
                        continue

                    try:
                        source = BytesIO(data)

                        img = Image.open(source)
                        img = img.convert("RGB")

                        output = BytesIO()

                        img.save(
                            output,
                            format="JPEG",
                            quality=90,
                            optimize=True
                        )

                        output.seek(0)

                        jpeg_data = output.read()

                        if not jpeg_data:
                            continue

                        return BufferedInputFile(
                            jpeg_data,
                            filename="yixnews.jpg"
                        )

                    except Exception as image_error:
                        print(
                            f"⚠️ Rasm format xatosi: "
                            f"{image_url} -> {image_error}"
                        )

        except Exception as e:
            print(
                f"⚠️ Rasm yuklash xatosi: "
                f"{image_url} -> {e}"
            )

    return None


# =========================================================
# START
# =========================================================

@router.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "👋 *Assalomu alaykum!*\n\n"
        "📰 *YIX News* — yangiliklarni bir joyda "
        "topish va o‘qish uchun zamonaviy aggregator.\n\n"
        "🔎 Kuchli qidiruvdan foydalaning yoki "
        "kategoriya tanlang.",
        reply_markup=main_menu(message.from_user.id),
        parse_mode="Markdown"
    )


# =========================================================
# CATEGORIES
# =========================================================

@router.callback_query(F.data == "categories")
async def categories_handler(callback: CallbackQuery):
    await safe_callback_answer(callback)

    await callback.message.edit_text(
        "📂 *Kategoriyani tanlang:*",
        reply_markup=categories_keyboard(),
        parse_mode="Markdown"
    )


# =========================================================
# STRONG SEARCH
# =========================================================

@router.callback_query(F.data == "search")
async def search_handler(callback: CallbackQuery):
    await safe_callback_answer(callback)

    search_users.add(callback.from_user.id)

    await callback.message.edit_text(
        "🔎 *Kuchli qidiruv*\n\n"
        "Qidirayotgan mavzu, ism, kompaniya yoki "
        "kalit so‘zlarni yozing.\n\n"
        "💡 Masalan:\n"
        "• suniy intellekt\n"
        "• Uzbekistan AI\n"
        "• football\n"
        "• Apple iPhone\n"
        "• Donald Trump\n\n"
        "✍️ Qidiruv so‘zini yuboring:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="back"
                    )
                ]
            ]
        ),
        parse_mode="Markdown"
    )


@router.message(
    F.text,
    ~F.text.startswith("/")
)
async def text_handler(message: Message):
    user_id = message.from_user.id

    # 1. Kanal qo‘shish rejimi
    if user_id in channel_setup_users:
        channel_ref = message.text.strip()

        if channel_ref.lower() in {"cancel", "bekor"}:
            channel_setup_users.pop(user_id, None)
            await message.answer("❌ Kanal qo‘shish bekor qilindi.")
            return

        bot = message.bot

        try:
            chat = await bot.get_chat(channel_ref)

            if chat.type != "channel":
                await message.answer(
                    "❌ Bu Telegram kanal emas."
                )
                return

            me = await bot.get_me()
            member = await bot.get_chat_member(
                chat_id=chat.id,
                user_id=me.id
            )

            if member.status not in {"administrator", "creator"}:
                await message.answer(
                    "❌ Bot bu kanalda administrator emas."
                )
                return

            if (
                member.status == "administrator"
                and getattr(
                    member,
                    "can_post_messages",
                    True
                ) is False
            ):
                await message.answer(
                    "❌ Botda post yozish huquqi yo‘q."
                )
                return

            await add_user_channel(
                user_id=user_id,
                channel_id=str(chat.id),
                channel_username=chat.username or "",
                channel_title=chat.title or "",
            )

            channel_setup_users.pop(user_id, None)

            await message.answer(
                "✅ *KANAL MUVAFFAQIYATLI ULANDI!*\n\n"
                f"📢 *Kanal:* {chat.title or 'Noma’lum'}\n"
                f"🆔 *ID:* `{chat.id}`\n\n"
                "Endi intervalni tanlang va Auto-Post'ni yoqing.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⚙️ Sozlamalarni ochish",
                                callback_data=f"channel:edit:{chat.id}"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="📢 Kanallar",
                                callback_data="channel_auto"
                            )
                        ]
                    ]
                ),
                parse_mode="Markdown"
            )

        except Exception as e:
            print(f"❌ Kanalni ulash xatosi: {e}")

            await message.answer(
                "❌ Kanalni topib bo‘lmadi yoki botning "
                "kanal huquqlarini tekshirishda xatolik yuz berdi.\n\n"
                "Kanal username'ini `@kanal_nomi` yoki "
                "ID'sini `-100...` ko‘rinishida yuboring.",
                parse_mode="Markdown"
            )

        return

    # 2. Qidiruv rejimi
    if user_id in search_users:
        query = normalize_query(message.text)

        if not query:
            await message.answer(
                "❌ Qidiruv so‘zi bo‘sh bo‘lishi mumkin emas."
            )
            return

        search_users.discard(user_id)

        await safe_delete(message)

        await perform_search(message, query)

        return


async def perform_search(message, query):
    try:
        # Database'dan kengroq natija olamiz.
        raw_results = await search_news(
            query,
            limit=200
        )

        # Qo'shimcha ranking.
        results = rank_search_results(
            raw_results,
            query
        )

        if not results:
            await message.answer(
                f"🔎 *“{query}”*\n\n"
                "❌ Hech qanday yangilik topilmadi.\n\n"
                "Boshqa kalit so‘z bilan urinib ko‘ring.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔎 Qayta qidirish",
                                callback_data="search"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="⬅️ Orqaga",
                                callback_data="back"
                            )
                        ],
                    ]
                ),
                parse_mode="Markdown"
            )
            return

        state_key = f"search_{message.from_user.id}"

        user_news_state[state_key] = {
            "articles": results,
            "title": f"🔎 {query}",
            "query": query,
        }

        await show_search_results(
            message,
            state_key,
            0
        )

    except Exception as e:
        print(f"❌ Search xatosi: {e}")

        await message.answer(
            "❌ Qidiruv vaqtida xatolik yuz berdi."
        )


async def show_search_results(
    message,
    state_key,
    page
):
    state = user_news_state.get(state_key)

    if not state:
        await message.answer(
            "❌ Qidiruv sessiyasi topilmadi."
        )
        return

    articles = state.get("articles", [])
    query = state.get("query", "")

    total = len(articles)

    total_pages = (
        total + SEARCH_PAGE_SIZE - 1
    ) // SEARCH_PAGE_SIZE

    if total_pages == 0:
        total_pages = 1

    page = max(0, min(page, total_pages - 1))

    start = page * SEARCH_PAGE_SIZE
    end = start + SEARCH_PAGE_SIZE

    page_articles = articles[start:end]

    text = (
        f"🔎 *Qidiruv:* `{query}`\n\n"
        f"📊 *{total} ta natija topildi*\n\n"
    )

    for number, article in enumerate(
        page_articles,
        start=start + 1
    ):
        title = shorten(
            article.get("title", "Nomsiz"),
            90
        )

        source = article.get(
            "source",
            "Noma'lum"
        )

        text += (
            f"*{number}.* 📰 {title}\n"
            f"   📡 {source}\n\n"
        )

    await message.answer(
        text,
        reply_markup=search_keyboard(
            page,
            total_pages
        ),
        parse_mode="Markdown"
    )


# =========================================================
# SEARCH PAGINATION
# =========================================================

@router.callback_query(F.data.startswith("searchpage:"))
async def search_page_handler(callback: CallbackQuery):
    await safe_callback_answer(callback)

    try:
        page = int(
            callback.data.split(":")[1]
        )
    except Exception:
        page = 0

    state_key = f"search_{callback.from_user.id}"

    state = user_news_state.get(state_key)

    if not state:
        await callback.message.edit_text(
            "❌ Qidiruv sessiyasi tugagan.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔎 Qidiruv",
                            callback_data="search"
                        )
                    ]
                ]
            )
        )
        return

    await show_search_results(
        callback.message,
        state_key,
        page
    )


# =========================================================
# OPEN ARTICLE
# =========================================================

async def show_article(target, state_key, index):
    state = user_news_state.get(state_key)

    if not state:
        return

    articles = state.get("articles", [])

    if not articles:
        return

    index = max(
        0,
        min(index, len(articles) - 1)
    )

    article = articles[index]

    saved = await is_saved(
        target.from_user.id,
        article.get("link", "")
    )

    keyboard = news_keyboard(
        state_key,
        index,
        len(articles),
        saved=saved
    )

    caption = article_caption(article)

    image = await download_image(
        article.get("image")
    )

    if isinstance(target, CallbackQuery):
        message = target.message
    else:
        message = target

    try:
        if image:
            if message.photo:
                try:
                    await message.edit_media(
                        media={
                            "type": "photo",
                            "media": image,
                            "caption": caption,
                            "parse_mode": "Markdown",
                        },
                        reply_markup=keyboard
                    )
                    return
                except Exception:
                    pass

            await safe_delete(message)

            await message.answer_photo(
                photo=image,
                caption=caption,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )

        else:
            if message.photo:
                await safe_delete(message)

                await message.answer(
                    caption,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )
            else:
                await message.edit_text(
                    caption,
                    reply_markup=keyboard,
                    parse_mode="Markdown"
                )

    except Exception as e:
        print(f"❌ Article ko‘rsatish xatosi: {e}")


# =========================================================
# SEARCH RESULT ARTICLE
# =========================================================

@router.callback_query(
    F.data.startswith("searchopen:")
)
async def search_open_handler(callback: CallbackQuery):
    await safe_callback_answer(callback)

    parts = callback.data.split(":")

    if len(parts) != 3:
        return

    state_key = parts[1]
    index = safe_int(parts[2])

    await show_article(
        callback,
        state_key,
        index
    )


# =========================================================
# GENERAL NAVIGATION
# =========================================================

@router.callback_query(F.data.startswith("nav:"))
async def navigation_handler(callback: CallbackQuery):
    await safe_callback_answer(callback)

    parts = callback.data.split(":")

    if len(parts) != 4:
        return

    direction = parts[1]
    state_key = parts[2]
    index = safe_int(parts[3])

    state = user_news_state.get(state_key)

    if not state:
        return

    articles = state.get("articles", [])

    if direction == "next":
        index += 1
    elif direction == "prev":
        index -= 1

    index = max(
        0,
        min(index, len(articles) - 1)
    )

    await show_article(
        callback,
        state_key,
        index
    )


# =========================================================
# SAVE
# =========================================================

@router.callback_query(F.data.startswith("save:"))
async def save_handler(callback: CallbackQuery):
    await safe_callback_answer(
        callback,
        "💾 Saqlandi!"
    )

    parts = callback.data.split(":")

    if len(parts) != 3:
        return

    state_key = parts[1]
    index = safe_int(parts[2])

    state = user_news_state.get(state_key)

    if not state:
        return

    articles = state.get("articles", [])

    if not 0 <= index < len(articles):
        return

    article = articles[index]

    await save_article(
        callback.from_user.id,
        article
    )

    await show_article(
        callback,
        state_key,
        index
    )


@router.callback_query(F.data.startswith("unsave:"))
async def unsave_handler(callback: CallbackQuery):
    await safe_callback_answer(
        callback,
        "🗑 Saqlanganlardan olib tashlandi."
    )

    parts = callback.data.split(":")

    if len(parts) != 3:
        return

    state_key = parts[1]
    index = safe_int(parts[2])

    state = user_news_state.get(state_key)

    if not state:
        return

    articles = state.get("articles", [])

    if not 0 <= index < len(articles):
        return

    article = articles[index]

    await remove_article(
        callback.from_user.id,
        article.get("link", "")
    )

    await show_article(
        callback,
        state_key,
        index
    )


# =========================================================
# LATEST NEWS
# =========================================================

@router.callback_query(F.data == "latest")
async def latest_handler(callback: CallbackQuery):
    await safe_callback_answer(callback)

    user_id = callback.from_user.id

    articles = await get_news(
        limit=50,
        offset=0
    )

    state_key = f"latest_{user_id}"

    user_news_state[state_key] = {
        "articles": articles,
        "title": "📰 So‘nggi yangiliklar",
    }

    if articles:
        await show_article(
            callback,
            state_key,
            0
        )
    else:
        await callback.message.edit_text(
            "❌ Hozircha yangiliklar yo‘q."
        )


# =========================================================
# CATEGORY
# =========================================================

@router.callback_query(F.data.startswith("cat:"))
async def category_handler(callback: CallbackQuery):
    await safe_callback_answer(callback)

    category = callback.data.split(":", 1)[1]

    if category not in CATEGORIES:
        return

    articles = await get_news(
        category=category,
        limit=50,
        offset=0
    )

    state_key = (
        f"cat_{callback.from_user.id}_{category}"
    )

    user_news_state[state_key] = {
        "articles": articles,
        "title": CATEGORIES[category],
    }

    if articles:
        await show_article(
            callback,
            state_key,
            0
        )
    else:
        await callback.message.edit_text(
            f"{CATEGORIES[category]}\n\n"
            "❌ Bu kategoriyada yangilik topilmadi.",
            reply_markup=categories_keyboard()
        )


# =========================================================
# SAVED
# =========================================================

@router.callback_query(F.data == "saved")
async def saved_handler(callback: CallbackQuery):
    await safe_callback_answer(callback)

    articles = await get_saved_articles(
        callback.from_user.id
    )

    if not articles:
        await callback.message.edit_text(
            "💾 *Saqlangan yangiliklar*\n\n"
            "Hozircha saqlangan maqolalar yo‘q.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔎 Qidiruv",
                            callback_data="search"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="⬅️ Orqaga",
                            callback_data="back"
                        )
                    ]
                ]
            ),
            parse_mode="Markdown"
        )
        return

    state_key = f"saved_{callback.from_user.id}"

    user_news_state[state_key] = {
        "articles": articles,
        "title": "💾 Saqlanganlar",
    }

    await show_article(
        callback,
        state_key,
        0
    )


# =========================================================
# BACK
# =========================================================

@router.callback_query(F.data == "back")
async def back_handler(callback: CallbackQuery):
    await safe_callback_answer(callback)

    try:
        if callback.message.photo:
            await safe_delete(callback.message)

            await callback.message.answer(
                "🏠 *YIX News*\n\n"
                "Kerakli bo‘limni tanlang:",
                reply_markup=main_menu(
                    callback.from_user.id
                ),
                parse_mode="Markdown"
            )
        else:
            await callback.message.edit_text(
                "🏠 *YIX News*\n\n"
                "Kerakli bo‘limni tanlang:",
                reply_markup=main_menu(
                    callback.from_user.id
                ),
                parse_mode="Markdown"
            )

    except Exception as e:
        print(f"⚠️ Back xatosi: {e}")


# =========================================================
# SETTINGS
# =========================================================

@router.callback_query(F.data == "settings")
async def settings_handler(callback: CallbackQuery):
    await safe_callback_answer(callback)

    setting = await get_setting(
        callback.from_user.id
    )

    notifications = setting.get(
        "notifications",
        True
    )

    default_category = setting.get(
        "default_category",
        "all"
    )

    await callback.message.edit_text(
        "⚙️ *Sozlamalar*\n\n"
        f"🔔 Bildirishnomalar: "
        f"{'Yoqilgan' if notifications else 'O‘chirilgan'}\n"
        f"📂 Standart kategoriya: "
        f"{CATEGORIES.get(default_category, 'Barchasi')}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔔 Bildirishnomani almashtirish",
                        callback_data="toggle_notifications"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📂 Standart kategoriyani tanlash",
                        callback_data="default_category"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Orqaga",
                        callback_data="back"
                    )
                ]
            ]
        ),
        parse_mode="Markdown"
    )


@router.callback_query(
    F.data == "toggle_notifications"
)
async def toggle_notifications_handler(
    callback: CallbackQuery
):
    await safe_callback_answer(callback)

    await toggle_notifications(
        callback.from_user.id
    )

    await settings_handler(callback)


@router.callback_query(
    F.data == "default_category"
)
async def default_category_handler(
    callback: CallbackQuery
):
    await safe_callback_answer(callback)

    rows = []

    for key, name in CATEGORIES.items():
        rows.append([
            InlineKeyboardButton(
                text=name,
                callback_data=f"default:{key}"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="settings"
        )
    ])

    await callback.message.edit_text(
        "📂 *Standart kategoriyani tanlang:*",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=rows
        ),
        parse_mode="Markdown"
    )


@router.callback_query(
    F.data.startswith("default:")
)
async def default_category_save(
    callback: CallbackQuery
):
    await safe_callback_answer(
        callback,
        "✅ Saqlandi!"
    )

    category = callback.data.split(":", 1)[1]

    await set_default_category(
        callback.from_user.id,
        category
    )

    await settings_handler(callback)


# =========================================================
# NOOP
# =========================================================

@router.callback_query(F.data == "noop")
async def noop_handler(callback: CallbackQuery):
    await safe_callback_answer(callback)


# =========================================================
# ADMIN
# =========================================================

def admin_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Statistika",
                    callback_data="admin_stats"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📰 Manbalar",
                    callback_data="admin_sources"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Yangilash",
                    callback_data="admin_sync"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Orqaga",
                    callback_data="back"
                )
            ]
        ]
    )


def source_count():
    return sum(
        1
        for source in SOURCES
        if source.get("active", True)
    )


async def show_admin_panel(target):
    text = (
        "👑 *YIX NEWS — ADMIN PANEL*\n\n"
        "_Bot boshqaruv markazi_ 👇\n\n"
        f"📰 Faol manbalar: "
        f"*{source_count()}*\n"
        f"🟢 Bot holati: *Online*"
    )

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(
            text,
            reply_markup=admin_keyboard(),
            parse_mode="Markdown"
        )
    else:
        await target.answer(
            text,
            reply_markup=admin_keyboard(),
            parse_mode="Markdown"
        )


@router.message(Command("admin"))
async def admin_command(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    await show_admin_panel(message)


@router.callback_query(F.data == "admin")
async def admin_callback(callback: CallbackQuery):
    await safe_callback_answer(callback)

    if callback.from_user.id not in ADMIN_IDS:
        return

    await show_admin_panel(callback)


@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    await safe_callback_answer(callback)

    if callback.from_user.id not in ADMIN_IDS:
        return

    total_news = await get_news_count()

    text = (
        "📊 *YIX NEWS STATISTIKA*\n\n"
        f"📰 Yangiliklar: *{total_news}*\n"
        f"📡 Faol manbalar: *{source_count()}*\n"
        "🟢 Bot: *Online*"
    )

    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Admin panel",
                        callback_data="admin"
                    )
                ]
            ]
        ),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin_sources")
async def admin_sources(callback: CallbackQuery):
    await safe_callback_answer(callback)

    if callback.from_user.id not in ADMIN_IDS:
        return

    text = "📰 *YIX NEWS MANBALARI*\n\n"

    for source in SOURCES:
        name = source.get(
            "name",
            "Noma'lum"
        )

        active = source.get(
            "active",
            True
        )

        text += (
            f"{'🟢' if active else '🔴'} "
            f"{name}\n"
        )

    await callback.message.edit_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Admin panel",
                        callback_data="admin"
                    )
                ]
            ]
        ),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "admin_sync")
async def admin_sync(callback: CallbackQuery):
    await safe_callback_answer(
        callback,
        "🔄 Yangilanmoqda..."
    )

    if callback.from_user.id not in ADMIN_IDS:
        return

    added = await sync_news()

    await callback.message.edit_text(
        "🔄 *SYNC YAKUNLANDI*\n\n"
        f"✅ Yangi maqolalar: *{added}*",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Admin panel",
                        callback_data="admin"
                    )
                ]
            ]
        ),
        parse_mode="Markdown"
    )


print("✅ YIX News handlers yuklandi")


# =========================================================
# YIX NEWS — USER CHANNEL AUTO-POST
# =========================================================

channel_setup_users = {}

CHANNEL_INTERVALS = {
    300: "5 daqiqa",
    600: "10 daqiqa",
    1800: "30 daqiqa",
    3600: "1 soat",
    10800: "3 soat",
    21600: "6 soat",
    43200: "12 soat",
    86400: "24 soat",
}


def channel_menu_keyboard(channels):
    rows = []

    if channels:
        for channel in channels:
            channel_id = str(channel["channel_id"])
            title = channel.get("channel_title") or channel.get(
                "channel_username"
            ) or channel_id

            status = "🟢" if channel.get("enabled") else "🔴"

            rows.append([
                InlineKeyboardButton(
                    text=f"{status} {title[:25]}",
                    callback_data=f"channel:edit:{channel_id}"
                )
            ])

    rows.append([
        InlineKeyboardButton(
            text="➕ Kanal qo‘shish",
            callback_data="channel:add"
        )
    ])

    rows.append([
        InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data="back"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_channel_menu(target):
    user_id = target.from_user.id
    channels = await get_user_channels(user_id)

    text = (
        "📢 *KANAL AUTO-POST*\n\n"
        "YIX News yangi yangiliklarni sizning Telegram "
        "kanalingizga avtomatik joylaydi.\n\n"
        "⚠️ Bot kanalga *administrator* qilib qo‘yilgan "
        "va post yozish huquqiga ega bo‘lishi kerak."
    )

    markup = channel_menu_keyboard(channels)

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    else:
        await target.answer(
            text,
            reply_markup=markup,
            parse_mode="Markdown"
        )


@router.message(Command("channel"))
async def channel_command(message: Message):
    await show_channel_menu(message)


@router.callback_query(F.data == "channel_auto")
async def channel_auto_callback(callback: CallbackQuery):
    await safe_callback_answer(callback)
    await show_channel_menu(callback)


@router.callback_query(F.data == "channel:add")
async def channel_add_callback(callback: CallbackQuery):
    await safe_callback_answer(callback)

    user_id = callback.from_user.id
    channel_setup_users[user_id] = True

    request_chat = KeyboardButton(
        text="📢 Kanalni tanlash",
        request_chat=KeyboardButtonRequestChat(
            request_id=1001,
            chat_is_channel=True,
            request_title=True,
            request_username=True,
            request_photo=True,
            bot_is_member=False,
        ),
    )

    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [request_chat],
            [KeyboardButton(text="❌ Bekor qilish")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await callback.message.edit_text(
        "➕ <b>KANAL QO‘SHISH</b>\n\n"
        "1️⃣ <b>📢 Kanalni tanlash</b> tugmasini bosing.\n"
        "2️⃣ O‘zingiz boshqaradigan kanalni tanlang.\n"
        "3️⃣ Telegram orqali botga administrator huquqini bering.\n"
        "4️⃣ <b>Post messages</b> huquqini yoqing.\n\n"
        "Keyin YIX News kanalni avtomatik ulaydi.",
        parse_mode="HTML",
    )

    await callback.message.answer(
        "📢 Kanalni tanlash uchun quyidagi tugmani bosing:",
        reply_markup=keyboard,
    )


@router.message(F.chat_shared)
async def channel_shared_handler(message: Message):
    user_id = message.from_user.id

    if user_id not in channel_setup_users:
        return

    channel_id = message.chat_shared.chat_id

    print(f"📢 Channel shared: user={user_id}, channel={channel_id}")

    try:
        bot = message.bot

        chat = await bot.get_chat(channel_id)

        print(
            f"✅ Kanal topildi: "
            f"{chat.title} ({chat.id})"
        )

        await add_user_channel(
            user_id=user_id,
            channel_id=str(chat.id),
            channel_username=chat.username or "",
            channel_title=chat.title or "",
        )

        channel_setup_users.pop(user_id, None)

        await message.answer(
            "✅ <b>KANAL MUVAFFAQIYATLI ULANDI!</b>\n\n"
            f"📢 <b>Kanal:</b> {chat.title or 'Noma’lum'}\n"
            f"🆔 <code>{chat.id}</code>\n\n"
            "Endi kanal uchun avtomatik post intervalini tanlang.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )

        await show_channel_menu(message)

    except Exception as e:
        print(f"❌ Shared kanal xatosi: {e}")

        await message.answer(
            "❌ Kanalni ulashda xatolik yuz berdi.\n\n"
            "Bot kanalga administrator qilib qo‘yilganini va "
            "<b>Post messages</b> huquqi berilganini tekshiring.",
            parse_mode="HTML",
        )


@router.message(Command("cancel"))
async def channel_cancel_command(message: Message):
    user_id = message.from_user.id

    if user_id in channel_setup_users:
        channel_setup_users.pop(user_id, None)

        await message.answer(
            "❌ Kanal qo‘shish bekor qilindi."
        )




async def get_user_channel(user_id, channel_id):
    channels = await get_user_channels(user_id)

    for channel in channels:
        if str(channel["channel_id"]) == str(channel_id):
            return channel

    return None


@router.callback_query(F.data.startswith("channel:edit:"))
async def channel_edit_callback(callback: CallbackQuery):
    await safe_callback_answer(callback)

    channel_id = callback.data.split(":", 2)[2]
    channel = await get_user_channel(
        callback.from_user.id,
        channel_id
    )

    if not channel:
        await callback.message.edit_text(
            "❌ Kanal topilmadi.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Kanallar",
                            callback_data="channel_auto"
                        )
                    ]
                ]
            )
        )
        return

    title = (
        channel.get("channel_title")
        or channel.get("channel_username")
        or channel_id
    )

    enabled = bool(channel.get("enabled"))
    interval = int(channel.get("interval") or 1800)

    status = "🟢 YONIQ" if enabled else "🔴 O‘CHIQ"
    interval_text = CHANNEL_INTERVALS.get(
        interval,
        f"{interval} soniya"
    )

    await callback.message.edit_text(
        "📢 *KANAL SOZLAMALARI*\n\n"
        f"📡 *Kanal:* {title}\n"
        f"📊 *Holat:* {status}\n"
        f"⏱ *Interval:* {interval_text}\n\n"
        "Quyidan kerakli amalni tanlang:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⏱ Interval",
                        callback_data=f"channel:interval:{channel_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🟢 ON",
                        callback_data=f"channel:on:{channel_id}"
                    ),
                    InlineKeyboardButton(
                        text="🔴 OFF",
                        callback_data=f"channel:off:{channel_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🗑 Kanalni uzish",
                        callback_data=f"channel:remove:{channel_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Kanallar",
                        callback_data="channel_auto"
                    )
                ]
            ]
        ),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("channel:interval:"))
async def channel_interval_callback(callback: CallbackQuery):
    await safe_callback_answer(callback)

    channel_id = callback.data.split(":", 2)[2]

    if not await get_user_channel(
        callback.from_user.id,
        channel_id
    ):
        return

    buttons = []

    intervals = list(CHANNEL_INTERVALS.items())

    for i in range(0, len(intervals), 2):
        row = []

        for seconds, name in intervals[i:i + 2]:
            row.append(
                InlineKeyboardButton(
                    text=f"⏱ {name}",
                    callback_data=(
                        f"channel:setinterval:"
                        f"{channel_id}:{seconds}"
                    )
                )
            )

        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Sozlamalar",
            callback_data=f"channel:edit:{channel_id}"
        )
    ])

    await callback.message.edit_text(
        "⏱ *YANGILIKLAR ORALIG‘I*\n\n"
        "Yangi yangiliklar kanalga qanchada bir "
        "joylanishini tanlang:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("channel:setinterval:"))
async def channel_set_interval_callback(callback: CallbackQuery):
    await safe_callback_answer(callback)

    parts = callback.data.split(":")

    if len(parts) != 4:
        return

    channel_id = parts[2]

    try:
        seconds = int(parts[3])
    except ValueError:
        return

    if seconds not in CHANNEL_INTERVALS:
        return

    channel = await get_user_channel(
        callback.from_user.id,
        channel_id
    )

    if not channel:
        return

    await set_channel_interval(
        user_id=callback.from_user.id,
        channel_id=channel_id,
        interval=seconds
    )

    await callback.message.edit_text(
        "✅ *INTERVAL SAQLANDI*\n\n"
        f"⏱ Yangi interval: "
        f"*{CHANNEL_INTERVALS[seconds]}*",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⚙️ Sozlamalar",
                        callback_data=f"channel:edit:{channel_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Kanallar",
                        callback_data="channel_auto"
                    )
                ]
            ]
        ),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("channel:on:"))
async def channel_on_callback(callback: CallbackQuery):
    await safe_callback_answer(callback)

    channel_id = callback.data.split(":", 2)[2]

    channel = await get_user_channel(
        callback.from_user.id,
        channel_id
    )

    if not channel:
        return

    await set_channel_enabled(
        user_id=callback.from_user.id,
        channel_id=channel_id,
        enabled=True
    )

    await callback.message.edit_text(
        "🟢 *AUTO-POST YOQILDI!*\n\n"
        "YIX News yangi yangiliklarni tanlangan "
        "interval bo‘yicha kanalingizga joylaydi.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⚙️ Sozlamalar",
                        callback_data=f"channel:edit:{channel_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Kanallar",
                        callback_data="channel_auto"
                    )
                ]
            ]
        ),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("channel:off:"))
async def channel_off_callback(callback: CallbackQuery):
    await safe_callback_answer(callback)

    channel_id = callback.data.split(":", 2)[2]

    if not await get_user_channel(
        callback.from_user.id,
        channel_id
    ):
        return

    await set_channel_enabled(
        user_id=callback.from_user.id,
        channel_id=channel_id,
        enabled=False
    )

    await callback.message.edit_text(
        "🔴 *AUTO-POST O‘CHIRILDI*",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⚙️ Sozlamalar",
                        callback_data=f"channel:edit:{channel_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Kanallar",
                        callback_data="channel_auto"
                    )
                ]
            ]
        ),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("channel:remove:"))
async def channel_remove_callback(callback: CallbackQuery):
    await safe_callback_answer(callback)

    channel_id = callback.data.split(":", 2)[2]

    if not await get_user_channel(
        callback.from_user.id,
        channel_id
    ):
        return

    await remove_user_channel(
        user_id=callback.from_user.id,
        channel_id=channel_id
    )

    await callback.message.edit_text(
        "🗑 *KANAL UZILDI*\n\n"
        "Ushbu kanal uchun Auto-Post sozlamalari "
        "o‘chirildi.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📢 Kanallar",
                        callback_data="channel_auto"
                    )
                ]
            ]
        ),
        parse_mode="Markdown"
    )
