"""Settings come only from environment variables (.env). Nothing secret lives in the code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv is optional at runtime
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

ROOT = Path(__file__).resolve().parent.parent


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


@dataclass
class Settings:
    # --- Instagram Platform (official API only) ---
    # "instagram" = Instagram API with Instagram Login (graph.instagram.com, no Facebook Page needed)
    # "facebook"  = Instagram API with Facebook Login (graph.facebook.com, IG account linked to a Page)
    login_type: str = field(default_factory=lambda: os.getenv("IG_LOGIN_TYPE", "instagram"))
    ig_account_id: str = field(default_factory=lambda: os.getenv("IG_ACCOUNT_ID", ""))
    access_token: str = field(default_factory=lambda: os.getenv("IG_ACCESS_TOKEN", ""))
    app_secret: str = field(default_factory=lambda: os.getenv("IG_APP_SECRET", ""))
    verify_token: str = field(default_factory=lambda: os.getenv("IG_VERIFY_TOKEN", ""))
    api_version: str = field(default_factory=lambda: os.getenv("GRAPH_API_VERSION", "v27.0"))

    # --- Business ---
    shop_file: str = field(default_factory=lambda: os.getenv("SHOP_FILE", str(ROOT / "app" / "shop.json")))
    default_lang: str = field(default_factory=lambda: os.getenv("DEFAULT_LANG", "en"))
    comment_keywords: list[str] = field(default_factory=lambda: _list("COMMENT_KEYWORDS", "PRICE,PREIS,ЦЕНА"))
    public_comment_reply: bool = field(default_factory=lambda: os.getenv("PUBLIC_COMMENT_REPLY", "1") == "1")
    handover_timeout_h: float = field(default_factory=lambda: float(os.getenv("HANDOVER_TIMEOUT_H", "12")))
    # where a handover alert goes: "log" (default) or a URL that gets a JSON POST (Slack/Telegram relay)
    owner_alert_url: str = field(default_factory=lambda: os.getenv("OWNER_ALERT_URL", ""))

    # owner endpoints (/admin/*): send header X-Admin-Token
    admin_token: str = field(default_factory=lambda: os.getenv("ADMIN_TOKEN", ""))

    # --- Storage ---
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", str(ROOT / "data" / "bot.db")))
    leads_csv: str = field(default_factory=lambda: os.getenv("LEADS_CSV", str(ROOT / "data" / "leads.csv")))

    # --- Local emulator (turn off in production) ---
    demo_mode: bool = field(default_factory=lambda: os.getenv("DEMO_MODE", "1") == "1")

    @property
    def graph_host(self) -> str:
        return "graph.facebook.com" if self.login_type == "facebook" else "graph.instagram.com"
