"""EN / DE / RU texts and language detection."""
from __future__ import annotations

import re

LANGS = ("en", "de", "ru")

T: dict[str, dict[str, str]] = {
    # EU AI Act art. 50: people must know they talk to an AI system
    "hello": {
        "en": "Hi! I'm the AI assistant of {shop} 🤖 I can help with prices, sizes, delivery and our address. "
              "A real person from the team can take over any time — just write \"human\".",
        "de": "Hallo! Ich bin der KI-Assistent von {shop} 🤖 Ich helfe bei Preisen, Größen, Versand und Adresse. "
              "Ein Mensch aus dem Team kann jederzeit übernehmen — schreib einfach \"Mensch\".",
        "ru": "Привет! Я ИИ-ассистент {shop} 🤖 Подскажу цены, размеры, доставку и адрес. "
              "Живой человек из команды может подключиться в любой момент — просто напишите «человек».",
    },
    "menu": {"en": "What can I help with?", "de": "Womit kann ich helfen?", "ru": "Чем помочь?"},
    "qr_price": {"en": "Prices", "de": "Preise", "ru": "Цены"},
    "qr_sizes": {"en": "Sizes", "de": "Größen", "ru": "Размеры"},
    "qr_ship": {"en": "Delivery", "de": "Versand", "ru": "Доставка"},
    "qr_addr": {"en": "Address", "de": "Adresse", "ru": "Адрес"},
    "qr_lead": {"en": "Order / reserve", "de": "Bestellen", "ru": "Заказать"},
    "qr_human": {"en": "Talk to a human", "de": "Mit Mensch reden", "ru": "Позвать человека"},
    "qr_cancel": {"en": "Cancel", "de": "Abbrechen", "ru": "Отмена"},
    "qr_skip": {"en": "Skip", "de": "Überspringen", "ru": "Пропустить"},

    "price_all": {"en": "Our prices:\n{lines}\nAll items: {catalog}",
                  "de": "Unsere Preise:\n{lines}\nAlle Artikel: {catalog}",
                  "ru": "Наши цены:\n{lines}\nВесь каталог: {catalog}"},
    "price_one": {"en": "{name} — {price}. Sizes {sizes}.\nOrder here: {url}",
                  "de": "{name} — {price}. Größen {sizes}.\nHier bestellen: {url}",
                  "ru": "{name} — {price}. Размеры {sizes}.\nЗаказать: {url}"},
    "sizes": {"en": "Sizes: {sizes}.\n{chart}\nBetween two sizes? Take the bigger one — the cut is slim.",
              "de": "Größen: {sizes}.\n{chart}\nZwischen zwei Größen? Nimm die größere — der Schnitt ist schmal.",
              "ru": "Размеры: {sizes}.\n{chart}\nМежду двумя размерами — берите больший, крой приталенный."},
    "ship": {"en": "Delivery in Germany {de_days} days, {de_price} (free from {free_from}). EU {eu_days} days, {eu_price}. "
                   "Returns within {returns_days} days.",
             "de": "Versand in Deutschland {de_days} Tage, {de_price} (ab {free_from} kostenlos). EU {eu_days} Tage, {eu_price}. "
                   "Rückgabe innerhalb von {returns_days} Tagen.",
             "ru": "Доставка по Германии {de_days} дня, {de_price} (бесплатно от {free_from}). ЕС {eu_days} дней, {eu_price}. "
                   "Возврат в течение {returns_days} дней."},
    "addr": {"en": "Our store: {address}\nOpen {hours}\nMap: {maps}",
             "de": "Unser Laden: {address}\nGeöffnet {hours}\nKarte: {maps}",
             "ru": "Наш магазин: {address}\nОткрыто {hours}\nКарта: {maps}"},
    "unknown": {"en": "Sorry, I didn't get that. Pick a topic below or write \"human\" and the team will answer.",
                "de": "Das habe ich nicht verstanden. Wähle unten ein Thema oder schreib \"Mensch\", dann antwortet das Team.",
                "ru": "Не понял вопрос. Выберите тему ниже или напишите «человек» — ответит команда."},
    "nontext": {"en": "Thanks! I can only read text — please describe your question in words.",
                "de": "Danke! Ich kann nur Text lesen — beschreib deine Frage bitte kurz.",
                "ru": "Спасибо! Я понимаю только текст — опишите вопрос словами."},

    # handover
    "handover_sorry": {
        "en": "I'm really sorry about that. I'm passing this chat to {owner} from our team — a person will reply here {eta}.",
        "de": "Das tut mir wirklich leid. Ich gebe den Chat an {owner} aus unserem Team weiter — ein Mensch antwortet hier {eta}.",
        "ru": "Очень жаль, что так вышло. Передаю чат {owner} из нашей команды — человек ответит здесь {eta}.",
    },
    "handover_ask": {
        "en": "Sure! I've asked {owner} from our team to join — a person will reply here {eta}.",
        "de": "Klar! {owner} aus unserem Team ist informiert — ein Mensch antwortet hier {eta}.",
        "ru": "Конечно! Позвал {owner} из команды — человек ответит здесь {eta}.",
    },
    "bot_back": {"en": "The AI assistant is back. Anything else I can help with?",
                 "de": "Der KI-Assistent ist wieder da. Kann ich noch helfen?",
                 "ru": "ИИ-ассистент снова на связи. Чем ещё помочь?"},

    # lead form
    "lead_name": {"en": "Great! What's your name?", "de": "Super! Wie heißt du?", "ru": "Отлично! Как вас зовут?"},
    "lead_item": {"en": "Which item and size, {name}? (e.g. \"Wool overshirt, M\")",
                  "de": "Welcher Artikel und welche Größe, {name}? (z. B. \"Wool overshirt, M\")",
                  "ru": "Какую вещь и размер, {name}? (например «Wool overshirt, M»)"},
    "lead_contact": {"en": "Phone or email so we can confirm? (or tap Skip — we'll answer here)",
                     "de": "Telefon oder E-Mail für die Bestätigung? (oder Überspringen — wir antworten hier)",
                     "ru": "Телефон или e-mail для подтверждения? (или «Пропустить» — ответим здесь)"},
    "lead_bad_contact": {"en": "That doesn't look like a phone or email. Try again or tap Skip.",
                         "de": "Das sieht nicht nach Telefon oder E-Mail aus. Nochmal oder Überspringen.",
                         "ru": "Не похоже на телефон или e-mail. Попробуйте ещё раз или нажмите «Пропустить»."},
    "lead_done": {"en": "Done ✅ Request #{id}: {item}. {owner} will confirm availability {eta}.",
                  "de": "Fertig ✅ Anfrage #{id}: {item}. {owner} bestätigt die Verfügbarkeit {eta}.",
                  "ru": "Готово ✅ Заявка #{id}: {item}. {owner} подтвердит наличие {eta}."},
    "lead_cancel": {"en": "No problem, cancelled.", "de": "Kein Problem, abgebrochen.", "ru": "Хорошо, отменил."},

    # comment -> DM (private reply)
    "private_reply": {
        "en": "Hi{user}! You asked for the price 👋 {name} — {price}. Sizes {sizes}.\nShop it here: {url}\n"
              "(I'm the shop's AI assistant — reply here with any question, or write \"human\".)",
        "de": "Hi{user}! Du hast nach dem Preis gefragt 👋 {name} — {price}. Größen {sizes}.\nHier kaufen: {url}\n"
              "(Ich bin der KI-Assistent des Shops — antworte hier mit deiner Frage oder schreib \"Mensch\".)",
        "ru": "Привет{user}! Вы спросили цену 👋 {name} — {price}. Размеры {sizes}.\nКупить: {url}\n"
              "(Я ИИ-ассистент магазина — задайте вопрос здесь или напишите «человек».)",
    },
    "public_reply": {"en": "Sent you a DM 📩", "de": "Wir haben dir eine DM geschickt 📩", "ru": "Отправили вам в директ 📩"},
}

_DE = re.compile(r"\b(hallo|hi zusammen|moin|servus|ich|du|ihr|euer|eure|wie|was|wo|gibt|habt|kostet|preis|preise|größe|groesse|"
                 r"versand|lieferung|danke|bitte|laden|geschäft|bestellen|kaufen|nicht|schlecht|mensch|und|ist|der|die|das)\b", re.I)
_EN = re.compile(r"\b(hi|hello|hey|the|what|where|how|much|price|prices|size|sizes|delivery|shipping|thanks|please|do|you|"
                 r"is|are|my|order|human|store|shop|i|want|can|have|it|for|one|this|that|need)\b", re.I)


def detect(text: str) -> str | None:
    """Return a language only when the signal is clear; None keeps the previous one."""
    if not text:
        return None
    if re.search(r"[а-яё]", text, re.I):
        return "ru"
    if re.search(r"[äöüß]", text, re.I):
        return "de"
    de, en = len(_DE.findall(text)), len(_EN.findall(text))
    if de > en:
        return "de"
    if en > de:
        return "en"
    return None


def t(key: str, lang: str, **kw) -> str:
    s = T[key].get(lang) or T[key]["en"]
    return s.format(**kw) if kw else s


def money(value: float, lang: str) -> str:
    if float(value).is_integer():
        num = f"{int(value)}"
    else:
        num = f"{value:.2f}"
        if lang != "en":
            num = num.replace(".", ",")
    return f"€{num}" if lang == "en" else f"{num} €"
