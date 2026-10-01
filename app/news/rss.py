from app.news.sources import SOURCES

import asyncio
import json
import random
import aiohttp
import feedparser

from html import unescape
from bs4 import BeautifulSoup
from urllib.parse import urljoin


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 15) "
        "AppleWebKit/537.36 Chrome/140 Safari/537.36"
    ),
    "Accept": (
        "application/rss+xml, application/xml, "
        "text/xml, text/html, */*"
    ),
    "Accept-Language": "uz,en;q=0.9,ru;q=0.8",
}


def clean_text(text: str) -> str:
    if not text:
        return ""

    soup = BeautifulSoup(str(text), "html.parser")

    return unescape(
        soup.get_text(" ", strip=True)
    )


def normalize_image(url, base_url=None):
    if not url:
        return None

    url = str(url).strip()

    if not url:
        return None

    if url.startswith("//"):
        url = "https:" + url

    if base_url:
        url = urljoin(base_url, url)

    if not url.startswith(("http://", "https://")):
        return None

    # Saytning standart placeholder rasmlarini qabul qilmaymiz
    bad_images = (
        "og-default",
        "default-image",
        "placeholder",
        "no-image",
        "noimage",
    )

    lowered = url.lower()

    if any(
        bad in lowered
        for bad in bad_images
    ):
        return None

    return url


def get_image(entry, base_url=None):
    """
    RSS entry ichidan maksimal darajada rasm topadi.
    """

    # media_content
    for key in (
        "media_content",
        "media_thumbnail",
    ):
        items = entry.get(key)

        if items:
            for item in items:
                if isinstance(item, dict):
                    url = (
                        item.get("url")
                        or item.get("href")
                    )

                    url = normalize_image(
                        url,
                        base_url
                    )

                    if url:
                        return url

    # enclosure
    for item in entry.get("enclosures", []):
        if isinstance(item, dict):

            url = (
                item.get("href")
                or item.get("url")
            )

            url = normalize_image(
                url,
                base_url
            )

            if url:
                return url

    # media object
    media = entry.get("media")

    if isinstance(media, dict):
        url = (
            media.get("url")
            or media.get("href")
        )

        url = normalize_image(
            url,
            base_url
        )

        if url:
            return url

    # summary/description ichidagi <img>
    for field in (
        "summary",
        "description",
        "content",
    ):

        value = entry.get(field)

        if not value:
            continue

        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    value = item.get("value", "")
                    break

        soup = BeautifulSoup(
            str(value),
            "html.parser"
        )

        img = soup.find("img")

        if img:
            for attr in (
                "src",
                "data-src",
                "data-original",
                "data-lazy-src",
            ):
                url = img.get(attr)

                url = normalize_image(
                    url,
                    base_url
                )

                if url:
                    return url

            srcset = img.get("srcset")

            if srcset:
                parts = [
                    x.strip().split(" ")[0]
                    for x in srcset.split(",")
                    if x.strip()
                ]

                for url in reversed(parts):
                    url = normalize_image(
                        url,
                        base_url
                    )

                    if url:
                        return url

    return None


def extract_meta_image(soup, base_url):
    """
    og:image, twitter:image va boshqa meta rasmlar.
    """

    meta_names = [
        ("property", "og:image"),
        ("property", "og:image:url"),
        ("name", "twitter:image"),
        ("name", "twitter:image:src"),
        ("itemprop", "image"),
    ]

    for attr, value in meta_names:

        tag = soup.find(
            "meta",
            attrs={attr: value}
        )

        if tag:
            url = (
                tag.get("content")
                or tag.get("value")
            )

            url = normalize_image(
                url,
                base_url
            )

            if url:
                return url

    return None


def extract_jsonld_image(obj, base_url):
    if not isinstance(obj, dict):
        return None

    image = obj.get("image")

    if isinstance(image, str):
        return normalize_image(
            image,
            base_url
        )

    if isinstance(image, list):

        for item in image:

            if isinstance(item, str):
                url = normalize_image(
                    item,
                    base_url
                )

                if url:
                    return url

            elif isinstance(item, dict):
                url = normalize_image(
                    item.get("url"),
                    base_url
                )

                if url:
                    return url

    if isinstance(image, dict):
        return normalize_image(
            image.get("url"),
            base_url
        )

    return None


def extract_html_image(container, base_url):
    """
    Berilgan HTML blokidan rasm qidiradi.
    """

    if not container:
        return None

    # img
    for img in container.find_all("img"):

        for attr in (
            "src",
            "data-src",
            "data-original",
            "data-lazy-src",
            "data-image",
            "data-url",
        ):

            url = img.get(attr)

            url = normalize_image(
                url,
                base_url
            )

            if url:
                return url

        srcset = img.get("srcset")

        if srcset:

            candidates = []

            for item in srcset.split(","):
                item = item.strip()

                if not item:
                    continue

                candidates.append(
                    item.split(" ")[0]
                )

            for url in reversed(candidates):

                url = normalize_image(
                    url,
                    base_url
                )

                if url:
                    return url

    # picture source
    for source in container.find_all(
        "source"
    ):

        srcset = source.get("srcset")

        if not srcset:
            continue

        for item in reversed(
            srcset.split(",")
        ):

            url = item.strip().split(" ")[0]

            url = normalize_image(
                url,
                base_url
            )

            if url:
                return url

    return None


async def fetch_article_image(
    session,
    link
):
    """
    Maqolaning o'z sahifasiga kirib,
    rasmni topadi.
    """

    if not link:
        return None

    try:

        async with session.get(
            link,
            timeout=aiohttp.ClientTimeout(
                total=15
            ),
            allow_redirects=True,
        ) as response:

            if response.status >= 400:
                return None

            html = await response.text(
                errors="ignore"
            )

        soup = BeautifulSoup(
            html,
            "html.parser"
        )

        base_url = str(
            response.url
        )

        # 1. og:image
        image = extract_meta_image(
            soup,
            base_url
        )

        if image:
            return image

        # 2. JSON-LD
        for script in soup.find_all(
            "script",
            type="application/ld+json"
        ):

            try:

                raw = (
                    script.string
                    or script.get_text()
                )

                data = json.loads(raw)

                objects = (
                    data
                    if isinstance(data, list)
                    else [data]
                )

                for obj in objects:

                    if not isinstance(obj, dict):
                        continue

                    image = extract_jsonld_image(
                        obj,
                        base_url
                    )

                    if image:
                        return image

                    # @graph
                    graph = obj.get("@graph")

                    if isinstance(graph, list):

                        for item in graph:

                            image = extract_jsonld_image(
                                item,
                                base_url
                            )

                            if image:
                                return image

            except Exception:
                continue

        # 3. article/main
        for selector in (
            "article",
            "main",
            "[itemprop='articleBody']",
            ".article",
            ".news-detail",
            ".single-content",
            ".post-content",
        ):

            container = soup.select_one(
                selector
            )

            image = extract_html_image(
                container,
                base_url
            )

            if image:
                return image

        # 4. butun sahifadan
        image = extract_html_image(
            soup,
            base_url
        )

        if image:
            return image

    except Exception:
        pass

    return None


async def fetch_feed(source):
    try:

        timeout = aiohttp.ClientTimeout(
            total=20
        )

        async with aiohttp.ClientSession(
            headers=HEADERS
        ) as session:

            async with session.get(
                source["url"],
                timeout=timeout,
                allow_redirects=True,
            ) as response:

                if response.status >= 400:

                    print(
                        f"[RSS {response.status}] "
                        f"{source['name']}"
                    )

                    return []

                data = await response.read()

        feed = feedparser.parse(data)

        articles = []

        for entry in feed.entries:

            title = clean_text(
                entry.get("title", "")
            )

            link = entry.get(
                "link",
                ""
            )

            if not title or not link:
                continue

            summary = clean_text(
                entry.get("summary", "")
                or entry.get(
                    "description",
                    ""
                )
            )

            published = entry.get(
                "published",
                entry.get(
                    "updated",
                    ""
                ),
            )

            image = get_image(
                entry,
                source["url"]
            )

            articles.append({
                "source": source["name"],
                "category": source["category"],
                "title": title,
                "summary": summary[:700],
                "link": link,
                "image": image,
                "published": published,
            })

        print(
            f"[RSS] {source['name']}: "
            f"{len(articles)} ta"
        )

        return articles

    except Exception as e:

        print(
            f"[RSS ERROR] "
            f"{source['name']}: {e}"
        )

        return []


async def fetch_html_source(source):

    try:

        timeout = aiohttp.ClientTimeout(
            total=20
        )

        async with aiohttp.ClientSession(
            headers=HEADERS
        ) as session:

            async with session.get(
                source["url"],
                timeout=timeout,
                allow_redirects=True,
            ) as response:

                if response.status >= 400:

                    print(
                        f"[HTML {response.status}] "
                        f"{source['name']}"
                    )

                    return []

                html = await response.text(
                    errors="ignore"
                )

            soup = BeautifulSoup(
                html,
                "html.parser"
            )

            articles = []
            seen = set()

            # JSON-LD
            for script in soup.find_all(
                "script",
                type="application/ld+json"
            ):

                try:

                    raw = (
                        script.string
                        or script.get_text()
                    )

                    data = json.loads(raw)

                    objects = (
                        data
                        if isinstance(data, list)
                        else [data]
                    )

                    for obj in objects:

                        if not isinstance(
                            obj,
                            dict
                        ):
                            continue

                        types = obj.get(
                            "@type",
                            []
                        )

                        if isinstance(
                            types,
                            str
                        ):
                            types = [types]

                        if not any(
                            x in (
                                "NewsArticle",
                                "Article",
                            )
                            for x in types
                        ):
                            continue

                        title = clean_text(
                            obj.get(
                                "headline",
                                ""
                            )
                        )

                        link = obj.get(
                            "url",
                            ""
                        )

                        if not title or not link:
                            continue

                        link = urljoin(
                            source["url"],
                            link
                        )

                        if link in seen:
                            continue

                        seen.add(link)

                        image = extract_jsonld_image(
                            obj,
                            link
                        )

                        if not image:
                            image = extract_meta_image(
                                soup,
                                link
                            )

                        articles.append({
                            "source": source["name"],
                            "category": source["category"],
                            "title": title,
                            "summary": clean_text(
                                obj.get(
                                    "description",
                                    ""
                                )
                            )[:700],
                            "link": link,
                            "image": image,
                            "published": obj.get(
                                "datePublished",
                                ""
                            ),
                        })

                except Exception:
                    continue

            # HTML fallback
            for a in soup.find_all(
                "a",
                href=True
            ):

                title = clean_text(
                    a.get_text(
                        " ",
                        strip=True
                    )
                )

                href = a.get(
                    "href",
                    ""
                )

                if (
                    not title
                    or len(title) < 20
                ):
                    continue

                link = urljoin(
                    source["url"],
                    href
                )

                if link in seen:
                    continue

                if source["name"] == "KUN.UZ":

                    valid = "/news/" in link

                else:

                    valid = (
                        "daryo.uz/" in link
                        and link.rstrip("/")
                        != "https://daryo.uz"
                    )

                if not valid:
                    continue

                seen.add(link)

                # Avval linkning o'z HTML blokidan
                image = extract_html_image(
                    a,
                    link
                )

                summary = ""

                # Keyin maqolaning o'z sahifasidan
                if not image:

                    image = await fetch_article_image(
                        session,
                        link
                    )

                # Link ichidagi ota elementdan ham izlash
                if not image:

                    parent = a.parent

                    if parent:
                        image = extract_html_image(
                            parent,
                            link
                        )

                articles.append({
                    "source": source["name"],
                    "category": source["category"],
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "image": image,
                    "published": "",
                })

            print(
                f"[HTML] {source['name']}: "
                f"{len(articles)} ta"
            )

            return articles

    except Exception as e:

        print(
            f"[HTML ERROR] "
            f"{source['name']}: {e}"
        )

        return []


async def get_latest_news(
    category=None
):

    active_sources = [
        source
        for source in SOURCES
        if source.get(
            "active",
            True
        )
    ]

    tasks = []

    for source in active_sources:

        if source.get("type") == "html":

            tasks.append(
                fetch_html_source(
                    source
                )
            )

        else:

            tasks.append(
                fetch_feed(
                    source
                )
            )

    results = await asyncio.gather(
        *tasks,
        return_exceptions=True
    )

    articles = []

    for result in results:

        if isinstance(
            result,
            Exception
        ):
            continue

        articles.extend(result)

    if category:

        articles = [
            article
            for article in articles
            if article.get(
                "category"
            ) == category
        ]

    # Link duplicate
    unique = {}

    for article in articles:

        link = article.get(
            "link"
        )

        if not link:
            continue

        if link not in unique:
            unique[link] = article

    articles = list(
        unique.values()
    )

    # Title duplicate
    titles = set()
    clean_articles = []

    for article in articles:

        title = (
            article.get(
                "title",
                ""
            )
            .strip()
            .lower()
        )

        if not title:
            continue

        if title in titles:
            continue

        titles.add(title)
        clean_articles.append(
            article
        )

    articles = clean_articles

    random.shuffle(
        articles
    )

    return articles
