"""Gera o arquivo de vagas do site da Mica (site/vagas.json).

Roda sem banco de dados: o próprio vagas.json anterior é o histórico. A cada
execução o gerador consulta as fontes que estão no horário, aplica as regras
(só remota, aceita quem está no Brasil), junta com as vagas que já existiam e
descarta as antigas. Feito para rodar no GitHub Actions, com o PC desligado.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from . import config, dedupe, http
from .busca import aceita_brasil, modo_trabalho, separar
from .collectors import get_collector
from .extract import extract_compensation
from .util import hours_since, now_iso, sha1, truncate

log = logging.getLogger("vagas")

SITE_DIR = config.ROOT / "site"
UNIDADE = {"video": "vídeo", "short": "short", "minute": "min", "hour": "hora", "month": "mês", "week": "semana",
           "year": "ano", "project": "projeto"}


def formatar_valor(comp: dict | None) -> str | None:
    if not comp or not comp.get("min"):
        return None
    n = lambda x: f"{x:,.0f}".replace(",", ".")  # noqa: E731
    lo, hi = comp["min"], comp.get("max") or comp["min"]
    if comp.get("unit") in (None, "unknown") and hi < 100:
        return None  # "BRL 45" sem unidade mais confunde do que ajuda
    v = n(lo) if lo == hi else f"{n(lo)}–{n(hi)}"
    un = UNIDADE.get(comp.get("unit") or "")
    return f"{comp.get('currency') or ''} {v}{'/' + un if un else ''}".strip()


def carregar_anterior(origem: str | None) -> dict:
    """Lê o vagas.json anterior de um arquivo ou de uma URL (o site publicado)."""
    if not origem:
        return {}
    try:
        if origem.startswith("http"):
            return http.get_json(origem.rstrip("/") + ("" if origem.endswith(".json") else "/vagas.json"))
        p = Path(origem)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception as e:  # noqa: BLE001 — sem histórico, começa do zero
        log.warning("não consegui ler as vagas anteriores (%s): %s", origem, e)
        return {}


def local_amigavel(loc: str | None, src: dict) -> str:
    if not loc:
        return "Brasil" if src.get("pais") == "BR" else "Remoto"
    if loc.startswith("Sem restrição") or re.search(r"(?i)world ?wide|anywhere|^global$", loc):
        return "Qualquer país"
    if re.fullmatch(r"(?i)\s*(remote|remoto)\s*", loc):
        return "Remoto"
    if re.fullmatch(r"(?i)\s*bra[sz]il\s*", loc):
        return "Brasil"
    partes = [p.strip() for p in loc.split(",") if p.strip()]
    if len(partes) > 4 and any(re.match(r"(?i)bra[sz]il", p) for p in partes):
        return "Vários países (inclui o Brasil)"
    return loc


def normalizar(item: dict, src: dict, agora: str) -> dict:
    comp = item.get("comp") or extract_compensation(item.get("title"), item.get("description"))
    return {
        "id": sha1(src["id"], item["source_item_id"])[:12],
        "titulo": (item.get("title") or "").strip(),
        "empresa": item.get("company"),
        "local": local_amigavel(item.get("location_text"), src),
        "valor": formatar_valor(comp),
        "fonte": src["name"],
        "url": item.get("source_url"),
        "publicada": item.get("published_at"),
        "descoberta": agora,
        "texto": truncate(item.get("description") or "", 1500),
        "url_canonica": dedupe.canonical_url(item.get("source_url")),
        "fp": dedupe.fingerprint(item.get("title"), item.get("company") or item.get("author")),
    }


def gerar(anterior: dict | None = None, *, force: bool = False, saida: Path | None = None, so: list[str] | None = None) -> dict:
    """`so`: consultar agora só estas fontes (ignorando o intervalo delas)."""
    cfg = config.load_json("site.json")
    mica = json.loads((SITE_DIR / "mica.json").read_text(encoding="utf-8"))
    termos = list(dict.fromkeys(separar(mica.get("palavras")) + list(cfg.get("termos_extras") or [])))
    anterior = anterior or {}
    agora = now_iso()
    estado = dict(anterior.get("fontes") or {})
    vagas = {v["id"]: v for v in anterior.get("vagas") or []}
    por_url = {v["url_canonica"]: v["id"] for v in vagas.values() if v.get("url_canonica")}
    por_fp = {v["fp"]: v["id"] for v in vagas.values() if v.get("fp")}

    for src in cfg["fontes"]:
        if not src.get("enabled", True) or (so and src["id"] not in so):
            continue
        ultima = (estado.get(src["id"]) or {}).get("ultima")
        if not (force or so) and ultima and hours_since(ultima) * 60 < src.get("interval_min", 60):
            continue
        s = json.loads(json.dumps(src))
        s.setdefault("params", {})
        if src.get("modo") == "termos":
            s["params"]["queries"] = termos
        try:
            items = get_collector(src["method"])(s)
        except Exception as e:  # noqa: BLE001 — uma fonte fora do ar não para as outras
            log.warning("%s: %s", src["id"], e)
            estado[src["id"]] = {**(estado.get(src["id"]) or {}), "ok": False, "erro": str(e)[:200], "tentativa": agora}
            continue
        novas = descartadas = 0
        for item in items:
            if not item.get("source_item_id"):
                continue
            item["source_item_id"] = str(item["source_item_id"])
            if modo_trabalho(item, src) != "remote" or not aceita_brasil(item, src)[0]:
                descartadas += 1
                continue
            v = normalizar(item, src, agora)
            if v["id"] in vagas or (v["url_canonica"] and v["url_canonica"] in por_url) or v["fp"] in por_fp:
                continue  # já existe (ou é a mesma vaga em outra fonte)
            vagas[v["id"]] = v
            if v["url_canonica"]:
                por_url[v["url_canonica"]] = v["id"]
            por_fp[v["fp"]] = v["id"]
            novas += 1
        estado[src["id"]] = {"ultima": agora, "ok": True, "vistas": len(items), "novas": novas, "descartadas": descartadas}
        log.info("%-14s vistas=%-4s novas=%-4s descartadas=%s", src["id"], len(items), novas, descartadas)
        time.sleep(1)

    dias = cfg.get("dias_guardar", 30)
    lista = [v for v in vagas.values() if (hours_since(v.get("publicada") or v["descoberta"]) or 0) <= dias * 24]
    lista.sort(key=lambda v: v.get("publicada") or v["descoberta"], reverse=True)
    out = {"atualizado_em": agora, "fontes": estado, "vagas": lista}
    if saida is not None:
        saida.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return out
