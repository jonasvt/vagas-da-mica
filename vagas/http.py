"""Cliente HTTP educado: User-Agent honesto, timeout, e erros tipados.

Nenhum disfarce de navegador, nenhuma tentativa de contornar bloqueios: se a
fonte responde 429/403, o coletor para e espera (backoff).
"""
from __future__ import annotations

import requests

from . import __version__

USER_AGENT = f"windows:localizador-de-vagas:v{__version__} (uso pessoal; leitor de feeds de baixo volume)"


class CollectError(Exception):
    """Erro de coleta. `transient` indica se vale tentar de novo depois."""

    def __init__(self, msg: str, *, transient: bool = True, http_status: int | None = None, retry_after: float | None = None):
        super().__init__(msg)
        self.transient = transient
        self.http_status = http_status
        self.retry_after = retry_after


class RateLimited(CollectError):
    pass


_session: requests.Session | None = None


def session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers["User-Agent"] = USER_AGENT
        _session = s
    return _session


def _retry_after(resp: requests.Response) -> float | None:
    for h in ("Retry-After", "x-ratelimit-reset"):
        v = resp.headers.get(h)
        if v:
            try:
                return max(float(v), 1.0)
            except ValueError:
                pass
    return None


def get(url: str, *, params=None, timeout: float = 30, accept: str | None = None) -> requests.Response:
    headers = {"Accept": accept} if accept else None
    try:
        resp = session().get(url, params=params, timeout=timeout, headers=headers)
    except requests.Timeout as e:
        raise CollectError(f"timeout: {e}", transient=True) from e
    except requests.ConnectionError as e:
        raise CollectError(f"falha de conexão: {e}", transient=True) from e
    except requests.RequestException as e:
        raise CollectError(f"erro HTTP: {e}", transient=True) from e
    if resp.status_code == 429:
        raise RateLimited("limite de requisições (429)", http_status=429, retry_after=_retry_after(resp))
    if resp.status_code in (401, 403):
        raise CollectError(f"acesso negado ({resp.status_code})", transient=False, http_status=resp.status_code)
    if resp.status_code == 404:
        raise CollectError("não encontrado (404)", transient=False, http_status=404)
    if resp.status_code >= 500:
        raise CollectError(f"erro no servidor ({resp.status_code})", transient=True, http_status=resp.status_code)
    if resp.status_code >= 400:
        raise CollectError(f"HTTP {resp.status_code}", transient=False, http_status=resp.status_code)
    return resp


def get_json(url: str, **kw):
    resp = get(url, accept="application/json", **kw)
    try:
        return resp.json()
    except ValueError as e:
        raise CollectError(f"resposta não é JSON válido: {e}", transient=True, http_status=resp.status_code) from e


def get_text(url: str, **kw) -> str:
    resp = get(url, **kw)
    resp.encoding = resp.encoding or "utf-8"
    if resp.encoding.lower() in ("iso-8859-1", "latin-1"):
        resp.encoding = "utf-8"
    return resp.text
