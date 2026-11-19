"""FastAPI app: Instagram webhook (messages + comments), owner endpoints, local emulator API.

Run:  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8091
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Callable, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import ROOT, Settings
from .db import DB
from .engine import CommentAction, Engine, load_shop
from .graph import CommentEvent, InstagramClient, MessageEvent, Out, WindowClosed
from .leads import CsvLeads

log = logging.getLogger("ig-dm-bot")


class FakeGraph:
    """Demo-only stand-in for graph.instagram.com: records every call, answers like the real API."""

    def __init__(self):
        self.calls: list[dict] = []
        self.n = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.n += 1
        body = json.loads(request.content or b"{}")
        self.calls.append({"path": request.url.path, "json": body,
                           "auth": request.headers.get("authorization", "")})
        if request.url.path.endswith("/replies"):
            return httpx.Response(200, json={"id": f"demo_reply_{self.n}"})
        rid = body.get("recipient", {}).get("id") or f"igsid_from_comment_{self.n}"
        return httpx.Response(200, json={"recipient_id": rid, "message_id": f"demo_mid_{self.n}"})


def create_app(settings: Optional[Settings] = None, graph_client: Optional[httpx.AsyncClient] = None,
               clock: Optional[Callable[[], datetime]] = None,
               alert_client: Optional[httpx.AsyncClient] = None) -> FastAPI:
    settings = settings or Settings()
    clock = clock or (lambda: datetime.now(timezone.utc))
    db = DB(settings.db_path)
    fake: Optional[FakeGraph] = None
    if graph_client is None and settings.demo_mode and not settings.access_token:
        fake = FakeGraph()  # no token -> nothing ever leaves this machine
        graph_client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    ig = InstagramClient(settings.ig_account_id or "demo_ig_account", settings.access_token or "DEMO_TOKEN",
                         settings.app_secret, settings.graph_host, settings.api_version, client=graph_client)
    alerts: list[dict] = []
    alert_http = alert_client or httpx.AsyncClient(timeout=10)

    def on_handover(h: dict) -> None:
        alerts.append(h)
        log.warning("HANDOVER %s: %s", h["igsid"], h["text"])

    engine = Engine(db, load_shop(settings.shop_file), clock, default_lang=settings.default_lang,
                    keywords=settings.comment_keywords, handover_timeout_h=settings.handover_timeout_h,
                    public_comment_reply=settings.public_comment_reply, own_ig_id=settings.ig_account_id,
                    on_lead=CsvLeads(settings.leads_csv), on_handover=on_handover)

    async def notify_owner(h: dict) -> None:
        if not settings.owner_alert_url:
            return
        try:
            await alert_http.post(settings.owner_alert_url, json={"type": "handover", **h})
        except Exception as e:  # pragma: no cover
            log.warning("owner alert failed: %s", e)

    async def deliver(igsid: str, outs: list[Out]) -> int:
        u = db.user(igsid)
        sent = 0
        for o in outs:
            try:
                r = await ig.send(igsid, o, u["last_in"], clock())
                db.remember_ours(r.get("message_id", ""))
                sent += 1
            except WindowClosed:
                log.info("24h window closed for %s, message not sent", igsid)
                break
            except Exception as e:  # one failed send must not break the webhook
                log.warning("send to %s failed: %s", igsid, e)
                break
        return sent

    async def handle_comment(action: CommentAction, username: str) -> None:
        try:
            r = await ig.private_reply(action.comment_id, action.dm)
            db.remember_ours(r.get("message_id", ""))
            engine.comment_sent(action, username)
        except Exception as e:
            log.warning("private reply %s failed: %s", action.comment_id, e)
            engine.comment_failed(action)
            return
        if action.public_text:
            try:
                await ig.reply_to_comment(action.comment_id, action.public_text)
            except Exception as e:
                log.info("public comment reply failed (DM was sent): %s", e)

    async def dispatch(body: dict) -> None:
        for ev in ig.parse(body):
            try:
                if isinstance(ev, CommentEvent):
                    action = engine.on_comment(ev)
                    if action:
                        await handle_comment(action, ev.username)
                    continue
                if ev.mid and not db.first_time(ev.mid):
                    continue  # duplicate delivery
                if ev.is_echo:
                    engine.on_echo(ev)
                    continue
                n_alerts = len(alerts)
                outs = engine.handle(ev)
                await deliver(ev.igsid, outs)
                for h in alerts[n_alerts:]:
                    await notify_owner(h)
            except Exception as e:  # pragma: no cover
                log.exception("event failed: %s", e)

    app = FastAPI(title="Instagram DM bot")
    app.state.engine = engine
    app.state.db = db
    app.state.ig = ig
    app.state.fake = fake
    app.state.alerts = alerts
    app.state.dispatch = dispatch
    app.state.deliver = deliver

    @app.get("/health")
    async def health():
        return {"ok": True}

    # ---------------- Meta webhook ----------------
    @app.get("/webhook")
    async def verify(request: Request):
        q = request.query_params
        if q.get("hub.mode") == "subscribe" and settings.verify_token and \
                secrets.compare_digest(q.get("hub.verify_token", ""), settings.verify_token):
            return PlainTextResponse(q.get("hub.challenge", ""))
        raise HTTPException(403, "verification failed")

    @app.post("/webhook")
    async def webhook(request: Request, x_hub_signature_256: str = Header(default="")):
        raw = await request.body()
        if not ig.verify_signature(raw, x_hub_signature_256):
            raise HTTPException(401, "bad signature")
        try:
            body = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "bad json")
        await dispatch(body)
        return {"ok": True}

    # ---------------- owner endpoints (protect with ADMIN_TOKEN) ----------------
    def check_admin(token: str) -> None:
        expected = settings.admin_token
        if not expected or not secrets.compare_digest(token, expected):
            raise HTTPException(401, "admin token required")

    @app.post("/admin/resume/{igsid}")
    async def admin_resume(igsid: str, x_admin_token: str = Header(default="")):
        check_admin(x_admin_token)
        n = await deliver(igsid, [engine.resume(igsid)])
        return {"ok": True, "sent": n}

    @app.get("/admin/leads")
    async def admin_leads(x_admin_token: str = Header(default="")):
        check_admin(x_admin_token)
        return {"leads": db.leads(), "handovers": db.handovers()}

    # ---------------- Emulator (local demo only) ----------------
    if settings.demo_mode and fake is not None:
        from .emulator import mount_emulator

        mount_emulator(app, settings, engine, db, fake, dispatch, deliver, clock)
        app.mount("/demo", StaticFiles(directory=str(ROOT / "demo"), html=True), name="demo")

        @app.get("/")
        async def root():
            return RedirectResponse("/demo/")

    return app


app = create_app()
