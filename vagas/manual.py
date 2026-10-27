"""Importação manual: para fontes que não podem ser coletadas automaticamente
(Discord de terceiros, YT Jobs, X/Twitter, grupos fechados).

Você cola o link e o texto da vaga; o sistema extrai, deduplica e pontua do
mesmo jeito que as fontes automáticas. Nada é buscado na internet aqui.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import db
from .pipeline import ingest_items
from .util import iso, sha1

MANUAL_SOURCE = {
    "id": "manual",
    "name": "Importação manual",
    "type": "manual",
    "method": "manual",
    "url": None,
    "enabled": True,
    "interval_min": 525600,
    "relevance_gate": "editor_community",
    "default_remote": True,
    "base_legit": 0.7,
    "params": {},
}

DISCORD_LINK = re.compile(r"discord(?:app)?\.com/channels/(\d+|@me)/(\d+)/(\d+)")
DISCORD_EPOCH_MS = 1420070400000


def snowflake_time(snowflake: str | int) -> datetime:
    """Data exata em que uma mensagem do Discord foi criada (vem embutida no ID)."""
    return datetime.fromtimestamp(((int(snowflake) >> 22) + DISCORD_EPOCH_MS) / 1000, timezone.utc)


def label_for(url: str | None) -> str:
    u = (url or "").lower()
    for key, name in (("discord.com", "Discord"), ("ytjobs.co", "YT Jobs"), ("x.com", "X"), ("twitter.com", "X"),
                      ("reddit.com", "Reddit"), ("linkedin.com", "LinkedIn"), ("upwork.com", "Upwork")):
        if key in u:
            return name
    return "Outro"


def import_job(conn, profile: dict, *, url: str | None, text: str, title: str | None = None,
               author: str | None = None, community: str | None = None, published_at: str | None = None) -> dict:
    text = (text or "").strip()
    if not text and not title:
        raise ValueError("cole pelo menos o texto ou o título da vaga")
    if not title:
        first = next((l.strip() for l in text.splitlines() if l.strip()), "")
        title = first[:140]
    item_id = None
    m = DISCORD_LINK.search(url or "")
    if m:
        item_id = f"discord:{m.group(3)}"
        published_at = published_at or iso(snowflake_time(m.group(3)))
        community = community or f"Discord · servidor {m.group(1)}"
    item = {
        "source_item_id": item_id or f"manual:{sha1(url or '', title, text[:500])[:16]}",
        "title": title,
        "description": text,
        "source_url": url,
        "apply_url": url,
        "published_at": published_at,
        "author": author,
        "company": None,
        "community": community or label_for(url),
        "location_text": None,
        "tags": [],
        "comp": None,
        "employment_type": None,
        # importação manual = você já decidiu que é uma vaga
        "post_kind_hint": "hiring",
        "raw": {"imported": True},
    }
    db.sync_sources(conn, [MANUAL_SOURCE])
    stats = ingest_items(conn, MANUAL_SOURCE, [item], profile, first_sync=False)
    row = conn.execute("SELECT * FROM jobs WHERE source_id='manual' AND source_item_id=?", (item["source_item_id"],)).fetchone()
    return {"created": bool(stats["new"]), "job": db.row_to_dict(row)}
