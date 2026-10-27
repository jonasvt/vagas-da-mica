"""Linha de comando.

    python -m vagas collect [--force] [--source ID ...]   coleta as fontes no horário
    python -m vagas loop                                  coleta continuamente (agendador)
    python -m vagas serve [--port 8765] [--no-scheduler]  painel + agendador
    python -m vagas import --url URL --text-file F        importa uma vaga manualmente
    python -m vagas rescore                               recalcula notas com o perfil atual
    python -m vagas stats                                 resumo no terminal
    python -m vagas backup | restore ARQUIVO              backup e restauração do banco
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from . import config, db, pipeline


def _setup_logging(verbose: bool) -> None:
    from logging.handlers import RotatingFileHandler

    # Console do Windows pode não ser UTF-8; com pythonw nem existe console.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(config.DATA_DIR / "vagas.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _conn():
    return db.connect(config.DB_PATH)


def cmd_collect(args) -> int:
    conn = _conn()
    res = pipeline.collect(conn, config.load_sources(), config.load_profile(), force=args.force, only=args.source or None)
    if not res:
        print("Nenhuma fonte no horário (use --force para coletar agora).")
    return 0 if all(r["status"] == "ok" for r in res) else 1


def scheduler_loop(stop_event=None, tick_s: int = 60) -> None:
    conn = _conn()
    log = logging.getLogger("vagas")
    log.info("agendador iniciado (verifica fontes a cada %ss)", tick_s)
    while not (stop_event and stop_event.is_set()):
        try:
            pipeline.collect(conn, config.load_sources(), config.load_profile())
        except Exception as e:  # noqa: BLE001 — o agendador nunca deve morrer
            log.exception("erro inesperado no ciclo: %s", e)
        for _ in range(tick_s):
            if stop_event and stop_event.is_set():
                break
            time.sleep(1)


def cmd_loop(args) -> int:
    try:
        scheduler_loop(tick_s=args.tick)
    except KeyboardInterrupt:
        print("encerrado.")
    return 0


def cmd_serve(args) -> int:
    from . import web

    web.serve(port=args.port, scheduler=not args.no_scheduler, open_browser=not args.no_browser)
    return 0


def cmd_import(args) -> int:
    from . import manual

    text = open(args.text_file, encoding="utf-8").read() if args.text_file else (args.text or sys.stdin.read())
    res = manual.import_job(_conn(), config.load_profile(), url=args.url, text=text, title=args.title)
    job = res["job"]
    print(("importada" if res["created"] else "já existia"), f"#{job['id']} nota {job['score']}: {job['title']}")
    for r in job["score_reasons"]:
        print("   ", r)
    return 0


def cmd_rescore(args) -> int:
    n = pipeline.rescore(_conn(), config.load_sources(), config.load_profile())
    print(f"{n} vagas reavaliadas.")
    return 0


def cmd_stats(args) -> int:
    from . import queries

    s = queries.stats(_conn())
    print(f"Vagas: {s['total']} (únicas {s['unique']}, duplicadas {s['duplicates']}) · novas 24h: {s['new_24h']} · candidaturas: {s['applied']}")
    for src in s["sources"]:
        print(f"  {src['id']:16} {'on ' if src['enabled'] else 'off'} vagas={src['jobs']:<5} último sucesso={src['last_success_at'] or '-'}"
              f"  falhas seguidas={src['consecutive_failures']} {src['last_error'] or ''}")
    return 0


def cmd_backup(args) -> int:
    from . import backup

    dest = backup.backup(config.DB_PATH, config.DATA_DIR / "backups")
    ok, info = backup.verify(dest)
    print(f"backup salvo em {dest} ({info})")
    return 0 if ok else 1


def cmd_restore(args) -> int:
    from pathlib import Path

    from . import backup

    safety = backup.restore(Path(args.file), config.DB_PATH, config.DATA_DIR / "backups")
    print(f"restaurado de {args.file}." + (f" Banco anterior guardado em {safety}" if safety else ""))
    return 0


def cmd_bot(args) -> int:
    from . import bot

    return bot.rodar()


def cmd_procurar(args) -> int:
    """Mostra no terminal o que o /vagas do bot responderia."""
    from . import busca

    conn, cfg = _conn(), busca.load_config()
    if args.coletar:
        busca.coletar(conn, cfg, force=True)
    busca.buscar_online(conn, cfg, busca.separar(args.palavras))
    jobs, total = busca.buscar(conn, args.palavras, args.sem, dias=args.dias, limite=args.limite)
    print(f"{total} vaga(s) remota(s) para '{args.palavras}' (mostrando {len(jobs)}):")
    for j in jobs:
        print(f"- {j['title']}\n    {j.get('company') or ''} · {j.get('location_text') or 'Remoto'} · {j.get('source_name')}"
              f" · publicada {j.get('published_at') or '?'} · achei no {j['_onde']}\n    {j.get('source_url')}")
    return 0


def cmd_coletar_bot(args) -> int:
    from . import busca

    res = busca.coletar(_conn(), busca.load_config(), force=args.force)
    return 0 if all(r["status"] == "ok" for r in res) else 1


def cmd_limpar(args) -> int:
    from . import backup, busca

    dest = backup.backup(config.DB_PATH, config.DATA_DIR / "backups")
    res = busca.remover_nao_aceitas(_conn(), busca.load_config())
    print(f"Backup antes de limpar: {dest}")
    print(f"Apagadas {res['apagadas']} vagas (presencial/híbrida: {res['presencial/híbrida']}, só outro país: {res['outro país']}).")
    return 0


def cmd_site(args) -> int:
    """Atualiza site/vagas.json (o GitHub Actions roda isto a cada 2 horas)."""
    from . import site

    saida = site.SITE_DIR / "vagas.json"
    # site publicado primeiro; se ainda não existir (primeira vez), o arquivo da pasta
    anterior = site.carregar_anterior(args.anterior) or site.carregar_anterior(str(saida))
    out = site.gerar(anterior, force=args.force, saida=saida, so=args.fonte)
    print(f"{len(out['vagas'])} vagas em {saida}")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # console do Windows pode não ser UTF-8
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    config.load_env()
    p = argparse.ArgumentParser(prog="vagas", description="Bot e localizador pessoal de vagas remotas")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("site", help="atualiza as vagas do site da Mica (site/vagas.json)")
    c.add_argument("--anterior", help="vagas.json anterior: arquivo ou URL do site publicado")
    c.add_argument("--force", action="store_true", help="consulta todas as fontes agora")
    c.add_argument("--fonte", action="append", help="consulta agora só esta fonte (pode repetir)")
    c.set_defaults(fn=cmd_site)

    sub.add_parser("bot", help="liga o bot do Discord").set_defaults(fn=cmd_bot)

    c = sub.add_parser("procurar", help="testa no terminal o que o /vagas responderia")
    c.add_argument("palavras")
    c.add_argument("--sem")
    c.add_argument("--dias", type=int, default=15)
    c.add_argument("--limite", type=int, default=10)
    c.add_argument("--coletar", action="store_true", help="coleta todas as fontes antes")
    c.set_defaults(fn=cmd_procurar)

    c = sub.add_parser("coletar-bot", help="coleta as fontes do bot (só remotas)")
    c.add_argument("--force", action="store_true")
    c.set_defaults(fn=cmd_coletar_bot)

    sub.add_parser("limpar", help="apaga do banco vagas presenciais, híbridas e só de outros países").set_defaults(fn=cmd_limpar)

    c = sub.add_parser("collect", help="coleta as fontes que estão no horário")
    c.add_argument("--force", action="store_true", help="ignora o intervalo e coleta agora")
    c.add_argument("--source", action="append", help="só esta fonte (pode repetir)")
    c.set_defaults(fn=cmd_collect)

    c = sub.add_parser("loop", help="coleta continuamente")
    c.add_argument("--tick", type=int, default=60)
    c.set_defaults(fn=cmd_loop)

    c = sub.add_parser("serve", help="painel web local + agendador")
    c.add_argument("--port", type=int, default=8765)
    c.add_argument("--no-scheduler", action="store_true")
    c.add_argument("--no-browser", action="store_true", help="não abre o navegador automaticamente")
    c.set_defaults(fn=cmd_serve)

    c = sub.add_parser("import", help="importa uma vaga manualmente")
    c.add_argument("--url")
    c.add_argument("--title")
    c.add_argument("--text")
    c.add_argument("--text-file")
    c.set_defaults(fn=cmd_import)

    sub.add_parser("rescore").set_defaults(fn=cmd_rescore)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    sub.add_parser("backup").set_defaults(fn=cmd_backup)
    c = sub.add_parser("restore")
    c.add_argument("file")
    c.set_defaults(fn=cmd_restore)

    args = p.parse_args(argv)
    _setup_logging(args.verbose)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
