"""Funções auxiliares: datas, texto, HTML, hashes."""
from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return iso(utcnow())


def parse_date(value) -> datetime | None:
    """Converte datas de vários formatos para datetime UTC.

    Aceita: epoch (segundos ou ms), ISO 8601 (com ou sem fuso), RFC 822 (RSS),
    e "MM/DD/YYYY". Datas sem fuso são tratadas como UTC. Retorna None se não
    der para interpretar — nunca inventa uma data.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        n = float(value)
        if n > 1e12:  # milissegundos
            n /= 1000
        if n <= 0:
            return None
        return datetime.fromtimestamp(n, timezone.utc)
    s = str(value).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)), tzinfo=timezone.utc)
    return None


def to_iso(value) -> str | None:
    return iso(parse_date(value))


def hours_since(value) -> float | None:
    dt = parse_date(value)
    if dt is None:
        return None
    return (utcnow() - dt).total_seconds() / 3600


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "blockquote", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("• ")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str | None) -> str:
    """HTML -> texto legível, preservando quebras de parágrafo."""
    if not raw:
        return ""
    p = _TextExtractor()
    try:
        p.feed(raw)
        p.close()
        text = "".join(p.parts)
    except Exception:  # HTML quebrado: cai para remoção simples de tags
        text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_links(raw: str | None) -> list[str]:
    if not raw:
        return []
    p = _TextExtractor()
    try:
        p.feed(raw)
    except Exception:
        return []
    return p.links


def fix_mojibake(s: str | None) -> str | None:
    """Corrige textos UTF-8 que foram decodificados como Latin-1 (ex.: 'â€“')."""
    if not s or not re.search("[ÂÃâ][\u0080-¿]", s):
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def sha1(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p or "").encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def normalize_title(title: str | None) -> str:
    """Título normalizado para comparação de duplicatas."""
    t = (title or "").lower()
    t = _EMOJI.sub(" ", t)
    t = re.sub(r"\[(hiring|for hire|task|paid|remote|urgent)\]|\((hiring|paid|remote)\)", " ", t)
    t = re.sub(r"\b(hiring|looking for|we are|we're|i'm|i am|seeking|need|needed|wanted)\b", " ", t)
    t = re.sub(r"[^a-z0-9$ ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def tokens(text: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9$]{3,}", (text or "").lower())}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def truncate(s: str | None, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"
