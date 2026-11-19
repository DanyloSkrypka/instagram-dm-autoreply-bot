"""Real FastAPI webhook + mocked Graph API (httpx MockTransport)."""
import csv
import json

import httpx
import pytest

from conftest import IG, START, comment, dm, post, sign

BASE = f"https://graph.instagram.com/v27.0/{IG}"


# ---------------------------------------------------------------- verification + signature
def test_verify_handshake(api):
    tc, *_ = api
    ok = tc.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "1158201444"})
    assert ok.status_code == 200 and ok.text == "1158201444"
    bad = tc.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"})
    assert bad.status_code == 403
    assert tc.get("/webhook", params={"hub.mode": "unsubscribe", "hub.verify_token": "verify-me"}).status_code == 403


def test_signature_wrong_secret_rejected(api):
    tc, _, rec = api
    assert post(tc, dm("hi"), secret="attacker").status_code == 401
    assert rec.calls == []


def test_signature_missing_rejected(api):
    tc, _, rec = api
    r = tc.post("/webhook", content=json.dumps(dm("hi")).encode(), headers={"content-type": "application/json"})
    assert r.status_code == 401 and rec.calls == []


def test_signature_on_tampered_body_rejected(api):
    tc, _, rec = api
    raw = json.dumps(dm("hi")).encode()
    sig = sign(raw)
    tampered = raw.replace(b"hi", b"yo")
    r = tc.post("/webhook", content=tampered, headers={"content-type": "application/json", "x-hub-signature-256": sig})
    assert r.status_code == 401 and rec.calls == []


def test_signature_without_prefix_rejected(api):
    tc, _, rec = api
    raw = json.dumps(dm("hi")).encode()
    r = tc.post("/webhook", content=raw, headers={"x-hub-signature-256": sign(raw)[len("sha256="):]})
    assert r.status_code == 401 and rec.calls == []


def test_signature_checked_over_raw_bytes_with_unicode(api):
    tc, _, rec = api
    raw = json.dumps(dm("Привет! Сколько стоит?"), ensure_ascii=False).encode("utf-8")
    assert post(tc, None, raw=raw).status_code == 200
    assert "ИИ-ассистент" in rec.texts()[0]


def test_unsigned_rejected_when_secret_not_configured(settings, clock):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from conftest import Recorder

    settings.app_secret = ""
    rec = Recorder()
    with TestClient(create_app(settings, graph_client=rec.client(), clock=clock)) as tc:
        assert post(tc, dm("hi"), secret="").status_code == 401
    assert rec.calls == []


def test_bad_json_with_valid_signature(api):
    tc, *_ = api
    assert post(tc, None, raw=b"{not json").status_code == 400


# ---------------------------------------------------------------- DMs
def test_first_dm_ai_disclosure_and_menu(api):
    tc, _, rec = api
    assert post(tc, dm("Hello")).status_code == 200
    calls = rec.messages()
    assert len(calls) == 2
    c = calls[0]
    assert c["url"] == f"{BASE}/messages"
    assert c["headers"]["authorization"] == "Bearer IG_TEST_TOKEN"
    assert c["json"]["recipient"] == {"id": "ig_user_1"}
    assert "I'm the AI assistant of Atelier Nord" in c["json"]["message"]["text"]
    qrs = calls[1]["json"]["message"]["quick_replies"]
    assert 1 <= len(qrs) <= 13
    assert all(q["content_type"] == "text" and len(q["title"]) <= 20 for q in qrs)
    assert {q["payload"] for q in qrs} == {"PRICE", "SIZES", "SHIP", "ADDR", "LEAD", "HUMAN"}


def test_quick_reply_payload(api):
    tc, _, rec = api
    post(tc, dm("hi"))
    rec.calls.clear()
    post(tc, dm("Sizes", payload="SIZES"))
    assert "XS · S · M · L · XL · XXL" in rec.texts()[0]


def test_facebook_login_uses_graph_facebook_com(settings, clock):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from conftest import Recorder

    settings.login_type = "facebook"
    settings.api_version = "v27.0"
    rec = Recorder()
    with TestClient(create_app(settings, graph_client=rec.client(), clock=clock)) as tc:
        post(tc, dm("price?"))
    assert rec.calls[0]["url"] == f"https://graph.facebook.com/v27.0/{IG}/messages"


def test_duplicate_delivery_answered_once(api):
    tc, _, rec = api
    post(tc, dm("hi", mid="same"))
    n = len(rec.calls)
    post(tc, dm("hi", mid="same"))  # Meta retries for up to 36 h
    assert len(rec.calls) == n


def test_attachment_without_text(api):
    tc, _, rec = api
    post(tc, dm("hi"))
    rec.calls.clear()
    post(tc, dm(attachments=True))
    assert "only read text" in rec.texts()[0]


def test_long_text_cut_to_1000_bytes(api):
    tc, app, rec = api
    from app.graph import Out, render
    msg = render(Out("Ж" * 900))  # 1800 bytes
    assert len(msg["text"].encode()) <= 1000


def test_echo_of_our_own_message_ignored(api):
    tc, app, rec = api
    post(tc, dm("hi"))
    post(tc, dm("hi", echo=True, mid="m_1"))  # echo of the bot's own message (id from the Send API response)
    assert app.state.db.user("ig_user_1")["mode"] == "bot"


def test_graph_error_does_not_break_webhook(api):
    tc, _, rec = api
    rec.fail_next = 5
    assert post(tc, dm("hi")).status_code == 200
    assert len(rec.calls) == 1  # stopped after the first failure


# ---------------------------------------------------------------- comment keyword -> private reply
def test_price_comment_sends_private_reply_and_public_reply(api):
    tc, app, rec = api
    assert post(tc, comment("PRICE", cid="c100")).status_code == 200
    pr, pub = rec.calls
    assert pr["url"] == f"{BASE}/messages"
    assert pr["json"]["recipient"] == {"comment_id": "c100"}
    text = pr["json"]["message"]["text"]
    assert "@anna.k" in text and "€89" in text and "https://atelier-nord.example/p/wool-overshirt" in text
    assert "AI assistant" in text
    assert pub["url"] == "https://graph.instagram.com/v27.0/c100/replies"
    assert pub["json"] == {"message": "Sent you a DM 📩"}


def test_comment_facebook_login_shape(api):
    tc, _, rec = api
    post(tc, comment("price pls 🙏", cid="c200", style="fb"))
    assert rec.calls[0]["json"]["recipient"] == {"comment_id": "c200"}


@pytest.mark.parametrize("text,ok", [("PRICE", True), ("price?", True), ("Price pls", True), ("PREIS", True),
                                      ("цена", True), ("PRICELESS look", False), ("nice jacket", False),
                                      ("what's the prices", False), ("", False)])
def test_keyword_matching(api, text, ok):
    tc, _, rec = api
    post(tc, comment(text))
    assert bool(rec.calls) is ok


def test_keyword_language(api):
    tc, _, rec = api
    post(tc, comment("PREIS?", user="u_de"))
    assert "Du hast nach dem Preis gefragt" in rec.calls[0]["json"]["message"]["text"]
    assert rec.calls[1]["json"]["message"] == "Wir haben dir eine DM geschickt 📩"
    rec.calls.clear()
    post(tc, comment("ЦЕНА", user="u_ru"))
    assert "Вы спросили цену" in rec.calls[0]["json"]["message"]["text"]
    assert "89 €" in rec.calls[0]["json"]["message"]["text"]


def test_comment_retry_and_second_comment_same_user_no_spam(api):
    tc, _, rec = api
    body = comment("PRICE", cid="c1")
    post(tc, body)
    post(tc, body)                         # retry of the same webhook
    post(tc, comment("PRICE!!", cid="c2"))  # same user, same post
    assert len([c for c in rec.calls if c["url"].endswith("/messages")]) == 1


def test_own_comment_ignored(api):
    tc, _, rec = api
    post(tc, comment("PRICE", user=IG))
    assert rec.calls == []


def test_comment_older_than_7_days_skipped(api):
    tc, app, rec = api
    old = int(START.timestamp() * 1000) - 8 * 86400 * 1000
    post(tc, comment("PRICE", cid="old", ts=old))
    assert rec.calls == []
    assert app.state.db._x("SELECT status FROM comments WHERE comment_id='old'").fetchone()[0] == "expired"


def test_private_reply_rate_limit_750_per_hour(api):
    tc, app, rec = api
    db = app.state.db
    for _ in range(750):
        db.log_private_reply(START.isoformat())
    post(tc, comment("PRICE", cid="busy"))
    assert rec.calls == []
    assert db._x("SELECT status FROM comments WHERE comment_id='busy'").fetchone()[0] == "rate_limited"


def test_unknown_media_gets_catalog(api):
    tc, _, rec = api
    post(tc, comment("PRICE", media="999"))
    text = rec.calls[0]["json"]["message"]["text"]
    assert "Wool overshirt — €89" in text and "Straight jeans — €79" in text


def test_reply_after_private_reply_no_double_greeting(api):
    tc, _, rec = api
    post(tc, comment("PRICE", user="u5"))
    rec.calls.clear()
    post(tc, dm("What sizes do you have?", user="u5"))
    assert len(rec.messages()) == 1 and "Sizes:" in rec.texts()[0]


def test_no_free_form_dm_before_user_writes(api):
    """After a private reply the window is NOT open: the bot can't push more until the user answers."""
    tc, app, rec = api
    post(tc, comment("PRICE", user="u6"))
    rec.calls.clear()
    r = tc.post("/admin/resume/u6", headers={"x-admin-token": "admin-secret"})
    assert r.json()["sent"] == 0 and rec.calls == []


# ---------------------------------------------------------------- handover
def test_unhappy_customer_handover_and_silence(api, settings):
    tc, app, rec = api
    post(tc, dm("hi"))
    rec.calls.clear()
    post(tc, dm("My parcel still hasn't arrived, this is ridiculous"))
    assert "passing this chat to Lena" in rec.texts()[0]
    assert app.state.db.user("ig_user_1")["mode"] == "human"
    assert app.state.db.handovers()[0]["reason"] == "unhappy"
    rec.calls.clear()
    post(tc, dm("hello??"))
    assert rec.calls == []  # bot stays quiet while a human handles it


def test_owner_alert_posted(settings, clock):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from conftest import Recorder

    settings.owner_alert_url = "https://hooks.example/owner"
    got = []

    def hook(req):
        got.append((str(req.url), json.loads(req.content)))
        return httpx.Response(200)

    alert = httpx.AsyncClient(transport=httpx.MockTransport(hook))
    with TestClient(create_app(settings, graph_client=Recorder().client(), clock=clock, alert_client=alert)) as tc:
        post(tc, dm("I want a refund, terrible service"))
    assert len(got) == 1
    url, body = got[0]
    assert url == "https://hooks.example/owner" and body["type"] == "handover" and body["reason"] == "unhappy"


def test_ask_for_human(api):
    tc, app, rec = api
    post(tc, dm("can I talk to a human please"))
    assert any("I've asked Lena" in t for t in rec.texts())
    assert app.state.db.handovers()[0]["reason"] == "asked"


def test_owner_reply_from_inbox_pauses_bot(api):
    tc, app, rec = api
    post(tc, dm("hi"))
    post(tc, dm("Hi! Lena here, let me check that for you", echo=True, mid="inbox_1"))
    assert app.state.db.user("ig_user_1")["mode"] == "human"
    rec.calls.clear()
    post(tc, dm("price?"))
    assert rec.calls == []


def test_resume_requires_admin_token(api):
    tc, app, rec = api
    post(tc, dm("human"))
    assert tc.post("/admin/resume/ig_user_1").status_code == 401
    assert tc.post("/admin/resume/ig_user_1", headers={"x-admin-token": "wrong"}).status_code == 401
    rec.calls.clear()
    r = tc.post("/admin/resume/ig_user_1", headers={"x-admin-token": "admin-secret"})
    assert r.status_code == 200 and "AI assistant is back" in rec.texts()[0]
    rec.calls.clear()
    post(tc, dm("price?"))
    assert "Our prices" in rec.texts()[0]


def test_admin_disabled_without_token_configured(settings, clock):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from conftest import Recorder

    settings.admin_token = ""
    with TestClient(create_app(settings, graph_client=Recorder().client(), clock=clock)) as tc:
        assert tc.get("/admin/leads", headers={"x-admin-token": ""}).status_code == 401


def test_bot_returns_after_timeout(api, clock):
    tc, app, rec = api
    post(tc, dm("human"))
    clock.advance(hours=13)
    rec.calls.clear()
    post(tc, dm("price?"))
    assert "Our prices" in rec.texts()[0]


def test_resume_outside_24h_window_not_sent(api, clock):
    tc, app, rec = api
    post(tc, dm("human"))
    clock.advance(hours=25)
    rec.calls.clear()
    r = tc.post("/admin/resume/ig_user_1", headers={"x-admin-token": "admin-secret"})
    assert r.json()["sent"] == 0 and rec.calls == []


# ---------------------------------------------------------------- leads -> CSV
def test_lead_flow_writes_csv(api, settings):
    tc, app, rec = api
    post(tc, comment("PRICE", user="u7", username="mia.style"))
    for step in [dict(payload="LEAD", text="Order / reserve"), dict(text="Mia"), dict(text="Wool overshirt, M"),
                 dict(text="+49 151 2345678")]:
        post(tc, dm(user="u7", **step))
    assert "Request #1: Wool overshirt, M" in rec.texts()[-1]
    rows = list(csv.DictReader(open(settings.leads_csv, encoding="utf-8-sig")))
    assert rows == [{"id": "1", "at": "2026-11-19T10:00:00+00:00", "name": "Mia", "item": "Wool overshirt, M",
                     "contact": "+49 151 2345678", "username": "mia.style", "igsid": "u7", "lang": "en",
                     "source": "dm"}]
    assert app.state.db.leads()[0]["name"] == "Mia"


def test_csv_formula_injection_escaped(api, settings):
    tc, *_ = api
    for step in [dict(text="I want to order"), dict(text="=HYPERLINK(\"http://evil\")"), dict(text="@SUM(A1)"),
                 dict(payload="SKIP", text="Skip")]:
        post(tc, dm(**step))
    row = list(csv.DictReader(open(settings.leads_csv, encoding="utf-8-sig")))[0]
    assert row["name"].startswith("'=") and row["item"].startswith("'@") and row["contact"] == ""


def test_admin_leads_endpoint(api):
    tc, *_ = api
    for step in [dict(text="order"), dict(text="Tom"), dict(text="Jeans 32"), dict(text="tom@example.com")]:
        post(tc, dm(**step))
    r = tc.get("/admin/leads", headers={"x-admin-token": "admin-secret"})
    assert r.json()["leads"][0]["contact"] == "tom@example.com"
