from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Boolean,
    select,
    delete,
    func,
    or_,
    text,
)

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import declarative_base

from app.config import DATABASE_URL


Base = declarative_base()

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# =========================================================
# MODELS
# =========================================================

class News(Base):
    __tablename__ = "news"

    id = Column(Integer, primary_key=True)

    title = Column(Text, nullable=False)
    link = Column(String(2000), unique=True, nullable=False)

    summary = Column(Text, default="")
    image = Column(String(2000), nullable=True)

    source = Column(String(500), default="")
    category = Column(String(100), default="world")

    published_at = Column(DateTime, nullable=True)

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )


class SavedNews(Base):
    __tablename__ = "saved_news"

    id = Column(Integer, primary_key=True)

    user_id = Column(Integer, nullable=False)
    news_id = Column(Integer, nullable=False)

    link = Column(String(2000), nullable=False)

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True)

    user_id = Column(
        Integer,
        unique=True,
        nullable=False
    )

    notifications = Column(
        Boolean,
        default=True
    )

    default_category = Column(
        String(100),
        default="all"
    )


# =========================================================
# INIT DATABASE
# =========================================================

async def init_db():
    await init_channel_tables()
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all
        )

        # SQLite FTS5 virtual table
        await conn.exec_driver_sql(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS news_fts
            USING fts5(
                title,
                summary,
                source,
                content='news',
                content_rowid='id'
            )
            """
        )

        # FTS triggers
        await conn.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS news_ai
            AFTER INSERT ON news
            BEGIN
                INSERT INTO news_fts(
                    rowid,
                    title,
                    summary,
                    source
                )
                VALUES (
                    new.id,
                    new.title,
                    new.summary,
                    new.source
                );
            END;
            """
        )

        await conn.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS news_ad
            AFTER DELETE ON news
            BEGIN
                INSERT INTO news_fts(
                    news_fts,
                    rowid,
                    title,
                    summary,
                    source
                )
                VALUES (
                    'delete',
                    old.id,
                    old.title,
                    old.summary,
                    old.source
                );
            END;
            """
        )

        await conn.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS news_au
            AFTER UPDATE ON news
            BEGIN
                INSERT INTO news_fts(
                    news_fts,
                    rowid,
                    title,
                    summary,
                    source
                )
                VALUES (
                    'delete',
                    old.id,
                    old.title,
                    old.summary,
                    old.source
                );

                INSERT INTO news_fts(
                    rowid,
                    title,
                    summary,
                    source
                )
                VALUES (
                    new.id,
                    new.title,
                    new.summary,
                    new.source
                );
            END;
            """
        )

        # Existing maqolalarni FTS indeksiga qo'shish.
        await conn.exec_driver_sql(
            """
            INSERT INTO news_fts(
                rowid,
                title,
                summary,
                source
            )
            SELECT
                id,
                title,
                summary,
                source
            FROM news
            WHERE id NOT IN (
                SELECT rowid
                FROM news_fts
            );
            """
        )


# =========================================================
# NEWS SAVE
# =========================================================

async def save_news(article):
    async with SessionLocal() as session:

        link = article.get("link")

        if not link:
            return False

        existing = await session.scalar(
            select(News).where(
                News.link == link
            )
        )

        if existing:
            return False

        news = News(
            title=article.get(
                "title",
                "Yangilik"
            ),
            link=link,
            summary=article.get(
                "summary",
                ""
            ),
            image=article.get(
                "image"
            ),
            source=article.get(
                "source",
                ""
            ),
            category=article.get(
                "category",
                "world"
            ),
            published_at=article.get(
                "published_at"
            ),
        )

        session.add(news)

        await session.commit()

        return True


async def save_news_bulk(articles):
    added = 0

    async with SessionLocal() as session:

        for article in articles:

            link = article.get("link")

            if not link:
                continue

            existing = await session.scalar(
                select(News).where(
                    News.link == link
                )
            )

            if existing:
                continue

            news = News(
                title=article.get(
                    "title",
                    "Yangilik"
                ),
                link=link,
                summary=article.get(
                    "summary",
                    ""
                ),
                image=article.get(
                    "image"
                ),
                source=article.get(
                    "source",
                    ""
                ),
                category=article.get(
                    "category",
                    "world"
                ),
                published_at=article.get(
                    "published_at"
                ),
            )

            session.add(news)
            added += 1

        await session.commit()

    return added


# =========================================================
# NEWS GET
# =========================================================

def news_to_dict(news):
    return {
        "id": news.id,
        "title": news.title,
        "link": news.link,
        "summary": news.summary or "",
        "image": news.image,
        "source": news.source or "",
        "category": news.category or "world",
        "published_at": news.published_at,
    }


async def get_news(
    category=None,
    limit=50,
    offset=0
):
    async with SessionLocal() as session:

        query = select(News)

        if category:
            query = query.where(
                News.category == category
            )

        query = query.order_by(
            News.published_at.desc(),
            News.id.desc()
        ).limit(limit).offset(offset)

        result = await session.execute(query)

        return [
            news_to_dict(news)
            for news in result.scalars().all()
        ]


# =========================================================
# 🔥 FTS5 KUCHLI QIDIRUV
# =========================================================

def build_fts_query(query):
    words = []

    for word in query.strip().split():
        word = word.strip()

        if not word:
            continue

        # FTS operatorlarini xavfsiz qilamiz.
        word = (
            word
            .replace('"', "")
            .replace("'", "")
            .replace("*", "")
            .replace(":", "")
            .replace("(", "")
            .replace(")", "")
        )

        if word:
            words.append(
                f'"{word}"*'
            )

    if not words:
        return ""

    # AND:
    # barcha muhim so'zlar mavjud bo'lishi kerak
    return " AND ".join(words)


async def search_news(
    query,
    limit=50
):
    fts_query = build_fts_query(query)

    if not fts_query:
        return []

    async with SessionLocal() as session:

        sql = """
            SELECT
                n.id,
                n.title,
                n.link,
                n.summary,
                n.image,
                n.source,
                n.category,
                n.published_at,
                bm25(
                    news_fts,
                    10.0,
                    3.0,
                    2.0
                ) AS rank
            FROM news_fts
            JOIN news n
                ON n.id = news_fts.rowid
            WHERE news_fts MATCH :query
            ORDER BY rank ASC,
                     n.published_at DESC
            LIMIT :limit
        """

        result = await session.execute(
            __import__(
                "sqlalchemy"
            ).text(sql),
            {
                "query": fts_query,
                "limit": limit,
            }
        )

        rows = result.mappings().all()

        return [
            {
                "id": row["id"],
                "title": row["title"],
                "link": row["link"],
                "summary": row["summary"] or "",
                "image": row["image"],
                "source": row["source"] or "",
                "category": row["category"] or "world",
                "published_at": row["published_at"],
                "_search_rank": row["rank"],
            }
            for row in rows
        ]


# =========================================================
# COUNT
# =========================================================

async def get_news_count(category=None):
    async with SessionLocal() as session:

        query = select(
            func.count(News.id)
        )

        if category:
            query = query.where(
                News.category == category
            )

        return await session.scalar(query) or 0


# =========================================================
# SAVED ARTICLES
# =========================================================

async def save_article(user_id, article):
    async with SessionLocal() as session:

        link = article.get("link", "")

        if not link:
            return False

        existing = await session.scalar(
            select(SavedNews).where(
                SavedNews.user_id == user_id,
                SavedNews.link == link
            )
        )

        if existing:
            return False

        item = SavedNews(
            user_id=user_id,
            news_id=article.get("id", 0),
            link=link
        )

        session.add(item)

        await session.commit()

        return True


async def remove_article(user_id, link):
    async with SessionLocal() as session:

        await session.execute(
            delete(SavedNews).where(
                SavedNews.user_id == user_id,
                SavedNews.link == link
            )
        )

        await session.commit()


async def is_saved(user_id, link):
    async with SessionLocal() as session:

        result = await session.scalar(
            select(SavedNews.id).where(
                SavedNews.user_id == user_id,
                SavedNews.link == link
            )
        )

        return result is not None


async def get_saved_articles(user_id):
    async with SessionLocal() as session:

        query = (
            select(News)
            .join(
                SavedNews,
                SavedNews.news_id == News.id
            )
            .where(
                SavedNews.user_id == user_id
            )
            .order_by(
                SavedNews.created_at.desc()
            )
        )

        result = await session.execute(query)

        return [
            news_to_dict(news)
            for news in result.scalars().all()
        ]


# =========================================================
# USER SETTINGS
# =========================================================

async def get_setting(user_id):
    async with SessionLocal() as session:

        setting = await session.scalar(
            select(UserSettings).where(
                UserSettings.user_id == user_id
            )
        )

        if not setting:
            setting = UserSettings(
                user_id=user_id,
                notifications=True,
                default_category="all"
            )

            session.add(setting)

            await session.commit()

        return {
            "notifications": setting.notifications,
            "default_category": setting.default_category,
        }


async def toggle_notifications(user_id):
    async with SessionLocal() as session:

        setting = await session.scalar(
            select(UserSettings).where(
                UserSettings.user_id == user_id
            )
        )

        if not setting:
            setting = UserSettings(
                user_id=user_id,
                notifications=True,
                default_category="all"
            )

            session.add(setting)

        setting.notifications = not setting.notifications

        await session.commit()

        return setting.notifications


async def set_default_category(
    user_id,
    category
):
    async with SessionLocal() as session:

        setting = await session.scalar(
            select(UserSettings).where(
                UserSettings.user_id == user_id
            )
        )

        if not setting:
            setting = UserSettings(
                user_id=user_id,
                notifications=True,
                default_category=category
            )

            session.add(setting)

        else:
            setting.default_category = category

        await session.commit()


print("✅ Database + SQLite FTS5 yuklandi")


# ============================================================
# YIX NEWS — USER CHANNEL AUTO-POST
# ============================================================

async def init_channel_tables():
    """
    User kanallari va kanalga yuborilgan yangiliklarni yaratadi.
    Mavjud DB ma'lumotlariga tegmaydi.
    """
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id TEXT NOT NULL,
                channel_username TEXT,
                channel_title TEXT,
                interval INTEGER NOT NULL DEFAULT 1800,
                enabled INTEGER NOT NULL DEFAULT 0,
                last_post_at DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, channel_id)
            )
        """))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS channel_posted_news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                news_id INTEGER NOT NULL,
                posted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_id, news_id)
            )
        """))

        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_user_channels_user
            ON user_channels(user_id)
        """))

        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_user_channels_enabled
            ON user_channels(enabled)
        """))

        await conn.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_channel_posted_news_channel
            ON channel_posted_news(channel_id)
        """))

    print("✅ Channel auto-post jadvallari tayyor")


async def add_user_channel(
    user_id: int,
    channel_id: str,
    channel_username: str = "",
    channel_title: str = "",
):
    async with SessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO user_channels (
                    user_id,
                    channel_id,
                    channel_username,
                    channel_title,
                    interval,
                    enabled
                )
                VALUES (
                    :user_id,
                    :channel_id,
                    :channel_username,
                    :channel_title,
                    1800,
                    0
                )
                ON CONFLICT(user_id, channel_id)
                DO UPDATE SET
                    channel_username = excluded.channel_username,
                    channel_title = excluded.channel_title
            """),
            {
                "user_id": user_id,
                "channel_id": str(channel_id),
                "channel_username": channel_username or "",
                "channel_title": channel_title or "",
            },
        )

        await session.commit()


async def get_user_channels(user_id: int):
    async with SessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT
                    id,
                    user_id,
                    channel_id,
                    channel_username,
                    channel_title,
                    interval,
                    enabled,
                    last_post_at,
                    created_at
                FROM user_channels
                WHERE user_id = :user_id
                ORDER BY id DESC
            """),
            {"user_id": user_id},
        )

        return [
            dict(row)
            for row in result.mappings().all()
        ]


async def get_active_channels():
    async with SessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT
                    id,
                    user_id,
                    channel_id,
                    channel_username,
                    channel_title,
                    interval,
                    enabled,
                    last_post_at,
                    created_at
                FROM user_channels
                WHERE enabled = 1
                ORDER BY id ASC
            """)
        )

        return [
            dict(row)
            for row in result.mappings().all()
        ]


async def set_channel_interval(
    user_id: int,
    channel_id: str,
    interval: int,
):
    async with SessionLocal() as session:
        await session.execute(
            text("""
                UPDATE user_channels
                SET interval = :interval
                WHERE user_id = :user_id
                  AND channel_id = :channel_id
            """),
            {
                "user_id": user_id,
                "channel_id": str(channel_id),
                "interval": int(interval),
            },
        )

        await session.commit()


async def set_channel_enabled(
    user_id: int,
    channel_id: str,
    enabled: bool,
):
    async with SessionLocal() as session:
        await session.execute(
            text("""
                UPDATE user_channels
                SET enabled = :enabled
                WHERE user_id = :user_id
                  AND channel_id = :channel_id
            """),
            {
                "user_id": user_id,
                "channel_id": str(channel_id),
                "enabled": 1 if enabled else 0,
            },
        )

        await session.commit()


async def remove_user_channel(
    user_id: int,
    channel_id: str,
):
    async with SessionLocal() as session:
        await session.execute(
            text("""
                DELETE FROM user_channels
                WHERE user_id = :user_id
                  AND channel_id = :channel_id
            """),
            {
                "user_id": user_id,
                "channel_id": str(channel_id),
            },
        )

        await session.commit()


async def get_next_unposted_news(channel_id: str):
    async with SessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT
                    n.id,
                    n.title,
                    n.link,
                    n.summary,
                    n.image,
                    n.source,
                    n.category,
                    n.published_at,
                    n.created_at
                FROM news n
                JOIN user_channels uc
                    ON uc.channel_id = :channel_id
                WHERE uc.enabled = 1
                  AND (
                      uc.last_post_at IS NULL
                      OR n.created_at > uc.last_post_at
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM channel_posted_news cp
                      WHERE cp.channel_id = :channel_id
                        AND cp.news_id = n.id
                  )
                ORDER BY
                    n.created_at ASC,
                    n.id ASC
                LIMIT 1
            """),
            {
                "channel_id": str(channel_id),
            },
        )

        row = result.mappings().first()

        if not row:
            return None

        return dict(row)


async def mark_news_posted(
    channel_id: str,
    news_id: int,
):
    async with SessionLocal() as session:
        await session.execute(
            text("""
                INSERT OR IGNORE INTO channel_posted_news (
                    channel_id,
                    news_id,
                    posted_at
                )
                VALUES (
                    :channel_id,
                    :news_id,
                    CURRENT_TIMESTAMP
                )
            """),
            {
                "channel_id": str(channel_id),
                "news_id": int(news_id),
            },
        )

        await session.execute(
            text("""
                UPDATE user_channels
                SET last_post_at = CURRENT_TIMESTAMP
                WHERE channel_id = :channel_id
            """),
            {
                "channel_id": str(channel_id),
            },
        )

        await session.commit()
