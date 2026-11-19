import hashlib
import hmac
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# tests never touch the real database / CSV (app.main builds a default app at import time)
import os  # noqa: E402
import tempfile  # noqa: E402

os.environ["DB_PATH"] = ":memory:"
os.environ["LEADS_CSV"] = os.path.join(tempfile.mkdtemp(prefix="igdm_test_"), "leads.csv")
os.environ["IG_ACCESS_TOKEN"] = ""

from app.config import Settings  # noqa: E402
from app.db import DB  # noqa: E402
from app.engine import Engine, load_shop  # noqa: E402
from app.graph import MessageEvent  # noqa: E402
from app.main import create_app  # noqa: E402

START = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
IG = "17841400000000000"
SECRET = "test-app-secret"


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


class Recorder:
    """httpx MockTransport: records every Graph API call (nothing leaves the machine)."""

    def __init__(self):
        self.calls = []
        self.fail_next = 0

    def __call__(self, request: httpx.Request):
        self.calls.append({"url": str(request.url), "method": request.method,
                           "headers": dict(request.headers), "json": json.loads(request.content or b"{}")})
        if self.fail_next:
            self.fail_next -= 1
            return httpx.Response(400, json={"error": {"message": "(#10) outside allowed window", "code": 10}})
        if request.url.path.endswith("/replies"):
            return httpx.Response(200, json={"id": f"reply_{len(self.calls)}"})
        return httpx.Response(200, json={"recipient_id": "x", "message_id": f"m_{len(self.calls)}"})

    def client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def messages(self):
        return [c for c in self.calls if c["url"].endswith("/messages")]

    def texts(self):
        return [c["json"]["message"]["text"] for c in self.messages()]


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.ig_account_id = IG
    s.access_token = "IG_TEST_TOKEN"
    s.app_secret = SECRET
    s.verify_token = "verify-me"
    s.admin_token = "admin-secret"
    s.db_path = ":memory:"
    s.leads_csv = str(tmp_path / "leads.csv")
    s.demo_mode = False
    s.owner_alert_url = ""
    return s


@pytest.fixture
def api(settings, clock):
    from fastapi.testclient import TestClient

    rec = Recorder()
    app = create_app(settings, graph_client=rec.client(), clock=clock)
    with TestClient(app) as tc:
        yield tc, app, rec


def sign(raw: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def post(tc, body, secret=SECRET, raw=None):
    raw = raw if raw is not None else json.dumps(body).encode()
    return tc.post("/webhook", content=raw,
                   headers={"content-type": "application/json", "x-hub-signature-256": sign(raw, secret)})


_n = [0]


def dm(text="", user="ig_user_1", payload=None, mid=None, echo=False, attachments=False, ts=None):
    _n[0] += 1
    msg = {"mid": mid or f"mid_{_n[0]}"}
    if text:
        msg["text"] = text
    if payload:
        msg["quick_reply"] = {"payload": payload}
    if echo:
        msg["is_echo"] = True
    if attachments:
        msg["attachments"] = [{"type": "image", "payload": {"url": "https://x/y.jpg"}}]
    sender, recipient = (IG, user) if echo else (user, IG)
    t = ts or 1763546400000
    return {"object": "instagram", "entry": [{"id": IG, "time": t, "messaging": [
        {"sender": {"id": sender}, "recipient": {"id": recipient}, "timestamp": t, "message": msg}]}]}


def comment(text, user="ig_user_9", username="anna.k", cid=None, media="17900000000000001", ts=None, style="ig"):
    _n[0] += 1
    cid = cid or f"c_{_n[0]}"
    t = ts if ts is not None else int(START.timestamp() * 1000)
    value = {"from": {"id": user, "username": username}, "text": text,
             "media": {"id": media, "media_product_type": "FEED"}}
    if style == "ig":  # Instagram Login shape
        return {"object": "instagram", "entry": [{"id": IG, "time": t, "field": "comments",
                                                  "value": {"id": cid, **value}}]}
    return {"object": "instagram", "entry": [{"id": IG, "time": t, "changes": [  # Facebook Login shape
        {"field": "comments", "value": {"comment_id": cid, **value}}]}]}


@pytest.fixture
def engine(clock, tmp_path):
    leads = []
    e = Engine(DB(":memory:"), load_shop(str(ROOT / "app" / "shop.json")), clock, own_ig_id=IG,
               keywords=["PRICE", "PREIS", "ЦЕНА"], on_lead=leads.append)
    e.leads_out = leads
    return e


class Chat:
    def __init__(self, engine, user="u1"):
        self.e, self.user, self.last = engine, user, []
        self.n = 0

    def say(self, text):
        self.n += 1
        self.last = self.e.handle(MessageEvent(self.user, f"m{self.n}", self.e.clock(), text=text))
        return self.text()

    def tap(self, payload):
        self.n += 1
        self.last = self.e.handle(MessageEvent(self.user, f"m{self.n}", self.e.clock(), payload=payload))
        return self.text()

    def text(self):
        return "\n".join(o.text for o in self.last)

    def qr(self):
        return [q.payload for o in self.last for q in o.quick_replies]


@pytest.fixture
def chat(engine):
    return Chat(engine)
