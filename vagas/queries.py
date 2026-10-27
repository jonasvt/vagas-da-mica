"""Consultas usadas pelo painel e pela linha de comando."""
from __future__ import annotations

from datetime import timedelta

from . import db
from .util import iso, now_iso, utcnow

USER_STATUSES = ("new", "interested", "applied", "interviewing", "rejected", "closed")
BEST_MAX_AGE_H = 14 * 24

LIST_COLS = """id, source_id, source_name, community, source_url, apply_url, title, company, author, published_at,
    discovered_at, discovery_kind, post_kind, comp_min, comp_max, comp_currency, comp_unit, payment_model,
    employment_type, remote_scope, location_text, specializations, software, experience, legit_confidence,
    legit_flags, score, duplicate_of, user_status, saved, hidden, follow_up_at, applied_at, updated_at,
    (SELECT COUNT(*) FROM jobs d WHERE d.duplicate_of = jobs.id) AS dup_count"""


def _hours_ago(h: float) -> str:
    return iso(utcnow() - timedelta(hours=h))


def list_jobs(conn, f: dict) -> list[dict]:
    where, args = [], []
    view = f.get("view") or "best"
    if not f.get("include_dups"):
        where.append("duplicate_of IS NULL")
    if not f.get("include_hidden") and view != "hidden":
        where.append("hidden = 0")
    if not f.get("include_for_hire"):
        where.append("post_kind != 'for_hire'")

    order = "score DESC, COALESCE(published_at, discovered_at) DESC"
    if view == "new":  # descobertas agora (não importação inicial)
        where.append("discovery_kind IN ('live','manual')")
        where.append("discovered_at >= ?"); args.append(_hours_ago(float(f.get("new_hours") or 48)))
        order = "discovered_at DESC, score DESC"
    elif view == "recent":  # publicadas recentemente (data da fonte)
        where.append("published_at IS NOT NULL")
        order = "published_at DESC"
    elif view == "best":
        where.append("user_status IN ('new','interested')")
        if not f.get("max_age_h"):  # "melhores" = ainda dá tempo de aplicar
            where.append("COALESCE(published_at, discovered_at) >= ?"); args.append(_hours_ago(BEST_MAX_AGE_H))
    elif view == "saved":
        where.append("saved = 1")
        order = "discovered_at DESC"
    elif view == "pipeline":
        where.append("user_status IN ('interested','applied','interviewing')")
        order = "CASE user_status WHEN 'interviewing' THEN 0 WHEN 'applied' THEN 1 ELSE 2 END, COALESCE(follow_up_at,'9999'), applied_at DESC"
    elif view == "hidden":
        where.append("hidden = 1")
    elif view == "all":
        order = "discovered_at DESC"

    if f.get("q"):
        where.append("(title LIKE ? OR description LIKE ? OR company LIKE ? OR author LIKE ?)")
        args += [f"%{f['q']}%"] * 4
    if f.get("exclude"):
        for w in str(f["exclude"]).split(","):
            if w.strip():
                where.append("NOT (title LIKE ? OR description LIKE ?)")
                args += [f"%{w.strip()}%"] * 2
    if f.get("source"):
        where.append("source_id = ?"); args.append(f["source"])
    if f.get("spec"):
        where.append("specializations LIKE ?"); args.append(f'%"{f["spec"]}"%')
    if f.get("software"):
        where.append("software LIKE ?"); args.append(f'%"{f["software"]}"%')
    if f.get("remote"):
        if f["remote"] == "not_restricted":
            where.append("remote_scope IN ('worldwide','unknown')")
        else:
            where.append("remote_scope = ?"); args.append(f["remote"])
    if f.get("payment"):
        where.append("payment_model = ?"); args.append(f["payment"])
    if f.get("currency"):
        cur = f["currency"].upper()
        if cur == "USD":
            where.append("comp_currency IN ('USD','$')")
        else:
            where.append("comp_currency = ?"); args.append(cur)
    if f.get("min_comp"):
        # vagas sem valor informado continuam aparecendo (não são excluídas automaticamente)
        where.append("(comp_max IS NULL OR comp_max >= ?)"); args.append(float(f["min_comp"]))
    if f.get("has_comp"):
        where.append("comp_max IS NOT NULL")
    if f.get("employment"):
        where.append("employment_type LIKE ?"); args.append(f"%{f['employment']}%")
    if f.get("experience"):
        where.append("experience = ?"); args.append(f["experience"])
    if f.get("status"):
        where.append("user_status = ?"); args.append(f["status"])
    if f.get("min_score"):
        where.append("score >= ?"); args.append(int(f["min_score"]))
    if f.get("max_age_h"):
        where.append("COALESCE(published_at, discovered_at) >= ?"); args.append(_hours_ago(float(f["max_age_h"])))

    sql = f"SELECT {LIST_COLS} FROM jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order} LIMIT ?"
    args.append(min(int(f.get("limit") or 200), 1000))
    return [db.row_to_dict(r) for r in conn.execute(sql, args).fetchall()]


def job_detail(conn, job_id: int) -> dict | None:
    job = db.row_to_dict(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    if not job:
        return None
    root = job["duplicate_of"] or job["id"]
    job["related"] = [
        dict(r) for r in conn.execute(
            """SELECT id, source_name, community, source_url, published_at, discovered_at, dup_reason, duplicate_of
               FROM jobs WHERE (id=? OR duplicate_of=?) AND id != ? ORDER BY discovered_at""",
            (root, root, job_id),
        ).fetchall()
    ]
    job["notifications"] = [dict(r) for r in conn.execute(
        "SELECT kind, channel, sent_at, status FROM notifications WHERE job_id=? ORDER BY id", (job_id,)).fetchall()]
    return job


EDITABLE = {"user_status", "saved", "hidden", "notes", "follow_up_at"}


def update_job(conn, job_id: int, changes: dict) -> dict | None:
    changes = {k: v for k, v in changes.items() if k in EDITABLE}
    if "user_status" in changes and changes["user_status"] not in USER_STATUSES:
        raise ValueError("status inválido")
    if not changes:
        return job_detail(conn, job_id)
    now = now_iso()
    sets = [f"{k}=:{k}" for k in changes]
    if "user_status" in changes:
        sets.append("status_changed_at=:now")
        if changes["user_status"] == "applied":
            sets.append("applied_at=COALESCE(applied_at, :now)")
    with db._lock:
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=:id", {**changes, "now": now, "id": job_id})
        conn.commit()
    return job_detail(conn, job_id)


def sources_health(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT s.*, (SELECT COUNT(*) FROM jobs j WHERE j.source_id=s.id) AS jobs,
                  (SELECT COUNT(*) FROM jobs j WHERE j.source_id=s.id AND j.discovered_at >= ?) AS jobs_24h,
                  (SELECT COUNT(*) FROM runs r WHERE r.source_id=s.id AND r.status!='ok' AND r.started_at >= ?) AS errors_24h
           FROM sources s ORDER BY s.id""",
        (_hours_ago(24), _hours_ago(24)),
    ).fetchall()
    return [dict(r) for r in rows]


def runs(conn, *, errors_only: bool = False, limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM runs"
    if errors_only:
        sql += " WHERE status NOT IN ('ok','running')"
    sql += " ORDER BY id DESC LIMIT ?"
    return [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]


def set_source_enabled(conn, source_id: str, enabled: bool) -> None:
    with db._lock:
        conn.execute("UPDATE sources SET enabled=? WHERE id=?", (1 if enabled else 0, source_id))
        if enabled:
            conn.execute("UPDATE sources SET next_check_at=NULL WHERE id=?", (source_id,))
        conn.commit()


def stats(conn) -> dict:
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    h24 = _hours_ago(24)
    by_status = {r[0]: r[1] for r in conn.execute("SELECT user_status, COUNT(*) FROM jobs GROUP BY user_status")}
    by_kind = {r[0]: r[1] for r in conn.execute("SELECT discovery_kind, COUNT(*) FROM jobs GROUP BY discovery_kind")}
    per_day = [dict(r) for r in conn.execute(
        """SELECT substr(discovered_at,1,10) AS day, COUNT(*) AS n FROM jobs
           WHERE duplicate_of IS NULL GROUP BY day ORDER BY day DESC LIMIT 14""")]
    return {
        "total": one("SELECT COUNT(*) FROM jobs"),
        "unique": one("SELECT COUNT(*) FROM jobs WHERE duplicate_of IS NULL"),
        "duplicates": one("SELECT COUNT(*) FROM jobs WHERE duplicate_of IS NOT NULL"),
        "new_24h": one("SELECT COUNT(*) FROM jobs WHERE duplicate_of IS NULL AND discovery_kind IN ('live','manual') AND discovered_at >= ?", h24),
        "strong_24h": one("SELECT COUNT(*) FROM jobs WHERE duplicate_of IS NULL AND discovered_at >= ? AND score >= 60", h24),
        "applied": sum(by_status.get(s, 0) for s in ("applied", "interviewing")),
        "by_status": by_status,
        "by_kind": by_kind,
        "per_day": per_day,
        "follow_ups_due": one("SELECT COUNT(*) FROM jobs WHERE follow_up_at IS NOT NULL AND follow_up_at <= ? AND user_status IN ('applied','interviewing','interested')", now_iso()[:10] + "T23:59:59Z"),
        "sources": sources_health(conn),
    }
