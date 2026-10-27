"""Sites de vagas com API pública ou RSS oficial.

Todos exigem atribuição/link de volta para a vaga original — o sistema
sempre guarda e mostra o link original e o nome da fonte. Uso pessoal apenas
(nada é republicado).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .. import http
from ..util import fix_mojibake, html_to_text, to_iso

UNIT_FROM_PERIOD = {
    "annual": "year", "yearly": "year", "year": "year",
    "monthly": "month", "month": "month",
    "hourly": "hour", "hour": "hour",
    "weekly": "week", "daily": "day",
}


def _num(v):
    try:
        n = float(v)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _comp(lo, hi, currency, unit, text=None):
    lo, hi = _num(lo), _num(hi)
    if lo is None and hi is None:
        return None
    return {
        "min": lo if lo is not None else hi,
        "max": hi if hi is not None else lo,
        "currency": (currency or "").upper() or None,
        "unit": unit or "unknown",
        "text": text or "valor informado pela fonte (campo estruturado)",
    }


def _item(**kw) -> dict:
    base = {
        "source_item_id": None, "title": None, "description": "", "source_url": None,
        "apply_url": None, "published_at": None, "author": None, "company": None,
        "community": None, "location_text": None, "tags": [], "comp": None,
        "employment_type": None, "post_kind_hint": "hiring", "raw": {},
    }
    base.update(kw)
    base["title"] = fix_mojibake(base["title"])
    base["description"] = fix_mojibake(base["description"])
    return base


# ---------------------------------------------------------------- Himalayas
def himalayas(source: dict) -> list[dict]:
    p = source["params"]
    out, seen = [], set()
    for q in p.get("queries", ["video editor"]):
        for page in range(1, int(p.get("pages", 2)) + 1):
            params = {"q": q, "page": page}
            if p.get("country"):  # só vagas abertas a quem está nesse país (inclui worldwide)
                params["country"] = p["country"]
            data = http.get_json("https://himalayas.app/jobs/api/search", params=params)
            jobs = data.get("jobs") or []
            if not jobs:
                break
            for j in jobs:
                guid = j.get("guid")
                if not guid or guid in seen:
                    continue
                seen.add(guid)
                restr = j.get("locationRestrictions") or []
                period = (j.get("salaryPeriod") or "").lower()
                out.append(
                    _item(
                        source_item_id=guid,
                        title=j.get("title"),
                        description=html_to_text(j.get("description")),
                        source_url=guid,
                        apply_url=j.get("applicationLink") or guid,
                        published_at=to_iso(j.get("pubDate")),
                        company=j.get("companyName"),
                        community="Himalayas",
                        location_text=", ".join(restr) if restr else "Sem restrição de país (Himalayas)",
                        tags=(j.get("categories") or []) + (j.get("seniority") or []),
                        comp=_comp(j.get("minSalary"), j.get("maxSalary"), j.get("currency"), UNIT_FROM_PERIOD.get(period, "unknown")),
                        employment_type=j.get("employmentType"),
                        raw={"expiryDate": j.get("expiryDate"), "timezoneRestrictions": j.get("timezoneRestrictions"),
                             "locationRestrictions": restr, "query": q},
                    )
                )
    return out


# ---------------------------------------------------------------- Remote OK
def remoteok(source: dict) -> list[dict]:
    data = http.get_json("https://remoteok.com/api")
    out = []
    for j in data:
        if not isinstance(j, dict) or "id" not in j or "position" not in j:
            continue  # primeiro item é o aviso legal
        out.append(
            _item(
                source_item_id=str(j["id"]),
                title=j.get("position"),
                description=html_to_text(j.get("description")),
                source_url=j.get("url"),
                apply_url=j.get("apply_url") or j.get("url"),
                published_at=to_iso(j.get("date") or j.get("epoch")),
                company=j.get("company"),
                community="Remote OK",
                location_text=j.get("location") or None,
                tags=j.get("tags") or [],
                comp=_comp(j.get("salary_min"), j.get("salary_max"), "USD", "year"),
                raw={"slug": j.get("slug")},
            )
        )
    return out


# ---------------------------------------------------------------- Remotive
def remotive(source: dict) -> list[dict]:
    p = source["params"]
    data = http.get_json("https://remotive.com/api/remote-jobs", params={"search": p.get("search", "video"), "limit": p.get("limit", 100)})
    out = []
    for j in data.get("jobs") or []:
        salary = (j.get("salary") or "").strip()
        desc = html_to_text(j.get("description"))
        if salary:
            desc = f"Salary (Remotive): {salary}\n\n{desc}"
        out.append(
            _item(
                source_item_id=str(j["id"]),
                title=j.get("title"),
                description=desc,
                source_url=j.get("url"),
                apply_url=j.get("url"),
                published_at=to_iso(j.get("publication_date")),
                company=j.get("company_name"),
                community=f"Remotive · {j.get('category')}",
                location_text=j.get("candidate_required_location") or None,
                tags=j.get("tags") or [],
                employment_type=j.get("job_type"),
                raw={"salary": salary or None},
            )
        )
    return out


# ---------------------------------------------------------------- Jobicy
def jobicy(source: dict) -> list[dict]:
    p = source["params"]
    out, seen = [], set()
    for params in p.get("requests", [{"tag": "video editor"}]):
        data = http.get_json("https://jobicy.com/api/v2/remote-jobs", params={"count": 50, **params})
        for j in data.get("jobs") or []:
            jid = str(j.get("id"))
            if jid in seen:
                continue
            seen.add(jid)
            period = (j.get("salaryPeriod") or "").lower()
            jt = j.get("jobType")
            out.append(
                _item(
                    source_item_id=jid,
                    title=j.get("jobTitle"),
                    description=html_to_text(j.get("jobDescription")),
                    source_url=j.get("url"),
                    apply_url=j.get("url"),
                    published_at=to_iso(j.get("pubDate")),
                    company=j.get("companyName"),
                    community="Jobicy",
                    location_text=j.get("jobGeo") or None,
                    tags=(j.get("jobIndustry") or []) if isinstance(j.get("jobIndustry"), list) else [],
                    comp=_comp(j.get("salaryMin"), j.get("salaryMax"), j.get("salaryCurrency"), UNIT_FROM_PERIOD.get(period, "unknown")),
                    employment_type=", ".join(jt) if isinstance(jt, list) else jt,
                    raw={"jobLevel": j.get("jobLevel")},
                )
            )
    return out


# ---------------------------------------------------------------- Working Nomads
def workingnomads(source: dict) -> list[dict]:
    data = http.get_json("https://www.workingnomads.com/api/exposed_jobs/")
    out = []
    for j in data:
        url = j.get("url")
        if not url:
            continue
        tags = j.get("tags")
        out.append(
            _item(
                source_item_id=url.rstrip("/").rsplit("/", 1)[-1],
                title=j.get("title"),
                description=html_to_text(j.get("description")),
                source_url=url,
                apply_url=url,
                published_at=to_iso(j.get("pub_date")),
                company=j.get("company_name"),
                community=f"Working Nomads · {j.get('category_name')}",
                location_text=j.get("location") or None,
                tags=[t.strip() for t in tags.split(",")] if isinstance(tags, str) else (tags or []),
            )
        )
    return out


# ---------------------------------------------------------------- Freelancer.com
def freelancer(source: dict) -> list[dict]:
    p = source["params"]
    out, seen = [], set()
    for q in p.get("queries", ["video editing"]):
        data = http.get_json(
            "https://www.freelancer.com/api/projects/0.1/projects/active/",
            params={"query": q, "limit": p.get("limit", 50), "full_description": "true", "job_details": "true"},
        )
        for pr in (data.get("result") or {}).get("projects") or []:
            pid = str(pr.get("id"))
            if pid in seen:
                continue
            seen.add(pid)
            budget = pr.get("budget") or {}
            cur = (pr.get("currency") or {}).get("code")
            ptype = pr.get("type")  # fixed | hourly
            url = f"https://www.freelancer.com/projects/{pr.get('seo_url')}" if pr.get("seo_url") else None
            out.append(
                _item(
                    source_item_id=pid,
                    title=pr.get("title"),
                    description=pr.get("description") or pr.get("preview_description") or "",
                    source_url=url,
                    apply_url=url,
                    published_at=to_iso(pr.get("time_submitted")),
                    community="Freelancer.com",
                    location_text=None,
                    tags=[jb.get("name") for jb in pr.get("jobs") or [] if isinstance(jb, dict)],
                    comp=_comp(budget.get("minimum"), budget.get("maximum"), cur, "hour" if ptype == "hourly" else "project",
                               text=f"orçamento Freelancer.com ({ptype})"),
                    employment_type="Freelance",
                    raw={"type": ptype, "bid_count": (pr.get("bid_stats") or {}).get("bid_count")},
                )
            )
    return out


# ---------------------------------------------------------------- We Work Remotely
def wwr_rss(source: dict) -> list[dict]:
    out = []
    for url in source["params"]["feeds"]:
        xml_text = http.get_text(url)
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            raise http.CollectError(f"RSS inválido: {e}") from e
        for it in root.iter("item"):
            link = (it.findtext("link") or "").strip()
            title = (it.findtext("title") or "").strip()
            company = None
            m = re.match(r"^(.*?):\s+(.*)$", title)
            if m:
                company, title = m.group(1), m.group(2)
            out.append(
                _item(
                    source_item_id=(it.findtext("guid") or link).strip(),
                    title=title,
                    description=html_to_text(it.findtext("description")),
                    source_url=link,
                    apply_url=link,
                    published_at=to_iso(it.findtext("pubDate")),
                    company=company,
                    community="We Work Remotely",
                    location_text=(it.findtext("region") or "").strip() or None,
                    tags=[(it.findtext("category") or "").strip()],
                )
            )
    return out
