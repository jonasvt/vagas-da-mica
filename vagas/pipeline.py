"""Pipeline de ingestão.

Para cada fonte que está no horário: coleta -> filtra -> extrai -> deduplica ->
pontua -> grava -> notifica. A falha de uma fonte nunca interrompe as outras;
erros temporários entram em backoff exponencial.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import timedelta

from . import db, dedupe, notify
from .collectors import get_collector
from .extract import extract_all
from .http import CollectError, RateLimited
from .scoring import score_job
from .util import hours_since, iso, normalize_title, now_iso, parse_date, sha1, utcnow

log = logging.getLogger("vagas")

# Espaço mínimo entre requisições ao mesmo serviço (segundos)
MIN_GAP = {"reddit_rss": 60}  # o RSS sem login do Reddit responde 429 com 30 s entre requisições
_last_call: dict[str, float] = {}

LIVE_MAX_AGE_H = 48  # publicada há mais tempo que isso => "late" (vista agora, mas antiga)


def _wait_gap(method: str) -> None:
    gap = MIN_GAP.get(method)
    if not gap:
        return
    elapsed = time.monotonic() - _last_call.get(method, -1e9)
    if elapsed < gap:
        time.sleep(gap - elapsed)


def _backoff_minutes(failures: int, interval: int, retry_after_s: float | None) -> float:
    if retry_after_s:
        return max(retry_after_s / 60, 2)
    return min(interval * (2 ** max(failures - 1, 0)), 12 * 60)


def passes_gate(item: dict, src: dict, profile: dict) -> bool:
    gate = src.get("relevance_gate", "video_keywords")
    if gate == "editor_community":
        return True
    rx = re.compile(profile.get("video_keywords", "video|editor"), re.I)
    if rx.search(item.get("title") or ""):
        return True
    # Tags só contam onde são confiáveis (ex.: categorias do Himalayas); no Remote OK
    # até vaga de analista vem marcada com "video".
    if "tags" in src.get("gate_fields", ()):
        return bool(rx.search(" ".join(map(str, item.get("tags") or []))))
    return False


def content_hash(item: dict) -> str:
    return sha1(item.get("title"), item.get("description"), (item.get("comp") or {}).get("text"))


def build_record(item: dict, src: dict, profile: dict) -> dict:
    """Item bruto -> registro normalizado (sem decidir duplicata)."""
    ext = extract_all(item, default_remote=src.get("default_remote", True), base_legit=src.get("base_legit", 0.8))
    score, reasons = score_job(item, ext, profile)
    comp = ext["comp"] or {}
    who = item.get("author") or item.get("company")
    return {
        "source_id": src["id"],
        "source_item_id": item["source_item_id"],
        "source_name": src["name"],
        "community": item.get("community"),
        "source_url": item.get("source_url"),
        "canonical_url": dedupe.canonical_url(item.get("source_url")),
        "apply_url": item.get("apply_url") or item.get("source_url"),
        "title": item.get("title"),
        "company": item.get("company"),
        "author": item.get("author"),
        "description": item.get("description"),
        "published_at": item.get("published_at"),
        "post_kind": ext["post_kind"],
        "comp_min": comp.get("min"),
        "comp_max": comp.get("max"),
        "comp_currency": comp.get("currency"),
        "comp_unit": comp.get("unit"),
        "comp_text": comp.get("text"),
        "payment_model": ext["payment_model"],
        "employment_type": ext["employment_type"],
        "remote_scope": ext["remote_scope"],
        "location_text": item.get("location_text") or ext.get("remote_reason"),
        "software": db.dumps(ext["software"]),
        "specializations": db.dumps(ext["specializations"]),
        "experience": ext["experience"],
        "languages": db.dumps(ext["languages"]),
        "legit_confidence": ext["legit_confidence"],
        "legit_flags": db.dumps(ext["legit_flags"]),
        "score": score,
        "score_reasons": db.dumps(reasons),
        "title_norm": normalize_title(item.get("title")),
        "fingerprint": dedupe.fingerprint(item.get("title"), who),
        "content_hash": content_hash(item),
        "raw": db.dumps(
            {
                "item": item.get("raw"),
                "tags": item.get("tags"),
                "structured_comp": item.get("comp"),
                "location_text": item.get("location_text"),
                "employment_type": item.get("employment_type"),
                "post_kind_hint": item.get("post_kind_hint"),
                "remote_reason": ext.get("remote_reason"),
            }
        ),
    }


UPDATABLE = (
    "title", "description", "community", "apply_url", "company", "post_kind", "comp_min", "comp_max", "comp_currency",
    "comp_unit", "comp_text", "payment_model", "employment_type", "remote_scope", "location_text", "software",
    "specializations", "experience", "languages", "legit_confidence", "legit_flags", "score", "score_reasons",
    "title_norm", "fingerprint", "content_hash", "raw",
)


def ingest_items(conn, src: dict, items: list[dict], profile: dict, *, first_sync: bool) -> dict:
    stats = {"seen": len(items), "new": 0, "updated": 0, "skipped": 0, "new_ids": []}
    now = now_iso()
    for item in items:
        if not item.get("source_item_id"):
            stats["skipped"] += 1
            continue
        item["source_item_id"] = str(item["source_item_id"])
        with db._lock:
            existing = conn.execute(
                "SELECT id, content_hash FROM jobs WHERE source_id=? AND source_item_id=?", (src["id"], item["source_item_id"])
            ).fetchone()
            if existing:
                if existing["content_hash"] != content_hash(item):
                    rec = build_record(item, src, profile)
                    sets = ", ".join(f"{k}=:{k}" for k in UPDATABLE)
                    conn.execute(f"UPDATE jobs SET {sets}, last_seen_at=:now, updated_at=:now WHERE id=:id", {**rec, "now": now, "id": existing["id"]})
                    stats["updated"] += 1
                else:
                    conn.execute("UPDATE jobs SET last_seen_at=? WHERE id=?", (now, existing["id"]))
                continue

        if not passes_gate(item, src, profile):
            stats["skipped"] += 1
            continue
        rec = build_record(item, src, profile)
        if rec["post_kind"] == "for_hire":
            stats["skipped"] += 1
            continue

        if src.get("method") == "manual":
            kind = "manual"
        elif first_sync:
            kind = "backfill"
        else:
            age = hours_since(rec["published_at"])
            kind = "late" if age is not None and age > LIVE_MAX_AGE_H else "live"
        with db._lock:
            dup_of, dup_reason = dedupe.find_duplicate(conn, rec)
            rec.update(discovered_at=now, last_seen_at=now, discovery_kind=kind, duplicate_of=dup_of, dup_reason=dup_reason)
            cols = ", ".join(rec)
            vals = ", ".join(f":{k}" for k in rec)
            cur = conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({vals})", rec)
            stats["new"] += 1
            stats["new_ids"].append(cur.lastrowid)
    with db._lock:
        conn.commit()
    return stats


def run_source(conn, src: dict, profile: dict, ingest=None) -> dict:
    """Coleta uma fonte. Nunca levanta exceção: registra o erro e devolve o resumo.

    `ingest` permite outro jeito de gravar os itens (o bot usa o seu, só remoto).
    """
    ingest = ingest or ingest_items
    started = now_iso()
    with db._lock:
        run_id = conn.execute("INSERT INTO runs(source_id, started_at, status) VALUES (?, ?, 'running')", (src["id"], started)).lastrowid
        conn.commit()
    row = conn.execute("SELECT * FROM sources WHERE id=?", (src["id"],)).fetchone()
    failures = row["consecutive_failures"] if row else 0
    first_sync = not (row and row["first_sync_done"])
    collector = get_collector(src["method"])
    result = {"source": src["id"], "status": "ok", "new": 0, "updated": 0, "seen": 0, "skipped": 0, "new_ids": [], "error": None}
    http_status = None
    try:
        if collector is None:
            raise CollectError(f"método de coleta desconhecido: {src['method']}", transient=False)
        items = None
        for attempt in (1, 2):
            _wait_gap(src["method"])
            try:
                items = collector(src)
                break
            except RateLimited:
                raise
            except CollectError as e:
                if not e.transient or attempt == 2:
                    raise
                log.warning("%s: erro temporário (%s), nova tentativa em 5 s", src["id"], e)
                time.sleep(5)
            finally:
                _last_call[src["method"]] = time.monotonic()
        stats = ingest(conn, src, items or [], profile, first_sync=first_sync)
        result.update({k: stats[k] for k in ("seen", "new", "updated", "skipped", "new_ids")})
        nxt = utcnow() + timedelta(minutes=src.get("interval_min", 60))
        with db._lock:
            conn.execute(
                """UPDATE sources SET last_check_at=?, last_success_at=?, next_check_at=?, consecutive_failures=0,
                   last_error=NULL, first_sync_done=1 WHERE id=?""",
                (now_iso(), now_iso(), iso(nxt), src["id"]),
            )
    except Exception as e:  # noqa: BLE001 — isolamento: nenhuma fonte derruba o pipeline
        retry_after = getattr(e, "retry_after", None)
        http_status = getattr(e, "http_status", None)
        failures += 1
        wait = _backoff_minutes(failures, src.get("interval_min", 60), retry_after)
        nxt = utcnow() + timedelta(minutes=wait)
        status = "rate_limited" if isinstance(e, RateLimited) else "error"
        msg = f"{type(e).__name__}: {e}"
        result.update(status=status, error=msg)
        log.warning("%s: %s — próxima tentativa em %.0f min", src["id"], msg, wait)
        with db._lock:
            conn.execute(
                "UPDATE sources SET last_check_at=?, next_check_at=?, consecutive_failures=?, last_error=? WHERE id=?",
                (now_iso(), iso(nxt), failures, msg, src["id"]),
            )
    with db._lock:
        conn.execute(
            """UPDATE runs SET finished_at=?, status=?, http_status=?, items_seen=?, items_new=?, items_updated=?,
               items_skipped=?, error=? WHERE id=?""",
            (now_iso(), result["status"], http_status, result["seen"], result["new"], result["updated"], result["skipped"], result["error"], run_id),
        )
        conn.commit()
    return result


def due_sources(conn, sources: list[dict], *, force: bool = False, only: list[str] | None = None) -> list[dict]:
    out = []
    now = utcnow()
    for s in sources:
        if only and s["id"] not in only:
            continue
        row = conn.execute("SELECT enabled, next_check_at FROM sources WHERE id=?", (s["id"],)).fetchone()
        if row is None or not row["enabled"]:
            continue
        if force or not row["next_check_at"] or parse_date(row["next_check_at"]) <= now:
            out.append(s)
    return out


_run_lock = threading.Lock()  # agendador e botão "Coletar agora" nunca coletam ao mesmo tempo


def collect(conn, sources: list[dict], profile: dict, *, force: bool = False, only: list[str] | None = None, notify_enabled: bool = True) -> list[dict]:
    with _run_lock:
        return _collect(conn, sources, profile, force=force, only=only, notify_enabled=notify_enabled)


def _collect(conn, sources, profile, *, force, only, notify_enabled) -> list[dict]:
    db.sync_sources(conn, sources)
    results = []
    for src in due_sources(conn, sources, force=force, only=only):
        res = run_source(conn, src, profile)
        results.append(res)
        log.info("%-16s %-12s vistos=%-4s novos=%-4s atualizados=%-3s ignorados=%-4s %s",
                 src["id"], res["status"], res["seen"], res["new"], res["updated"], res["skipped"], res["error"] or "")
    if notify_enabled:
        new_ids = [i for r in results for i in r["new_ids"]]
        try:
            notify.notify_new(conn, profile, new_ids)
            notify.maybe_digest(conn, profile)
        except Exception as e:  # noqa: BLE001 — notificação falhar não pode perder a coleta
            log.warning("falha ao notificar: %s", e)
    return results


def rescore(conn, sources: list[dict], profile: dict) -> int:
    """Recalcula extração e nota de todas as vagas com o perfil atual."""
    by_id = {s["id"]: s for s in sources}
    n = 0
    rows = conn.execute("SELECT * FROM jobs").fetchall()
    for r in rows:
        d = db.row_to_dict(r)
        raw = d.get("raw") or {}
        src = by_id.get(d["source_id"], {"id": d["source_id"], "name": d["source_name"]})
        item = {
            "source_item_id": d["source_item_id"], "title": d["title"], "description": d["description"],
            "source_url": d["source_url"], "apply_url": d["apply_url"], "published_at": d["published_at"],
            "author": d["author"], "company": d["company"], "community": d["community"],
            "location_text": raw.get("location_text"), "tags": raw.get("tags") or [], "comp": raw.get("structured_comp"),
            "employment_type": raw.get("employment_type"), "post_kind_hint": raw.get("post_kind_hint"), "raw": raw.get("item"),
        }
        rec = build_record(item, src, profile)
        sets = ", ".join(f"{k}=:{k}" for k in UPDATABLE)
        with db._lock:
            conn.execute(f"UPDATE jobs SET {sets} WHERE id=:id", {**rec, "id": d["id"]})
        n += 1
    with db._lock:
        conn.commit()
    return n
