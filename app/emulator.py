"""Local emulator API. Builds the same webhook JSON Meta sends and reads back the exact Graph API calls."""
from __future__ import annotations

import itertools
from pathlib import Path

from fastapi import FastAPI, Request

_seq = itertools.count(1)


def mount_emulator(app: FastAPI, settings, engine, db, fake, dispatch, deliver, clock) -> None:
    own = settings.ig_account_id or "demo_ig_account"

    def ms() -> int:
        return int(clock().timestamp() * 1000)

    def dm_body(user: str, msg: dict, echo: bool = False) -> dict:
        sender, recipient = (own, user) if echo else (user, own)
        return {"object": "instagram", "entry": [{"id": own, "time": ms(), "messaging": [
            {"sender": {"id": sender}, "recipient": {"id": recipient}, "timestamp": ms(), "message": msg}]}]}

    def feed(since: int) -> list[dict]:
        out = []
        for i, c in enumerate(fake.calls[since:], start=since):
            j = c["json"]
            if c["path"].endswith("/replies"):
                out.append({"i": i, "kind": "comment_reply", "comment_id": c["path"].split("/")[-2],
                            "text": j.get("message", "")})
                continue
            rec = j.get("recipient", {})
            user = rec.get("id")
            via = "human" if c.get("human") else "send"
            if "comment_id" in rec:
                row = db._x("SELECT igsid FROM comments WHERE comment_id=?", (rec["comment_id"],)).fetchone()
                user, via = (row[0] if row else None), "private_reply"
            out.append({"i": i, "kind": "dm", "user": user, "via": via, "request": {
                "url": "POST https://" + settings.graph_host + c["path"], "body": j}})
        return out

    def state() -> dict:
        csv_path = Path(settings.leads_csv)
        users = {r["igsid"]: r["mode"] for r in db._x("SELECT igsid, mode FROM users").fetchall()}
        return {"leads": db.leads(), "handovers": db.handovers(), "modes": users,
                "csv": csv_path.read_text(encoding="utf-8-sig") if csv_path.exists() else ""}

    @app.post("/api/emu/reset")
    async def reset():
        for tbl in ("users", "seen_mids", "our_mids", "comments", "leads", "handovers", "private_reply_log"):
            db._x(f"DELETE FROM {tbl}")
        db._x("DELETE FROM sqlite_sequence")
        fake.calls.clear()
        app.state.alerts.clear()
        Path(settings.leads_csv).unlink(missing_ok=True)
        return {"ok": True}

    @app.post("/api/emu/comment")
    async def comment(request: Request):
        b = await request.json()
        body = {"object": "instagram", "entry": [{"id": own, "time": ms(), "field": "comments", "value": {
            "id": f"comment_{next(_seq)}", "from": {"id": b["user"], "username": b.get("username", "")},
            "text": b["text"][:300], "media": {"id": b.get("media_id", ""), "media_product_type": "FEED"}}}]}
        await dispatch(body)
        return {"ok": True}

    @app.post("/api/emu/dm")
    async def dm(request: Request):
        b = await request.json()
        msg = {"mid": f"emu_in_{next(_seq)}", "text": (b.get("text") or "")[:1000]}
        if b.get("payload"):
            msg["quick_reply"] = {"payload": b["payload"]}
        await dispatch(dm_body(b["user"], msg))
        return {"ok": True}

    @app.post("/api/emu/owner_reply")
    async def owner_reply(request: Request):
        """The owner answers from the Instagram inbox: Meta delivers it to us as an echo (not our mid)."""
        b = await request.json()
        mid = f"inbox_{next(_seq)}"
        fake.calls.append({"path": f"/inbox/{own}", "human": True,
                           "json": {"recipient": {"id": b["user"]}, "message": {"text": b["text"][:1000]}}})
        await dispatch(dm_body(b["user"], {"mid": mid, "text": b["text"], "is_echo": True}, echo=True))
        return {"ok": True}

    @app.post("/api/emu/resume")
    async def resume(request: Request):
        b = await request.json()
        await deliver(b["user"], [engine.resume(b["user"])])
        return {"ok": True}

    @app.get("/api/emu/poll")
    async def poll(since: int = 0):
        return {"events": feed(since), "next": len(fake.calls), **state()}
