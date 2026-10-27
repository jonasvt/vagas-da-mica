"""Fontes brasileiras.

- Repositórios de vagas no GitHub (backend-br, frontendbr, datascience-br…):
  cada vaga é uma issue. API oficial do GitHub, 60 requisições/hora sem login.
- Gupy (portal.gupy.io): busca pública por palavra. Os termos de uso vedam
  "agregar, copiar ou duplicar partes" da plataforma — por isso vem DESLIGADA
  em config/bot.json; ligar é decisão sua.
"""
from __future__ import annotations

from .. import http
from ..util import to_iso


def github_issues(source: dict) -> list[dict]:
    out = []
    for repo in source["params"]["repos"]:
        data = http.get_json(f"https://api.github.com/repos/{repo}/issues",
                             params={"state": "open", "per_page": source["params"].get("per_page", 50),
                                     "sort": "created", "direction": "desc"})
        for it in data:
            if "pull_request" in it:  # a API de issues também devolve PRs
                continue
            labels = [l.get("name", "") for l in it.get("labels") or [] if isinstance(l, dict)]
            out.append({
                "source_item_id": f"{repo}#{it['number']}",
                "title": it.get("title"),
                "description": it.get("body") or "",
                "source_url": it.get("html_url"),
                "apply_url": it.get("html_url"),
                "published_at": to_iso(it.get("created_at")),
                "author": (it.get("user") or {}).get("login"),
                "company": None,
                "community": f"GitHub {repo}",
                "location_text": None,
                "tags": labels,
                "comp": None,
                "employment_type": None,
                "post_kind_hint": "hiring",
                "raw": {"labels": labels},
            })
    return out


WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite"}


def gupy(source: dict) -> list[dict]:
    out, seen = [], set()
    for termo in source["params"].get("queries", []):
        data = http.get_json("https://portal.gupy.io/api/job-search/jobs",
                             params={"jobName": termo, "limit": source["params"].get("limit", 50), "offset": 0})
        for j in data.get("data") or []:
            jid = str(j.get("id"))
            if jid in seen:
                continue
            seen.add(jid)
            cidade = ", ".join(x for x in (j.get("city"), j.get("state")) if x)
            out.append({
                "source_item_id": jid,
                "title": j.get("name"),
                "description": j.get("description") or "",
                "source_url": j.get("jobUrl"),
                "apply_url": j.get("jobUrl"),
                "published_at": to_iso(j.get("publishedDate")),
                "author": None,
                "company": j.get("careerPageName"),
                "community": "Gupy",
                "location_text": cidade or "Brasil",
                "tags": [],
                "comp": None,
                "employment_type": None,
                "post_kind_hint": "hiring",
                "raw": {"workplace_type": WORKPLACE.get((j.get("workplaceType") or "").lower(), "unknown"),
                        "type": j.get("type")},
            })
    return out
