"""Reddit via RSS (Atom) público.

Uma única requisição cobre vários subreddits ao mesmo tempo
(`/r/sub1+sub2+.../new/.rss`), o que mantém o volume baixíssimo
(1 requisição a cada N minutos). O JSON sem login do Reddit responde 403 e a
API oficial exige aprovação — por isso o RSS, que é o formato oferecido para
leitores de feed.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .. import http
from ..util import html_links, html_to_text, to_iso

ATOM = "{http://www.w3.org/2005/Atom}"


def feed_url(subreddits: list[str], limit: int = 100) -> str:
    return f"https://www.reddit.com/r/{'+'.join(subreddits)}/new/.rss?limit={limit}"


def parse_feed(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise http.CollectError(f"RSS inválido: {e}", transient=True) from e
    items = []
    for e in root.findall(f"{ATOM}entry"):
        post_id = (e.findtext(f"{ATOM}id") or "").strip()  # ex.: t3_1abcde
        link_el = e.find(f"{ATOM}link")
        link = link_el.get("href") if link_el is not None else None
        cat_el = e.find(f"{ATOM}category")
        sub = cat_el.get("term") if cat_el is not None else None
        author = (e.findtext(f"{ATOM}author/{ATOM}name") or "").strip()
        content_html = e.findtext(f"{ATOM}content") or ""
        text = html_to_text(content_html)
        # O RSS do Reddit acrescenta "submitted by /u/x [link] [comments]" no fim
        text = re.sub(r"\s*submitted by\s+/u/\S+.*$", "", text, flags=re.S | re.I).strip()
        links = [l for l in html_links(content_html) if "reddit.com" not in l and not l.startswith("/")]
        title = (e.findtext(f"{ATOM}title") or "").strip()
        published = e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated")
        if not post_id or not link:
            continue
        items.append(
            {
                "source_item_id": post_id,
                "title": title,
                "description": text,
                "source_url": link,
                "apply_url": link,  # no Reddit a candidatura é por DM/comentário no post
                "published_at": to_iso(published),
                "author": author.replace("/u/", "u/") if author else None,
                "company": None,
                "community": f"r/{sub}" if sub else None,
                "location_text": None,
                "tags": [],
                "comp": None,
                "employment_type": None,
                "post_kind_hint": None,
                "raw": {"id": post_id, "subreddit": sub, "external_links": links[:10]},
            }
        )
    return items


def collect(source: dict) -> list[dict]:
    subs = source["params"]["subreddits"]
    xml_text = http.get_text(feed_url(subs, source["params"].get("limit", 100)))
    return parse_feed(xml_text)
