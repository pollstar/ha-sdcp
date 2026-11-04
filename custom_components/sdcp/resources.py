"""Pure helper for Lovelace resource clean-up (no Home Assistant imports)."""

from __future__ import annotations

from urllib.parse import urlsplit


def plan_removal(items: list[dict], path: str) -> list[str]:
    """Ids of the resources whose URL path is exactly ``path``."""
    return [str(i["id"]) for i in items if urlsplit(i.get("url", "") or "").path == path]
