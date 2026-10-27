"""Painel web local (só em 127.0.0.1). Biblioteca padrão do Python, sem dependências.

API JSON:
    GET  /api/jobs?view=...&filtros    lista
    GET  /api/jobs/<id>                detalhe + repostagens
    POST /api/jobs/<id>                {user_status, saved, hidden, notes, follow_up_at}
    POST /api/import                   {url, title, text}
    GET  /api/sources                  saúde das fontes
    POST /api/sources/<id>             {enabled}
    POST /api/collect                  {source?} coleta agora (em segundo plano)
    GET  /api/runs?errors=1            histórico de coletas
    GET  /api/notifications            alertas registrados
    GET  /api/stats                    números gerais
"""
from __future__ import annotations

import json
import logging
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import config, db, notify, pipeline, queries

log = logging.getLogger("vagas")
STATIC = Path(__file__).parent / "static"

_conn = None
_collect_lock = threading.Lock()


def conn():
    global _conn
    if _conn is None:
        _conn = db.connect(config.DB_PATH)
    return _conn


def _collect_bg(only=None):
    if not _collect_lock.acquire(blocking=False):
        return False

    def run():
        try:
            pipeline.collect(db.connect(config.DB_PATH), config.load_sources(), config.load_profile(), force=True, only=only)
        finally:
            _collect_lock.release()

    threading.Thread(target=run, daemon=True).start()
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "vagas"

    def log_message(self, fmt, *args):
        log.debug("web: " + fmt, *args)

    # ------------------------------------------------------------ util
    def _send(self, status: int, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    # ------------------------------------------------------------ GET
    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "host não permitido"})
        url = urlsplit(self.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        parts = [p for p in url.path.split("/") if p]
        try:
            if not parts or parts == ["index.html"]:
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if parts[0] != "api":
                return self._send(404, {"error": "não encontrado"})
            c = conn()
            if parts[1:] == ["jobs"]:
                return self._send(200, queries.list_jobs(c, q))
            if parts[1] == "jobs" and len(parts) == 3:
                job = queries.job_detail(c, int(parts[2]))
                return self._send(200 if job else 404, job or {"error": "vaga não encontrada"})
            if parts[1:] == ["sources"]:
                return self._send(200, queries.sources_health(c))
            if parts[1:] == ["runs"]:
                return self._send(200, queries.runs(c, errors_only=q.get("errors") == "1"))
            if parts[1:] == ["notifications"]:
                return self._send(200, notify.recent(c))
            if parts[1:] == ["stats"]:
                s = queries.stats(c)
                s["collecting"] = _collect_lock.locked()
                return self._send(200, s)
            return self._send(404, {"error": "não encontrado"})
        except Exception as e:  # noqa: BLE001
            log.exception("erro no painel")
            return self._send(500, {"error": str(e)})

    # ------------------------------------------------------------ POST
    def do_POST(self):
        # Cabeçalho próprio + Host local: impede que outro site no navegador mande comandos (CSRF)
        if not self._host_ok() or self.headers.get("X-Vagas") != "1":
            return self._send(403, {"error": "requisição recusada"})
        parts = [p for p in urlsplit(self.path).path.split("/") if p]
        try:
            body = self._json_body()
            c = conn()
            if parts[:2] == ["api", "jobs"] and len(parts) == 3:
                job = queries.update_job(c, int(parts[2]), body)
                return self._send(200 if job else 404, job or {"error": "vaga não encontrada"})
            if parts == ["api", "import"]:
                from . import manual

                res = manual.import_job(c, config.load_profile(), url=body.get("url") or None,
                                        text=body.get("text") or "", title=body.get("title") or None)
                return self._send(200, res)
            if parts[:2] == ["api", "sources"] and len(parts) == 3:
                queries.set_source_enabled(c, parts[2], bool(body.get("enabled")))
                return self._send(200, {"ok": True})
            if parts == ["api", "collect"]:
                only = [body["source"]] if body.get("source") else None
                started = _collect_bg(only)
                return self._send(202 if started else 409, {"started": started})
            return self._send(404, {"error": "não encontrado"})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            log.exception("erro no painel")
            return self._send(500, {"error": str(e)})


def serve(port: int = 8765, scheduler: bool = True, open_browser: bool = True) -> None:
    from .__main__ import scheduler_loop

    stop = threading.Event()
    if scheduler:
        threading.Thread(target=scheduler_loop, kwargs={"stop_event": stop}, daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    log.info("painel em %s (Ctrl+C para sair)", url)
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
