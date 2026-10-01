import asyncio
import os
import aiohttp

TRANSLATE_URL = os.getenv(
    "TRANSLATE_URL",
    "http://127.0.0.1:5000"
).rstrip("/")

SUPPORTED_LANGUAGES = {
    "uz": "🇺🇿 O‘zbekcha",
    "en": "🇬🇧 English",
    "ru": "🇷🇺 Русский",
}

DEFAULT_LANGUAGE = "uz"


async def translate_text(
    text: str,
    target: str = DEFAULT_LANGUAGE,
    source: str = "auto",
) -> str:
    """
    Matnni LibreTranslate orqali tarjima qiladi.
    Xatolik bo'lsa original matn qaytariladi.
    """

    if not text:
        return ""

    target = target.lower().strip()

    if target not in SUPPORTED_LANGUAGES:
        target = DEFAULT_LANGUAGE

    # O'zbekchaga tarjima talab qilinmasa ham,
    # keyinchalik source detection bilan ishlashi uchun qoldiramiz.
    payload = {
        "q": text,
        "source": source,
        "target": target,
        "format": "text",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=60)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{TRANSLATE_URL}/translate",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "YIX-News/1.0",
                },
            ) as response:

                if response.status != 200:
                    error_text = await response.text()
                    print(
                        f"⚠️ Translation HTTP {response.status}: "
                        f"{error_text[:300]}"
                    )
                    return text

                data = await response.json()

                translated = data.get("translatedText")

                if not translated:
                    print("⚠️ Translation javobida translatedText yo‘q")
                    return text

                return translated.strip()

    except asyncio.TimeoutError:
        print("⚠️ Translation timeout")
        return text

    except Exception as e:
        print(f"⚠️ Translation xatosi: {e}")
        return text


async def translate_article(article: dict, target: str):
    """
    Maqolaning title va summary qismini tarjima qiladi.
    Original article dict o'zgarmaydi.
    """

    result = dict(article)

    if target == DEFAULT_LANGUAGE:
        return result

    title = article.get("title", "")
    summary = article.get("summary", "")

    result["title"] = await translate_text(
        title,
        target=target,
        source="auto",
    )

    if summary:
        result["summary"] = await translate_text(
            summary,
            target=target,
            source="auto",
        )

    return result
