"""Conversation engine on a set of realistic customer messages (EN/DE/RU) + unit checks."""
import json
from pathlib import Path

import pytest

from app.engine import is_unhappy
from app.graph import CommentEvent
from app.i18n import detect, money
from conftest import START, Chat

CASES = [json.loads(line) for line in (Path(__file__).parent / "data" / "messages.jsonl").read_text().splitlines()
         if line.strip()]

MARK = {  # how each answer type shows up in the reply, per language
    "price": {"en": ["Our prices", "€"], "de": ["Unsere Preise", " €"], "ru": ["Наши цены", " €"]},
    "sizes": {"en": ["Sizes:"], "de": ["Größen:"], "ru": ["Размеры:"]},
    "ship": {"en": ["Delivery in Germany"], "de": ["Versand in Deutschland"], "ru": ["Доставка по Германии"]},
    "addr": {"en": ["Our store:"], "de": ["Unser Laden:"], "ru": ["Наш магазин:"]},
    "lead": {"en": ["What's your name?"], "de": ["Wie heißt du?"], "ru": ["Как вас зовут?"]},
    "human": {"en": ["I've asked Lena"], "de": ["Lena aus unserem Team ist informiert"], "ru": ["Позвал Lena"]},
    "unhappy": {"en": ["passing this chat to Lena"], "de": ["gebe den Chat an Lena"], "ru": ["Передаю чат Lena"]},
    "unknown_or_menu": {"en": ["?"], "de": ["?"], "ru": ["?"]},
}
EXCLUSIVE = ["price", "sizes", "ship", "addr", "lead", "human", "unhappy"]


@pytest.mark.parametrize("case", CASES, ids=[c["text"][:40] for c in CASES])
def test_real_messages(engine, case):
    chat = Chat(engine, user="u_" + str(abs(hash(case["text"]))))
    chat.say(case["text"])
    out = chat.text()
    lang = case["lang"]
    assert engine.db.user(chat.user)["lang"] == lang
    hello = {"en": "AI assistant", "de": "KI-Assistent", "ru": "ИИ-ассистент"}[lang]
    assert hello in out  # first message always discloses the AI (EU AI Act art. 50)
    for intent in case["expect"]:
        assert any(m in out for m in MARK[intent][lang]), f"{intent} missing in: {out}"
    for other in EXCLUSIVE:
        if other not in case["expect"]:
            first = MARK[other][lang][0]
            assert first not in out, f"unexpected {other} in: {out}"
    if "has" in case:
        assert case["has"] in out


def test_language_switch_mid_chat(chat):
    chat.say("hi")
    assert "Unser Laden" in chat.say("Wo ist euer Laden?")
    assert "Наш магазин" in chat.say("Где магазин?")
    assert "Hours" not in chat.say("ok")  # neutral text keeps the last language
    assert chat.e.db.user("u1")["lang"] == "ru"


def test_greeting_only_once(chat):
    assert "AI assistant" in chat.say("hi")
    assert "AI assistant" not in chat.say("hi again")


def test_menu_buttons_mid_lead_form(chat):
    chat.say("I want to order")
    assert "Delivery in Germany" in chat.tap("SHIP")  # leaves the form
    assert chat.e.db.user("u1")["state"] is None


def test_lead_cancel(chat):
    chat.say("order please")
    assert "cancelled" in chat.tap("CANCEL")
    assert chat.e.leads_out == []


def test_lead_bad_contact_then_skip(chat):
    chat.say("order")
    chat.say("Anna")
    chat.say("Knit, S")
    assert "doesn't look like" in chat.say("tomorrow")
    assert "Request #1" in chat.tap("SKIP")
    assert chat.e.leads_out[0]["contact"] == "" and chat.e.leads_out[0]["item"] == "Knit, S"


def test_lead_in_german(chat):
    chat.say("Ich möchte bestellen")
    assert "Welcher Artikel" in chat.say("Jonas")
    chat.say("Jeans, 32")
    assert "Anfrage #1" in chat.say("jonas@example.de")
    assert chat.e.leads_out[0]["lang"] == "de"


def test_unhappy_during_form_goes_to_human(chat):
    chat.say("order")
    assert "passing this chat" in chat.say("this is ridiculous, nobody answered my last order")
    assert chat.e.db.user("u1")["state"] is None


def test_human_mode_silence_and_timeout(chat, clock):
    chat.say("human")
    assert chat.say("hello?") == ""
    clock.advance(hours=12, minutes=1)
    assert "Our prices" in chat.say("price")


def test_unhappy_lexicon_false_positives():
    for ok in ["not bad", "nicht schlecht", "неплохо", "no complaints, love it", "thanks!!!", "PRICE PLS", "ok"]:
        assert not is_unhappy(ok), ok


@pytest.mark.parametrize("text,lang", [("Привет", "ru"), ("Größe M?", "de"), ("Wie viel kostet das", "de"),
                                       ("how much is it", "en"), ("ok", None), ("👍", None), ("M", None)])
def test_detect(text, lang):
    assert detect(text) == lang


def test_money():
    assert money(89, "en") == "€89" and money(4.9, "de") == "4,90 €" and money(9.9, "en") == "€9.90"


def test_comment_without_username(engine):
    a = engine.on_comment(CommentEvent("c1", "u1", "", "PRICE", "17900000000000001", START))
    assert a.dm.text.startswith("Hi! You asked") and "Wool overshirt" in a.dm.text
