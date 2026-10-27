"""Deduplicação: URL canônica, ID da fonte, título normalizado + autor/empresa,
e similaridade de texto. Repostagens NÃO são apagadas: ficam ligadas à vaga
original pelo campo `duplicate_of`.
"""
from __future__ import annotations

import re
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .util import iso, jaccard, normalize_title, sha1, tokens, utcnow

TRACKING = re.compile(r"^(utm_|ref$|ref_|source$|src$|fbclid|gclid|mc_|share_id|context$|ut$)", re.I)


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("old.").removeprefix("new.")
    path = parts.path or "/"
    m = re.search(r"/comments/([a-z0-9]+)", path, re.I)
    if host.endswith("reddit.com") and m:
        return f"reddit:{m.group(1).lower()}"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not TRACKING.match(k)])
    path = re.sub(r"/+$", "", path) or "/"
    return urlunsplit(("https", host, path, query, ""))


def fingerprint(title: str | None, who: str | None) -> str:
    """Mesmo título normalizado + mesmo autor/empresa => mesma vaga."""
    return sha1(normalize_title(title), (who or "").lower().strip())


def find_duplicate(conn, job: dict, window_days: int = 21) -> tuple[int | None, str | None]:
    """Procura uma vaga já existente que seja a mesma oportunidade.

    Retorna (id_da_original, motivo) ou (None, None).
    """
    cu = job.get("canonical_url")
    if cu:
        r = conn.execute(
            "SELECT id, duplicate_of FROM jobs WHERE canonical_url=? AND NOT (source_id=? AND source_item_id=?) ORDER BY id LIMIT 1",
            (cu, job["source_id"], job["source_item_id"]),
        ).fetchone()
        if r:
            return (r["duplicate_of"] or r["id"]), "mesma URL"
    cutoff = iso(utcnow() - timedelta(days=window_days))
    fp = job.get("fingerprint")
    if fp and (job.get("author") or job.get("company")):
        r = conn.execute(
            "SELECT id, duplicate_of FROM jobs WHERE fingerprint=? AND discovered_at >= ? ORDER BY id LIMIT 1",
            (fp, cutoff),
        ).fetchone()
        if r:
            return (r["duplicate_of"] or r["id"]), "mesmo título e mesmo autor/empresa"
    # Similaridade: mesmo autor/empresa com texto muito parecido, ou texto praticamente idêntico
    t_tokens = tokens(job.get("title"))
    d_tokens = tokens((job.get("description") or "")[:3000])
    if len(d_tokens) < 15:
        return None, None
    rows = conn.execute(
        """SELECT id, duplicate_of, title, description, author, company FROM jobs
            WHERE discovered_at >= ? ORDER BY id DESC LIMIT 1500""",
        (cutoff,),
    ).fetchall()
    who = (job.get("author") or job.get("company") or "").lower()
    for r in rows:
        dj = jaccard(d_tokens, tokens((r["description"] or "")[:3000]))
        tj = jaccard(t_tokens, tokens(r["title"]))
        same_who = who and who == (r["author"] or r["company"] or "").lower()
        if (same_who and (dj >= 0.6 or tj >= 0.8)) or (dj >= 0.85 and tj >= 0.5):
            return (r["duplicate_of"] or r["id"]), f"texto {dj:.0%} igual" + (" e mesmo autor" if same_who else "")
    return None, None
