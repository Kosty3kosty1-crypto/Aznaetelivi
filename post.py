# -*- coding: utf-8 -*-
"""
Публикует один пост "А знаете ли вы?" в Telegram-канал, с иллюстрацией
по теме факта (картинка берётся из бесплатного публичного API Wikipedia —
ключи и токены для этого не нужны).

Факт выбирается по номеру дня (порядковый номер даты), чтобы каждый день
был новым и без повторов, пока не закончится список — потом список
начинает повторяться по кругу.
"""
import os
import sys
import datetime
import urllib.parse

import requests

from facts import FACTS

HEADER = "🤔 <b>А знаете ли вы?</b>"
TELEGRAM_CAPTION_LIMIT = 1024  # лимит Telegram для подписи к фото
USER_AGENT = "daily-fact-telegram-bot/1.0 (contact: channel owner)"


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


def find_image(title: str) -> str | None:
    """Достаёт URL картинки из Wikipedia REST API по заголовку статьи."""
    if not title:
        return None
    try:
        url = (
            "https://en.wikipedia.org/api/rest_v1/page/summary/"
            + urllib.parse.quote(title.replace(" ", "_"))
        )
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        if not resp.ok:
            return None
        data = resp.json()
        image = data.get("originalimage") or data.get("thumbnail")
        return image.get("source") if image else None
    except requests.RequestException:
        return None


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


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel = os.environ.get("TELEGRAM_CHANNEL")
    if not token or not channel:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL не заданы", file=sys.stderr)
        sys.exit(1)

    fact, idx = pick_fact()
    message = f"{HEADER}\n\n{fact['text']}"
    image_url = find_image(fact.get("wiki"))

    if image_url:
        if len(message) <= TELEGRAM_CAPTION_LIMIT:
            resp = send_photo(token, channel, image_url, message)
        else:
            # Текст не влезает в подпись к фото — публикуем фото с коротким
            # заголовком, а следом отдельным сообщением полный текст.
            resp = send_photo(token, channel, image_url, HEADER)
            if resp.ok:
                resp = send_text(token, channel, fact["text"])
    else:
        # Не нашли подходящую иллюстрацию — публикуем как обычный текстовый пост.
        resp = send_text(token, channel, message)

    if not resp.ok:
        print(f"Ошибка Telegram API: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)

    has_photo = "да" if image_url else "нет"
    print(f"Опубликован факт #{idx} за {datetime.date.today().isoformat()} (фото: {has_photo})")

    # Ведём небольшой лог — это заодно создаёт ежедневный коммит в репозитории,
    # чтобы GitHub не отключил задачу по расписанию из-за "неактивности".
    with open("log.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.date.today().isoformat()} — факт #{idx}, фото: {has_photo}\n")


if __name__ == "__main__":
    main()
