from app.news.sources import SOURCES

import asyncio
import json
import random
import re
import aiohttp
import feedparser

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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

def source_locale(source):
    country = source.get("country", "international")
    language = source.get("language")
    if not language:
        if country == "uzbekistan":
            language = "ru" if source.get("name") in {"Gazeta.uz", "UzNet Press RU"} else "uz"
        elif country == "russia":
            language = "ru"
        elif country == "turkey":
            language = "tr"
        elif country == "india":
            language = "en"
        else:
            language = "en"
    return country, language


def parse_published_date(value="", parsed=None):
    """Convert RSS/HTML publication dates to a naive UTC datetime."""
    if parsed:
        try:
            return datetime(*parsed[:6])
        except (TypeError, ValueError, OverflowError):
            pass

    if not value:
        return None

    try:
        published = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        try:
            published = parsedate_to_datetime(str(value).strip())
        except (TypeError, ValueError, OverflowError):
            return None

    if published.tzinfo is not None:
        published = published.astimezone(timezone.utc).replace(tzinfo=None)

    return published



def clean_text(text: str) -> str:
    if not text:
        return ""
    soup = BeautifulSoup(str(text), "html.parser")
    value = unescape(soup.get_text(" ", strip=True))
    noise = [
        r"\b\d+[.,]?\d*\s*(views?|ko['’]?rish(?:lar)?|просмотр(?:ов|а)?)\b",
        r"\b\d+\s*(seconds?|minutes?|hours?|days?|sekund|daqiqa|soat|kun)\s*(ago|oldin)\b",
        r"\b(share|read more|continue reading|поделиться|читать далее)\b",
    ]
    for pattern in noise:
        value = re.sub(pattern, " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip(" -|•·")


def extract_article_metadata(soup, base_url):
    title = ""
    description = ""
    image = extract_meta_image(soup, base_url)
    if not image:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or script.get_text())
                objects = data if isinstance(data, list) else [data]
                for obj in objects:
                    image = extract_jsonld_image(obj, base_url)
                    if image:
                        break
                    if isinstance(obj, dict) and isinstance(obj.get("@graph"), list):
                        for item in obj["@graph"]:
                            image = extract_jsonld_image(item, base_url)
                            if image:
                                break
                    if image:
                        break
            except (ValueError, TypeError):
                continue
            if image:
                break
    if not image:
        image = extract_article_content_image(soup, base_url)

    for attrs in (
        {"property": "og:title"}, {"name": "twitter:title"}
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            title = clean_text(tag["content"])
            break
    if not title and soup.title:
        title = clean_text(soup.title.get_text(" ", strip=True))

    for attrs in (
        {"property": "og:description"},
        {"name": "description"},
        {"name": "twitter:description"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            description = clean_text(tag["content"])
            if description:
                break

    video = extract_meta_video(soup, base_url)
    published = extract_meta_date(soup)
    return title, description[:700], image, video, published



def extract_meta_video(soup, base_url):
    """Find a public video file or embed URL from common article metadata."""
    for attrs in (
        {"property": "og:video"},
        {"property": "og:video:url"},
        {"property": "og:video:secure_url"},
        {"name": "twitter:player"},
        {"name": "twitter:player:stream"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            value = urljoin(base_url, tag["content"].strip())
            if value.startswith(("https://", "http://")):
                return value

    for video in soup.find_all("video"):
        source = video.find("source")
        candidate = (source.get("src") if source else None) or video.get("src")
        if candidate:
            value = urljoin(base_url, candidate.strip())
            if value.startswith(("https://", "http://")):
                return value

    for iframe in soup.find_all("iframe", src=True):
        src = urljoin(base_url, iframe["src"].strip())
        host = src.lower()
        if any(domain in host for domain in ("youtube.com", "youtu.be", "vimeo.com", "rutube.ru")):
            return src
    return None


def extract_meta_date(soup):
    for attrs in (
        {"property": "article:published_time"},
        {"property": "og:published_time"},
        {"name": "pubdate"},
        {"name": "publishdate"},
        {"name": "date"},
        {"itemprop": "datePublished"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return tag["content"].strip()
    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag:
        return time_tag.get("datetime", "").strip()
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
            objects = data if isinstance(data, list) else [data]
            for obj in objects:
                if not isinstance(obj, dict):
                    continue
                candidates = [obj, *obj.get("@graph", [])] if isinstance(obj.get("@graph", []), list) else [obj]
                for item in candidates:
                    if isinstance(item, dict):
                        value = item.get("datePublished") or item.get("dateCreated")
                        if value:
                            return str(value)
        except (ValueError, TypeError):
            continue
    return None


async def enrich_article(session, article):
    link = article.get("link")
    if not link:
        return article
    try:
        async with session.get(link, timeout=aiohttp.ClientTimeout(total=12), allow_redirects=True) as response:
            if response.status >= 400:
                return article
            body = await response.text(errors="ignore")
            base_url = str(response.url)
        soup = BeautifulSoup(body, "html.parser")
        title, description, image, video, published = extract_article_metadata(soup, base_url)
        if title and len(title) >= 10:
            article["title"] = title
        if description:
            article["summary"] = description
        if image:
            article["image"] = image
        if video:
            article["video_url"] = video
        if published and not article.get("published_at"):
            article["published_at"] = parse_published_date(published)
    except Exception:
        pass
    if not article.get("image"):
        article["image"] = fallback_brand_image(article.get("title", "YIX News"))
    return article


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

            srcset = img.get("srcset") or img.get("data-srcset") or img.get("data-original-srcset")

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


def fallback_brand_image(title="YIX News"):
    # Neutral branded fallback. Article title stays in the card below, so do not duplicate it here.
    return "https://placehold.co/1200x675/e5e7eb/9ca3af.png?text=YIX%20NEWS"


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


def extract_article_content_image(soup, base_url):
    """Extract media from publisher article markup and linked image files."""
    for selector in (
        "article figure", "article picture", "article",
        "[itemprop='articleBody']", "main figure", "main",
        ".article-content", ".article-body", ".post-content",
        ".news-detail", ".single-content",
    ):
        container = soup.select_one(selector)
        image = extract_html_image(container, base_url)
        if image:
            return image
    return extract_html_image(soup, base_url)


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
            "data-original-src",
            "data-lazy",
            "data-echo",
            "data-actualsrc",
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

    # Full-size images linked from article templates (not always present in img.src).
    for anchor in container.find_all("a", href=True):
        candidate = anchor.get("href", "").strip()
        absolute = urljoin(base_url, candidate).lower()
        if (
            any(host in absolute for host in ("data.daryo.uz", "storage.kun.uz"))
            or "nuz.uz/wp-content/uploads/" in absolute
            or any(ext in absolute for ext in (".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"))
        ):
            url = normalize_image(candidate, base_url)
            if url:
                return url

    # picture source
    for source in container.find_all(
        "source"
    ):

        srcset = source.get("srcset") or source.get("data-srcset")

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
            published_parsed = (
                entry.get("published_parsed")
                or entry.get("updated_parsed")
            )

            image = get_image(
                entry,
                source["url"]
            )

            article = {
                "source": source["name"],
                "category": source["category"],
                "country": source_locale(source)[0],
                "language": source_locale(source)[1],
                "title": title,
                "summary": summary[:700],
                "link": link,
                "image": image or fallback_brand_image(title),
                "published_at": parse_published_date(published, published_parsed),
            }
            if not article["image"] or "placehold.co" in str(article["image"]):
                article["image"] = None
            articles.append(article)

        # Enrich more Uzbekistan publisher items because some RSS feeds omit
        # article images and publication dates. Keep strict concurrency/time limits.
        enrich_limit = 24 if source.get("name") in {"Nuz.uz", "Daryo", "KUN.UZ"} else 3
        async with aiohttp.ClientSession(headers=HEADERS) as enrich_session:
            enrich_semaphore = asyncio.Semaphore(4)

            async def bounded_enrich(article):
                async with enrich_semaphore:
                    try:
                        return await asyncio.wait_for(
                            enrich_article(enrich_session, article),
                            timeout=5,
                        )
                    except asyncio.TimeoutError:
                        return article

            candidates = [
                article for article in articles[:enrich_limit]
                if (not article.get("image") or "placehold.co" in str(article.get("image", "")))
                or not article.get("published_at")
            ]
            enriched = await asyncio.gather(*[
                bounded_enrich(article) for article in candidates
            ])
            by_link = {item.get("link"): item for item in enriched}
            articles = [by_link.get(item.get("link"), item) for item in articles]

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
                            "country": source_locale(source)[0],
                            "language": source_locale(source)[1],
                            "title": title,
                            "summary": clean_text(
                                obj.get(
                                    "description",
                                    ""
                                )
                            )[:700],
                            "link": link,
                            "image": image,
                            "published_at": parse_published_date(
                                obj.get("datePublished", "")
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

                # Do not fetch every article page during feed sync.
                # Source-page images are used when available; otherwise the UI fallback is used.
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
                    "country": source_locale(source)[0],
                    "language": source_locale(source)[1],
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "image": image or fallback_brand_image(title),
                    "published_at": None,
                })

            # Daryo and KUN.UZ listing pages often omit media and dates.
            # Visit a bounded set of article pages to recover Open Graph/JSON-LD metadata.
            if source.get("name") in {"Daryo", "KUN.UZ", "Nuz.uz"} and articles:
                enrich_semaphore = asyncio.Semaphore(4)

                async def enrich_html_item(article):
                    async with enrich_semaphore:
                        try:
                            return await asyncio.wait_for(
                                enrich_article(session, article),
                                timeout=8,
                            )
                        except asyncio.TimeoutError:
                            return article

                articles[:24] = await asyncio.gather(*[
                    enrich_html_item(article) for article in articles[:24]
                ])

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

    # Fetch sources in parallel while keeping a bounded number of connections.
    # Five workers caused 40+ sources to exceed the 120-second sync deadline.
    source_semaphore = asyncio.Semaphore(10)

    async def fetch_limited(source):
        async with source_semaphore:
            if source.get("type") == "html":
                return await fetch_html_source(source)
            return await fetch_feed(source)

    results = await asyncio.gather(
        *(fetch_limited(source) for source in active_sources),
        return_exceptions=True,
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

    # Aralashtiramiz
    random.shuffle(articles)

    # Render Free uchun xavfsiz limit
    articles = articles[:300]

    return articles
