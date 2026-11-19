"""Conversation logic (no HTTP here): FAQ, comment keyword -> DM, handover to a human, lead form."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .db import DB
from .graph import PRIVATE_REPLY_DAYS, PRIVATE_REPLY_PER_HOUR, CommentEvent, MessageEvent, Out, QuickReply
from .i18n import LANGS, detect, money, t


def load_shop(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _rx(*words: str) -> re.Pattern:
    return re.compile("|".join(words), re.I)


# ---------------------------------------------------------------- intents (EN / DE / RU)
INTENTS: dict[str, re.Pattern] = {
    "price": _rx(r"\bprice", r"\bcost", r"how much", r"\bpreis", r"\bkoste", r"wie ?viel", r"\bwas kostet",
                 r"\bцен[аыуе]", r"\bсколько", r"\bстоит", r"\bпрайс"),
    "sizes": _rx(r"\bsize", r"\b(xs|xl|xxl)\b", r"\bin (s|m|l)\b", r"\bsizing", r"\bfit\b", r"\bgr(ö|oe)(ß|ss)e", r"\bpassform", r"\bразмер", r"\bсетк"),
    "ship": _rx(r"\bdeliver", r"\bshipping", r"\bship\b", r"\bsend to\b", r"\breturn", r"\bversand", r"\bliefer",
                r"\bverschick", r"r(ü|ue)cksend", r"zur(ü|ue)ckschick", r"\bumtausch", r"\bretoure", r"\bдоставк", r"\bотправ", r"\bвозврат(?! денег)"),
    "addr": _rx(r"\baddress", r"\bwhere (is|are)\b", r"\blocation", r"\bopening hours", r"\bstore\b", r"\bvisit\b",
                r"\badresse", r"\bwo (ist|seid|finde)", r"\bladen\b", r"(ö|oe)ffnungszeit", r"\bgeöffnet",
                r"\bадрес", r"\bгде (вы|магазин|находит)", r"\bмагазин\b", r"\bчасы работы"),
    "human": _rx(r"\bhuman\b", r"\breal person", r"\bmanager\b", r"\boperator", r"\bsomeone from",
                 r"\bmensch\b", r"\bmitarbeiter", r"\bechte[rn]? person", r"\bчеловек", r"\bоператор", r"\bменеджер"),
    "lead": _rx(r"\border\b", r"\bbuy\b", r"\breserve", r"\bput aside", r"\bbestell", r"\bkaufen\b", r"\breservier",
                r"\bзаказ", r"\bкупить", r"\bотложит", r"\bзабронир"),
    "hello": _rx(r"^\s*(hi|hello|hey|hallo|moin|servus|guten (tag|morgen|abend)|привет|здравствуй|добрый)\b"),
    "cancel": _rx(r"^\s*(cancel|stop|abbrechen|stopp|отмена|стоп)\s*$"),
}

UNHAPPY = _rx(
    r"\bterrible", r"\bawful", r"\bworst", r"\bhorrible", r"\bangry", r"\bdisappoint", r"\bunacceptable", r"\bscam",
    r"\brefund", r"never (arrived|came)", r"still (haven'?t|hasn'?t|not|no)\b", r"(hasn'?t|has not|not) arrived",
    r"where is my (order|parcel|package)", r"\bnot happy", r"\bunhappy", r"\brude\b",
    r"\bridiculous", r"\bcomplain", r"\bdamaged", r"\bfraud", r"\bwtf\b", r"\bdisgust",
    r"\bschlecht", r"\bschrecklich", r"\bentt(ä|ae)usch", r"\bunversch(ä|ae)mt", r"\bfrechheit", r"\bbetrug",
    r"\breklamation", r"\bbeschwer", r"geld zur(ü|ue)ck", r"\bkaputt", r"\bbesch(ä|ae)digt", r"immer noch nicht",
    r"nie angekommen", r"\b(ä|ae)rgerlich", r"\bsauer\b", r"\bkatastroph",
    r"\bужас", r"(?<![а-яё])плохо", r"\bнедовол", r"\bобман", r"\bмошенн", r"верните деньги", r"возврат денег",
    r"\bжалоб", r"\bбракован", r"\bсломан", r"\bкошмар", r"\bразочаров", r"до сих пор нет", r"\bхамств",
)
NOT_UNHAPPY = _rx(r"not (bad|terrible|awful)", r"nicht schlecht", r"no complaints?")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
PHONE = re.compile(r"^\+?[\d\s\-()/]{7,20}$")


def is_unhappy(text: str) -> bool:
    if not text:
        return False
    clean = NOT_UNHAPPY.sub(" ", text)
    if UNHAPPY.search(clean):
        return True
    letters = [c for c in text if c.isalpha()]
    # long message in CAPS = shouting ("PRICE PLS" is too short to count)
    return len(letters) >= 20 and sum(c.isupper() for c in letters) / len(letters) > 0.7


@dataclass
class CommentAction:
    comment_id: str
    igsid: str
    lang: str
    dm: Out
    public_text: Optional[str]


class Engine:
    def __init__(self, db: DB, shop: dict, clock: Callable[[], datetime], *, default_lang: str = "en",
                 keywords: Optional[list[str]] = None, handover_timeout_h: float = 12,
                 public_comment_reply: bool = True, own_ig_id: str = "",
                 on_lead: Optional[Callable[[dict], None]] = None,
                 on_handover: Optional[Callable[[dict], None]] = None):
        self.db = db
        self.shop = shop
        self.clock = clock
        self.default_lang = default_lang if default_lang in LANGS else "en"
        self.keywords = [k for k in (keywords or ["PRICE"]) if k]
        self.kw_rx = re.compile(r"(?<!\w)(" + "|".join(re.escape(k) for k in self.keywords) + r")(?!\w)", re.I)
        self.handover_timeout = timedelta(hours=handover_timeout_h)
        self.public_comment_reply = public_comment_reply
        self.own_ig_id = own_ig_id
        self.on_lead = on_lead or (lambda lead: None)
        self.on_handover = on_handover or (lambda h: None)

    # ------------------------------------------------------------ helpers
    def eta(self, lang: str) -> str:
        return self.shop["reply_time"].get(lang, self.shop["reply_time"]["en"])

    def menu_qr(self, lang: str) -> list[QuickReply]:
        return [QuickReply(t(k, lang), p) for k, p in (("qr_price", "PRICE"), ("qr_sizes", "SIZES"),
                ("qr_ship", "SHIP"), ("qr_addr", "ADDR"), ("qr_lead", "LEAD"), ("qr_human", "HUMAN"))]

    def product_in(self, text: str) -> Optional[dict]:
        low = text.lower()
        for p in self.shop["products"]:
            if p["name"].lower() in low or any(k in low for k in p["keywords"]):
                return p
        return None

    def ship_text(self, lang: str) -> str:
        s = self.shop["shipping"]
        return t("ship", lang, de_days=s["de_days"], de_price=money(s["de_price"], lang),
                 free_from=money(s["free_from"], lang), eu_days=s["eu_days"], eu_price=money(s["eu_price"], lang),
                 returns_days=s["returns_days"])

    def answer(self, intent: str, lang: str, text: str = "") -> str:
        shop = self.shop
        if intent == "price":
            p = self.product_in(text)
            if p:
                return t("price_one", lang, name=p["name"], price=money(p["price"], lang), sizes=shop["sizes"], url=p["url"])
            lines = "\n".join(f"• {p['name']} — {money(p['price'], lang)}" for p in shop["products"])
            return t("price_all", lang, lines=lines, catalog=shop["catalog_url"])
        if intent == "sizes":
            chart = " · ".join(f"{k}: {v}" for k, v in shop["size_chart"].items())
            return t("sizes", lang, sizes=shop["sizes"], chart=chart)
        if intent == "ship":
            return self.ship_text(lang)
        if intent == "addr":
            return t("addr", lang, address=shop["address"], hours=shop["hours"].get(lang, shop["hours"]["en"]),
                     maps=shop["maps_url"])
        raise KeyError(intent)

    # ------------------------------------------------------------ handover
    def handover(self, u: dict, lang: str, reason: str, text: str) -> list[Out]:
        now = self.clock().isoformat()
        self.db.update_user(u["igsid"], mode="human", human_since=now, state=None, form={})
        h = {"at": now, "igsid": u["igsid"], "username": u.get("username") or "", "reason": reason, "text": text}
        h["id"] = self.db.add_handover(**h)
        self.on_handover(h)
        key = "handover_sorry" if reason == "unhappy" else "handover_ask"
        return [Out(t(key, lang, owner=self.shop["owner_name"], eta=self.eta(lang)))]

    def resume(self, igsid: str) -> Out:
        u = self.db.user(igsid)
        self.db.update_user(igsid, mode="bot", human_since=None)
        lang = u["lang"] or self.default_lang
        return Out(t("bot_back", lang), self.menu_qr(lang))

    # ------------------------------------------------------------ messages
    def on_echo(self, ev: MessageEvent) -> None:
        """A message the business account sent. If it isn't one of ours, a human replied from the inbox."""
        if self.db.is_ours(ev.mid):
            return
        u = self.db.user(ev.igsid)
        if u["mode"] != "human":
            self.db.update_user(ev.igsid, mode="human", human_since=self.clock().isoformat(), state=None, form={})

    def handle(self, ev: MessageEvent) -> list[Out]:
        u = self.db.user(ev.igsid)
        now = self.clock()
        self.db.update_user(ev.igsid, last_in=now.isoformat())

        text = (ev.text or "").strip()
        lang = detect(text) or u["lang"] or self.default_lang
        if lang != u["lang"]:
            self.db.update_user(ev.igsid, lang=lang)

        if u["mode"] == "human":
            since = datetime.fromisoformat(u["human_since"]) if u["human_since"] else now
            if now - since < self.handover_timeout:
                return []  # a person is handling this chat; the bot stays quiet
            self.db.update_user(ev.igsid, mode="bot", human_since=None)

        out: list[Out] = []
        if not u["greeted"]:
            self.db.update_user(ev.igsid, greeted=1)
            out.append(Out(t("hello", lang, shop=self.shop["name"])))

        if ev.nontext:
            return out + [Out(t("nontext", lang), self.menu_qr(lang))]

        payload = ev.payload or ""
        if payload == "HUMAN" or (not payload and INTENTS["human"].search(text)):
            return out + self.handover(u, lang, "asked", text or "(button)")
        if not payload and is_unhappy(text):
            return out + self.handover(u, lang, "unhappy", text)

        if u["state"]:
            return out + self.lead_step(u, lang, text, payload)

        if payload == "LEAD" or (not payload and INTENTS["lead"].search(text)):
            self.db.update_user(ev.igsid, state="lead_name", form={"source": "dm"})
            return out + [Out(t("lead_name", lang), [QuickReply(t("qr_cancel", lang), "CANCEL")])]

        intents = {"PRICE": ["price"], "SIZES": ["sizes"], "SHIP": ["ship"], "ADDR": ["addr"]}.get(payload)
        if intents is None:
            intents = [i for i in ("price", "sizes", "ship", "addr") if INTENTS[i].search(text)]
            product = self.product_in(text)
            if "ship" in intents and "price" in intents and not product:
                intents.remove("price")  # "shipping cost?" is about shipping
            if not intents and product:
                intents = ["price"]
        if intents:
            body = "\n\n".join(self.answer(i, lang, text) for i in intents)
            return out + [Out(body, self.menu_qr(lang))]

        if out or INTENTS["hello"].search(text) or payload == "MENU":
            return out + [Out(t("menu", lang), self.menu_qr(lang))]
        return [Out(t("unknown", lang), self.menu_qr(lang))]

    def lead_step(self, u: dict, lang: str, text: str, payload: str) -> list[Out]:
        igsid, form, state = u["igsid"], dict(u["form"]), u["state"]
        cancel = [QuickReply(t("qr_cancel", lang), "CANCEL")]
        if payload == "CANCEL" or INTENTS["cancel"].search(text):
            self.db.update_user(igsid, state=None, form={})
            return [Out(t("lead_cancel", lang), self.menu_qr(lang))]
        if payload and payload != "SKIP":  # menu button pressed mid-form: leave the form, answer normally
            self.db.update_user(igsid, state=None, form={})
            return self.handle(MessageEvent(igsid=igsid, mid="", ts=self.clock(), payload=payload))
        if state == "lead_name":
            if not text:
                return [Out(t("lead_name", lang), cancel)]
            form["name"] = text[:60]
            self.db.update_user(igsid, state="lead_item", form=form)
            return [Out(t("lead_item", lang, name=form["name"]), cancel)]
        if state == "lead_item":
            if not text:
                return [Out(t("lead_item", lang, name=form.get("name", "")), cancel)]
            form["item"] = text[:120]
            self.db.update_user(igsid, state="lead_contact", form=form)
            return [Out(t("lead_contact", lang), [QuickReply(t("qr_skip", lang), "SKIP")] + cancel)]
        if state == "lead_contact":
            contact = ""
            if payload != "SKIP":
                if not (EMAIL.match(text) or PHONE.match(text)):
                    return [Out(t("lead_bad_contact", lang), [QuickReply(t("qr_skip", lang), "SKIP")] + cancel)]
                contact = text
            lead = {"at": self.clock().isoformat(timespec="seconds"), "igsid": igsid,
                    "username": u.get("username") or "", "name": form.get("name", ""), "item": form.get("item", ""),
                    "contact": contact, "lang": lang, "source": form.get("source", "dm")}
            lead["id"] = self.db.add_lead(**lead)
            self.db.update_user(igsid, state=None, form={})
            self.on_lead(lead)
            return [Out(t("lead_done", lang, id=lead["id"], item=lead["item"], owner=self.shop["owner_name"],
                          eta=self.eta(lang)), self.menu_qr(lang))]
        self.db.update_user(igsid, state=None, form={})  # unknown state: reset
        return [Out(t("menu", lang), self.menu_qr(lang))]

    # ------------------------------------------------------------ comments -> private reply
    def on_comment(self, ev: CommentEvent) -> Optional[CommentAction]:
        if not ev.comment_id or not ev.igsid or ev.igsid == self.own_ig_id:
            return None  # our own replies also arrive as comments: never loop
        m = self.kw_rx.search(ev.text or "")
        if not m:
            return None
        now = self.clock()
        if not self.db.claim_comment(ev.comment_id, ev.igsid, ev.media_id, ev.text, now.isoformat()):
            return None  # webhook retry
        if now - ev.ts > timedelta(days=PRIVATE_REPLY_DAYS):
            self.db.set_comment_status(ev.comment_id, "expired")
            return None
        if self.db.replied_to_user_on_media(ev.igsid, ev.media_id):
            self.db.set_comment_status(ev.comment_id, "already_messaged")
            return None
        if self.db.private_replies_since((now - timedelta(hours=1)).isoformat()) >= PRIVATE_REPLY_PER_HOUR:
            self.db.set_comment_status(ev.comment_id, "rate_limited")
            return None
        kw = m.group(1).lower()
        lang = {"preis": "de", "цена": "ru"}.get(kw) or detect(ev.text.replace(m.group(1), "")) or \
            self.db.user(ev.igsid)["lang"] or self.default_lang
        pid = self.shop.get("media", {}).get(ev.media_id)
        p = next((x for x in self.shop["products"] if x["id"] == pid), None)
        user = f" @{ev.username}" if ev.username else ""
        if p:
            text = t("private_reply", lang, user=user, name=p["name"], price=money(p["price"], lang),
                     sizes=self.shop["sizes"], url=p["url"])
        else:
            text = t("hello", lang, shop=self.shop["name"]) + "\n\n" + self.answer("price", lang)
        qr = [QuickReply(t("qr_sizes", lang), "SIZES"), QuickReply(t("qr_ship", lang), "SHIP"),
              QuickReply(t("qr_lead", lang), "LEAD")]
        return CommentAction(ev.comment_id, ev.igsid, lang, Out(text, qr),
                             t("public_reply", lang) if self.public_comment_reply else None)

    def comment_sent(self, action: CommentAction, username: str) -> None:
        self.db.set_comment_status(action.comment_id, "sent")
        self.db.log_private_reply(self.clock().isoformat())
        u = self.db.user(action.igsid)
        self.db.update_user(action.igsid, greeted=1, lang=u["lang"] or action.lang, username=username or u["username"])

    def comment_failed(self, action: CommentAction) -> None:
        self.db.set_comment_status(action.comment_id, "failed")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
