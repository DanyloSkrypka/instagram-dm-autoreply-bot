"""Instagram Platform client — official Graph API only (graph.instagram.com / graph.facebook.com).

No instagrapi / instabot / browser automation: unofficial clients break Instagram's Terms and get
the business account restricted or disabled.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

log = logging.getLogger("ig-dm-bot")

TEXT_MAX_BYTES = 1000          # message text: UTF-8, 1000 bytes or less
QR_MAX = 13                    # quick replies per message
QR_TITLE = 20                  # quick reply title, characters
WINDOW = timedelta(hours=24)   # standard messaging window after the user's last message
PRIVATE_REPLY_DAYS = 7         # private reply must be sent within 7 days of the comment
PRIVATE_REPLY_PER_HOUR = 750   # per IG professional account, comments on posts and reels


@dataclass
class QuickReply:
    title: str
    payload: str


@dataclass
class Out:
    text: str
    quick_replies: list[QuickReply] = field(default_factory=list)


@dataclass
class MessageEvent:
    igsid: str                 # Instagram-scoped id of the customer
    mid: str
    ts: datetime
    text: str = ""
    payload: Optional[str] = None
    is_echo: bool = False      # sent BY the business account (our bot or a human in the IG inbox)
    nontext: bool = False      # image, audio, story reply without text…


@dataclass
class CommentEvent:
    comment_id: str
    igsid: str
    username: str
    text: str
    media_id: str
    ts: datetime


class WindowClosed(Exception):
    """Free-form messages are allowed only within 24 h of the user's last message."""


def cut_bytes(s: str, limit: int = TEXT_MAX_BYTES) -> str:
    raw = s.encode("utf-8")
    if len(raw) <= limit:
        return s
    ell = "…".encode("utf-8")
    return raw[: limit - len(ell)].decode("utf-8", "ignore") + "…"


def window_open(last_in: Optional[str], now: datetime) -> bool:
    if not last_in:
        return False
    return now - datetime.fromisoformat(last_in) < WINDOW


def render(out: Out) -> dict:
    """Out -> Send API `message` object."""
    msg: dict = {"text": cut_bytes(out.text)}
    if out.quick_replies:
        msg["quick_replies"] = [
            {"content_type": "text", "title": q.title[:QR_TITLE], "payload": q.payload}
            for q in out.quick_replies[:QR_MAX]
        ]
    return msg


def _ts(ms) -> datetime:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


class InstagramClient:
    def __init__(self, ig_account_id: str, token: str, app_secret: str = "", host: str = "graph.instagram.com",
                 api_version: str = "v27.0", client: Optional[httpx.AsyncClient] = None):
        self.ig_id = ig_account_id
        self.token = token
        self.app_secret = app_secret
        self.base = f"https://{host}/{api_version}"
        self.client = client or httpx.AsyncClient(timeout=15)

    # ------------------------------------------------------------ inbound
    def verify_signature(self, raw: bytes, header: str) -> bool:
        """X-Hub-Signature-256: sha256=HMAC_SHA256(app_secret, raw body)."""
        if not self.app_secret:
            return False  # never accept unsigned webhooks; the emulator does not use this route
        expected = "sha256=" + hmac.new(self.app_secret.encode(), raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(header or "", expected)

    def parse(self, body: dict) -> list:
        events: list = []
        if body.get("object") not in ("instagram", "page"):
            return events
        for entry in body.get("entry", []):
            for m in entry.get("messaging", []):
                msg = m.get("message")
                if not msg or msg.get("is_deleted") or msg.get("is_unsupported"):
                    continue
                echo = bool(msg.get("is_echo"))
                user = (m.get("recipient") if echo else m.get("sender")) or {}
                ev = MessageEvent(igsid=str(user.get("id", "")), mid=msg.get("mid", ""), ts=_ts(m.get("timestamp")),
                                  text=msg.get("text") or "", is_echo=echo,
                                  payload=(msg.get("quick_reply") or {}).get("payload"))
                if not ev.text and not ev.payload:
                    ev.nontext = True
                events.append(ev)
            # comments: Instagram Login sends field/value on the entry, Facebook/Unified Login uses changes[]
            changes = list(entry.get("changes", []))
            if entry.get("field"):
                changes.append({"field": entry["field"], "value": entry.get("value", {})})
            for ch in changes:
                if ch.get("field") not in ("comments", "live_comments"):
                    continue
                v = ch.get("value", {})
                frm = v.get("from") or {}
                events.append(CommentEvent(
                    comment_id=str(v.get("id") or v.get("comment_id") or ""),
                    igsid=str(frm.get("id", "")), username=frm.get("username", ""), text=v.get("text", ""),
                    media_id=str((v.get("media") or {}).get("id", "")), ts=_ts(entry.get("time"))))
        return events

    # ------------------------------------------------------------ outbound
    async def _post(self, path: str, payload: dict) -> dict:
        r = await self.client.post(f"{self.base}/{path}", json=payload,
                                   headers={"Authorization": f"Bearer {self.token}"})
        r.raise_for_status()
        return r.json()

    async def send(self, igsid: str, out: Out, last_in: Optional[str], now: datetime) -> dict:
        if not window_open(last_in, now):
            raise WindowClosed(igsid)
        return await self._post(f"{self.ig_id}/messages", {"recipient": {"id": igsid}, "message": render(out)})

    async def private_reply(self, comment_id: str, out: Out) -> dict:
        """One DM to the commenter, addressed by comment_id (allowed without a prior DM)."""
        return await self._post(f"{self.ig_id}/messages",
                                {"recipient": {"comment_id": comment_id}, "message": render(out)})

    async def reply_to_comment(self, comment_id: str, text: str) -> dict:
        return await self._post(f"{comment_id}/replies", {"message": text})
