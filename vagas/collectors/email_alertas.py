"""Vagas do LinkedIn e do Upwork pelos e-mails de alerta que eles mandam para você.

Os termos dos dois proíbem robôs nos sites, então o sistema não acessa nenhum
dos dois: ele só LÊ a sua caixa de entrada (IMAP, somente leitura — nada é
marcado como lido nem apagado) e tira os links das vagas dos e-mails.

Configure no .env: IMAP_USER, IMAP_PASSWORD (no Gmail: "senha de app") e,
se não for Gmail, IMAP_HOST. Sem isso a fonte fica parada.

Atenção: o formato dos e-mails foi deduzido, não testado com e-mails reais.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from datetime import timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from .. import http
from ..util import iso, utcnow

LINKEDIN_JOB = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", re.I)
UPWORK_JOB = re.compile(r"upwork\.com/(?:jobs|freelance-jobs/apply)/[^\s\"'?]*?~(\w{10,})", re.I)
GENERICO = re.compile(r"^(view|see|ver|apply|candidat|veja|mais|more|unsubscribe|descadastr)", re.I)
WORKPLACE = [("hybrid", r"\bhybrid\b|h[ií]brid"), ("onsite", r"\bon[- ]?site\b|presencial"), ("remote", r"\bremot[eao]\b")]


class _Eventos(HTMLParser):
    """Sequência de ('link', href, texto) e ('texto', texto) na ordem do e-mail."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ev, self._href, self._buf = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._buf = dict(attrs).get("href") or "", []

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.ev.append(("link", self._href, " ".join("".join(self._buf).split())))
            self._href = None

    def handle_data(self, data):
        if self._href is not None:
            self._buf.append(data)
        elif data.strip():
            self.ev.append(("texto", " ".join(data.split())))


def parse_alerta(html: str, recebido_em=None) -> list[dict]:
    p = _Eventos()
    p.feed(html or "")
    vagas: dict[str, dict] = {}
    atual = None
    for ev in p.ev:
        if ev[0] == "link":
            m = LINKEDIN_JOB.search(ev[1]) or UPWORK_JOB.search(ev[1])
            if m:
                site = "linkedin" if "linkedin" in m.re.pattern else "upwork"
                key = f"{site}:{m.group(1)}"
                atual = vagas.setdefault(key, {"site": site, "id": m.group(1), "titulo": "", "contexto": []})
                if ev[2] and not GENERICO.match(ev[2]) and len(ev[2]) > len(atual["titulo"]):
                    atual["titulo"] = ev[2]
                continue
            if atual and ev[2]:
                atual["contexto"].append(ev[2])
        elif atual is not None and len(" ".join(atual["contexto"])) < 400:
            atual["contexto"].append(ev[1])
    out = []
    for key, v in vagas.items():
        if not v["titulo"]:
            continue
        contexto = " · ".join(v["contexto"])[:400]
        if v["site"] == "linkedin":
            url = f"https://www.linkedin.com/jobs/view/{v['id']}/"
            modo = next((m for m, rx in WORKPLACE if re.search(rx, contexto, re.I)), "unknown")
        else:
            url = f"https://www.upwork.com/jobs/~{v['id']}"
            modo = "remote"  # trabalho no Upwork é remoto por natureza
        out.append({
            "source_item_id": key, "title": v["titulo"], "description": contexto, "source_url": url, "apply_url": url,
            "published_at": iso(recebido_em) if recebido_em else None, "author": None, "company": None,
            "community": "LinkedIn (alerta por e-mail)" if v["site"] == "linkedin" else "Upwork (alerta por e-mail)",
            "location_text": None, "tags": [], "comp": None, "employment_type": None, "post_kind_hint": "hiring",
            "raw": {"workplace_type": modo},
        })
    return out


def _html_do_email(msg) -> str:
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def email_alertas(source: dict) -> list[dict]:
    user, pwd = os.environ.get("IMAP_USER"), os.environ.get("IMAP_PASSWORD")
    if not user or not pwd:
        raise http.CollectError("configure IMAP_USER e IMAP_PASSWORD no .env", transient=False)
    p = source["params"]
    desde = (utcnow() - timedelta(days=int(p.get("dias", 3)))).strftime("%d-%b-%Y")
    out = []
    try:
        box = imaplib.IMAP4_SSL(os.environ.get("IMAP_HOST", "imap.gmail.com"))
        box.login(user, pwd)
        box.select("INBOX", readonly=True)
        for remetente in p.get("remetentes", ["linkedin.com", "upwork.com"]):
            _, data = box.search(None, f'(FROM "{remetente}" SINCE {desde})')
            for num in (data[0] or b"").split()[-40:]:
                _, partes = box.fetch(num, "(BODY.PEEK[])")
                msg = email.message_from_bytes(partes[0][1])
                try:
                    recebido = parsedate_to_datetime(msg.get("Date"))
                except (TypeError, ValueError):
                    recebido = None
                out += parse_alerta(_html_do_email(msg), recebido)
        box.logout()
    except imaplib.IMAP4.error as e:
        raise http.CollectError(f"erro no e-mail (IMAP): {e}", transient=False) from e
    except OSError as e:
        raise http.CollectError(f"sem conexão com o servidor de e-mail: {e}") from e
    return out
