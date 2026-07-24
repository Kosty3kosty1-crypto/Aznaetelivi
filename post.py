# -*- coding: utf-8 -*-
"""
Публикует один пост "А знаете ли вы?" в Telegram-канал, с 2-3 тематическими
картинками (альбомом), которые скрипт сам находит на Wikipedia через
бесплатный публичный API (без ключей и токенов).

Факт выбирается по номеру дня (порядковый номер даты), чтобы каждый день
был новым и без повторов, пока не закончится список — потом список
начинает повторяться по кругу. Для ручного теста можно передать переменную
окружения FACT_INDEX с конкретным номером факта.
"""
import os
import sys
import json
import datetime
import urllib.parse

import requests

from facts import FACTS

HEADER = "🤔 <b>А знаете ли вы?</b>"
TELEGRAM_CAPTION_LIMIT = 1024  # лимит Telegram для подписи к фото/альбому
IMAGES_PER_POST = 3
USER_AGENT = "daily-fact-telegram-bot/1.0 (contact: channel owner)"
HEADERS = {"User-Agent": USER_AGENT}

# Файлы-иконки/служебные картинки Wikipedia, которые не нужны в постах
BAD_IMAGE_KEYWORDS = [
    "commons-logo", "wiktionary", "wikidata", "edit-clear", "question_book",
    "ambox", "padlock", "wikisource-logo", "wikinews", "wikiquote",
    "ooui", "symbol_", "sound-icon", "loudspeaker", "folder_hexagonal",
    "disambig", "merge-arrows", "nuvola", "wiki_letter", "broom_icon",
    "text_document", "increase", "decrease", "steady", "crystal_clear",
]


def pick_fact() -> tuple[dict, int]:
    # Если задан FACT_INDEX (ручной запуск с параметром) — берём факт по номеру,
    # это удобно для тестов. Иначе выбираем по дате, как в обычном ежедневном режиме.
    override = os.environ.get("FACT_INDEX", "").strip()
    if override:
        try:
            idx = int(override) % len(FACTS)
            return FACTS[idx], idx
        except ValueError:
            print(f"Некорректный FACT_INDEX={override!r}, использую выбор по дате", file=sys.stderr)

    day_index = datetime.date.today().toordinal() % len(FACTS)
    return FACTS[day_index], day_index


def _is_good_image(page_title: str, info: dict) -> bool:
    if info.get("mime") not in ("image/jpeg", "image/png"):
        return False
    if (info.get("width") or 0) < 300 or (info.get("height") or 0) < 300:
        return False
    low = page_title.lower()
    return not any(bad in low for bad in BAD_IMAGE_KEYWORDS)


def find_openverse_images(query: str, count: int) -> list[str]:
    """Ищет свободно лицензированные фото на Openverse — это агрегатор
    Flickr, музейных архивов и других сайтов (не только Wikimedia).
    Не требует ключа API."""
    if not query or count <= 0:
        return []
    try:
        resp = requests.get(
            "https://api.openverse.org/v1/images/",
            params={
                "q": query,
                # только лицензии, которые разрешают повторную публикацию и изменение
                "license_type": "commercial,modification",
                "mature": "false",
                "page_size": 10,
            },
            headers=HEADERS,
            timeout=15,
        )
        if not resp.ok:
            return []
        images = []
        for item in resp.json().get("results", []):
            url = item.get("url")
            if not url:
                continue
            if url.lower().split("?")[0].endswith((".svg", ".gif")):
                continue
            images.append(url)
            if len(images) >= count:
                break
        return images
    except requests.RequestException:
        return []


def find_wikipedia_images(title: str, count: int) -> list[str]:
    """Находит до `count` картинок в статье Wikipedia (запасной источник —
    у Wikipedia почти всегда есть хотя бы одна релевантная иллюстрация)."""
    if not title or count <= 0:
        return []

    images: list[str] = []

    # 1) Главная иллюстрация статьи (обычно самая релевантная)
    try:
        summary_url = (
            "https://en.wikipedia.org/api/rest_v1/page/summary/"
            + urllib.parse.quote(title.replace(" ", "_"))
        )
        resp = requests.get(summary_url, headers=HEADERS, timeout=15)
        if resp.ok:
            data = resp.json()
            lead = data.get("originalimage") or data.get("thumbnail")
            if lead and lead.get("source"):
                images.append(lead["source"])
    except requests.RequestException:
        pass

    # 2) Остальные картинки со страницы — добираем до нужного количества
    if len(images) < count:
        try:
            resp = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "titles": title,
                    "generator": "images",
                    "gimlimit": 40,
                    "redirects": 1,
                    "prop": "imageinfo",
                    "iiprop": "url|mime|size",
                    "iiurlwidth": 1024,
                    "format": "json",
                },
                headers=HEADERS,
                timeout=15,
            )
            if resp.ok:
                pages = (resp.json().get("query", {}) or {}).get("pages", {}) or {}
                for page in pages.values():
                    infos = page.get("imageinfo") or []
                    if not infos:
                        continue
                    info = infos[0]
                    page_title = page.get("title", "")
                    if not _is_good_image(page_title, info):
                        continue
                    url = info.get("thumburl") or info.get("url")
                    if url and url not in images:
                        images.append(url)
                    if len(images) >= count:
                        break
        except requests.RequestException:
            pass

    return images[:count]


def find_images(title: str, count: int = IMAGES_PER_POST) -> list[str]:
    """Собирает до `count` картинок из разных источников: сперва Openverse
    (Flickr, музейные архивы и другие сайты — не только Wikipedia), затем
    добирает недостающее из самой статьи Wikipedia."""
    if not title:
        return []

    images: list[str] = find_openverse_images(title, count=2)

    if len(images) < count:
        for url in find_wikipedia_images(title, count=count):
            if url not in images:
                images.append(url)
            if len(images) >= count:
                break

    return images[:count]


def send_text(token: str, channel: str, text: str) -> requests.Response:
    return requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": channel, "text": text, "parse_mode": "HTML"},
        timeout=30,
    )


def send_photo(token: str, channel: str, photo_url: str, caption: str) -> requests.Response:
    return requests.post(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data={
            "chat_id": channel,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "HTML",
        },
        timeout=30,
    )


def send_media_group(token: str, channel: str, photo_urls: list[str], caption: str) -> requests.Response:
    media = []
    for i, url in enumerate(photo_urls):
        item = {"type": "photo", "media": url}
        if i == 0:
            item["caption"] = caption
            item["parse_mode"] = "HTML"
        media.append(item)
    return requests.post(
        f"https://api.telegram.org/bot{token}/sendMediaGroup",
        data={"chat_id": channel, "media": json.dumps(media)},
        timeout=30,
    )


def publish(token: str, channel: str, fact_text: str, images: list[str]):
    """Публикует пост: альбом из 2-3 фото -> одно фото -> просто текст.
    На каждом шаге, если Telegram отклонил запрос (например, картинка
    оказалась недоступна), скрипт откатывается на более простой вариант,
    чтобы день без идеальной картинки не срывал публикацию вовсе."""
    full_text = f"{HEADER}\n\n{fact_text}"
    fits_caption = len(full_text) <= TELEGRAM_CAPTION_LIMIT
    caption = full_text if fits_caption else HEADER

    def finish(resp: requests.Response, mode: str):
        if resp.ok and not fits_caption:
            resp = send_text(token, channel, fact_text)
        return resp, mode

    if len(images) >= 2:
        resp = send_media_group(token, channel, images, caption)
        if resp.ok:
            return finish(resp, f"альбом из {len(images)}")
        print(f"sendMediaGroup не удался, пробую одно фото: {resp.text}", file=sys.stderr)

    if len(images) >= 1:
        resp = send_photo(token, channel, images[0], caption)
        if resp.ok:
            return finish(resp, "одно фото")
        print(f"sendPhoto не удался, публикую без картинки: {resp.text}", file=sys.stderr)

    resp = send_text(token, channel, full_text)
    return resp, "только текст"


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel = os.environ.get("TELEGRAM_CHANNEL")
    if not token or not channel:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL не заданы", file=sys.stderr)
        sys.exit(1)

    fact, idx = pick_fact()
    images = find_images(fact.get("wiki"))
    resp, mode = publish(token, channel, fact["text"], images)

    if not resp.ok:
        print(f"Ошибка Telegram API: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)

    print(f"Опубликован факт #{idx} за {datetime.date.today().isoformat()} ({mode})")

    # Ведём небольшой лог — это заодно создаёт ежедневный коммит в репозитории,
    # чтобы GitHub не отключил задачу по расписанию из-за "неактивности".
    with open("log.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.date.today().isoformat()} — факт #{idx}, {mode}\n")


if __name__ == "__main__":
    main()
