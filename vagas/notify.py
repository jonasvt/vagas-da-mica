"""Notificações: alerta imediato para vagas fortes e resumo periódico.

Canais suportados (configure no .env): webhook de um canal do SEU Discord e/ou
bot do Telegram. Sem canal configurado, o alerta fica registrado no banco
(aba Alertas do painel) e aparece no console.

Nunca avisa duas vezes da mesma vaga, a menos que título ou valor mudem.
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta

import requests

from . import db
from .util import iso, now_iso, parse_date, sha1, truncate, utcnow

log = logging.getLogger("vagas")


def _hash(job: dict) -> str:
    return sha1(job.get("title"), job.get("comp_text"))


def format_job(job: dict) -> str:
    comp = ""
    if job.get("comp_min"):
        cur = job.get("comp_currency") or ""
        lo, hi = job["comp_min"], job.get("comp_max") or job["comp_min"]
        val = f"{lo:,.0f}" if lo == hi else f"{lo:,.0f}–{hi:,.0f}"
        comp = f" · {cur} {val}/{job.get('comp_unit')}"
    where = job.get("community") or job.get("source_name")
    return f"[{job['score']}] {job['title']}{comp}\n{where} · {job.get('source_url')}"


def _send_discord(url: str, text: str) -> None:
    r = requests.post(url, json={"content": truncate(text, 1900), "allowed_mentions": {"parse": []}}, timeout=20)
    r.raise_for_status()


def _send_telegram(token: str, chat_id: str, text: str) -> None:
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": truncate(text, 3900), "disable_web_page_preview": True},
        timeout=20,
    )
    r.raise_for_status()


def channels(profile: dict) -> list[tuple[str, callable]]:
    cfg = profile.get("notify", {})
    out = []
    hook = os.environ.get(cfg.get("discord_webhook_url_env", "VAGAS_DISCORD_WEBHOOK"))
    if hook:
        out.append(("discord", lambda text: _send_discord(hook, text)))
    tok = os.environ.get(cfg.get("telegram_bot_token_env", "VAGAS_TELEGRAM_TOKEN"))
    chat = os.environ.get(cfg.get("telegram_chat_id_env", "VAGAS_TELEGRAM_CHAT_ID"))
    if tok and chat:
        out.append(("telegram", lambda text: _send_telegram(tok, chat, text)))
    return out


def _deliver(conn, job_id, kind: str, text: str, profile: dict, senders=None) -> bool:
    senders = channels(profile) if senders is None else senders
    ok_any = False
    if not senders:
        log.info("ALERTA %s\n%s", kind, text)
        with db._lock:
            conn.execute("INSERT INTO notifications(job_id, kind, channel, sent_at, status, payload) VALUES (?,?,?,?,?,?)",
                         (job_id, kind, "log", now_iso(), "logged", text))
        return True
    for name, send in senders:
        try:
            send(text)
            status, err, ok_any = "sent", None, True
        except Exception as e:  # noqa: BLE001
            status, err = "failed", str(e)[:500]
            log.warning("falha ao enviar alerta por %s: %s", name, e)
        with db._lock:
            conn.execute("INSERT INTO notifications(job_id, kind, channel, sent_at, status, error, payload) VALUES (?,?,?,?,?,?,?)",
                         (job_id, kind, name, now_iso(), status, err, text))
    return ok_any


def notify_new(conn, profile: dict, job_ids: list[int], senders=None) -> int:
    cfg = profile.get("notify", {})
    if not job_ids:
        return 0
    sent = 0
    q = ",".join("?" * len(job_ids))
    rows = conn.execute(
        f"""SELECT * FROM jobs WHERE id IN ({q}) AND duplicate_of IS NULL AND hidden=0 AND post_kind!='for_hire'
            AND score >= ? ORDER BY score DESC""",
        (*job_ids, cfg.get("min_score", 60)),
    ).fetchall()
    for r in rows:
        job = dict(r)
        if cfg.get("only_live", True) and job["discovery_kind"] not in ("live", "manual"):
            continue
        h = _hash(job)
        if job.get("notified_hash") == h:
            continue
        if _deliver(conn, job["id"], "immediate", "Nova vaga forte\n" + format_job(job), profile, senders):
            with db._lock:
                conn.execute("UPDATE jobs SET notified_at=?, notified_hash=? WHERE id=?", (now_iso(), h, job["id"]))
            sent += 1
    with db._lock:
        conn.commit()
    return sent


def maybe_digest(conn, profile: dict, *, force: bool = False, senders=None) -> int:
    cfg = profile.get("notify", {})
    every = cfg.get("digest_every_hours", 12)
    if not every:
        return 0
    last = parse_date(db.get_meta(conn, "last_digest_at"))
    now = utcnow()
    if last is None:
        db.set_meta(conn, "last_digest_at", iso(now))  # primeira execução só marca o início
        return 0
    if not force and now - last < timedelta(hours=every):
        return 0
    rows = conn.execute(
        """SELECT * FROM jobs WHERE discovered_at > ? AND duplicate_of IS NULL AND hidden=0 AND notified_at IS NULL
           AND post_kind!='for_hire' AND score >= ? AND discovery_kind IN ('live','late','manual')
           ORDER BY score DESC LIMIT 15""",
        (iso(last), cfg.get("digest_min_score", 40)),
    ).fetchall()
    db.set_meta(conn, "last_digest_at", iso(now))
    if not rows:
        return 0
    text = f"Resumo: {len(rows)} vagas novas desde {iso(last)}\n\n" + "\n\n".join(format_job(dict(r)) for r in rows)
    _deliver(conn, None, "digest", text, profile, senders)
    with db._lock:
        conn.commit()
    return len(rows)


def recent(conn, limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

