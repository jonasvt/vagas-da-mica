"""Motor simples do bot: coleta só vagas remotas abertas a quem está no Brasil,
casa com as palavras-chave de cada pessoa e prepara os avisos.

Regras de entrada (aplicadas na coleta — o que não passa nem é guardado):
1. Precisa ser remota. Presencial e híbrida ficam de fora.
2. Remota restrita EXPLICITAMENTE a outro país ("US only", "Europe", "must be
   based in the UK") fica de fora. Worldwide, Brasil, América Latina, Américas
   ou local não informado continuam.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import unicodedata
from datetime import timedelta

from . import config, db, dedupe, pipeline
from .collectors import get_collector
from .extract import TITLE_REGION, classify_post, extract_compensation, search_affirmative
from .util import hours_since, iso, normalize_title, now_iso, sha1, to_iso, utcnow

log = logging.getLogger("vagas")


def load_config() -> dict:
    return config.load_json("bot.json")


# ------------------------------------------------------------------ remoto?
ONSITE = re.compile(r"\b(on[- ]?site|in[- ]office|in[- ]person|presencial|relocat\w*|office[- ]based)\b", re.I)
HYBRID = re.compile(
    r"\bh[ií]brido\b|\b(modelo|regime|trabalho|formato|modalidade|jornada)\s+h[ií]brid[oa]\b"
    r"|\bhybrid\s+(work|working|role|position|model|schedule|arrangement|setup|job|office|policy)\b"
    r"|[\[(]\s*hybrid\s*[\])]|[-–|•]\s*hybrid\b|\bhybrid\s*[-–(]",
    re.I,
)
REMOTE = re.compile(r"\bremot[eo]s?\b|\bremota\b|\bremotely\b|\bhome[- ]?office\b|\bteletrabalho\b|\banywhere\b|\bwork from home\b|\bwfh\b|100%\s*remot", re.I)
# No texto da vaga só contam frases explícitas sobre o modelo de trabalho
# (senão "in-person visits" ou "hybrid cloud" derrubariam vagas remotas).
ONSITE_TEXTO = re.compile(
    r"\b(on[- ]?site|in[- ]office|in[- ]person|office[- ]based)\s+(role|position|job|work|opportunity|only|requirement|presence)\b"
    r"|\b(this|the) (role|position|job) is (fully |100% )?(on[- ]?site|in[- ]office|in[- ]person|office[- ]based)\b"
    r"|\b(on[- ]?site|in[- ]office|office[- ]based)\s+(in|at)\s+(?-i:[A-Z])"
    r"|\bwork(ing)? from (our|the) office\b|\bmust relocate\b|\brelocation (is )?required\b"
    r"|\b(vaga|trabalho|regime|modelo|atua[cç][aã]o)\s+presencial\b|\b100% presencial\b",
    re.I,
)
HYBRID_TEXTO = re.compile(
    r"\b(modelo|regime|trabalho|formato|modalidade|jornada|vaga)\s+h[ií]brid[oa]\b"
    r"|\bhybrid\s+(work|working|role|position|model|schedule|arrangement|job|policy)\b"
    r"|\b(this|the) (role|position|job) is hybrid\b",
    re.I,
)


def modo_trabalho(item: dict, src: dict) -> str:
    """remote | hybrid | onsite | unknown."""
    raw = item.get("raw") or {}
    if raw.get("workplace_type"):  # Gupy informa o modelo num campo próprio
        return raw["workplace_type"]
    title = item.get("title") or ""
    head = " ".join([title, item.get("location_text") or "", " ".join(map(str, item.get("tags") or []))])
    if HYBRID.search(head):
        return "hybrid"
    if ONSITE.search(head) and not REMOTE.search(head):
        return "onsite"
    if REMOTE.search(head):
        return "remote"
    body = (item.get("description") or "")[:3000]
    if search_affirmative(HYBRID_TEXTO, body):
        return "hybrid"
    if search_affirmative(ONSITE_TEXTO, body):
        return "onsite"
    if src.get("default_remote"):
        return "remote"
    return "remote" if REMOTE.search(body) else "unknown"


# ------------------------------------------------------------------ aceita quem está no Brasil?
OK_REGION = re.compile(
    r"world ?wide|anywhere|global|international|any (country|location)|everywhere|sem restri|todo o mundo|qualquer lugar"
    r"|\bbra[sz]il|\blatam\b|latin america|am[eé]rica latina|south america|am[eé]rica do sul|\bamericas\b",
    re.I,
)
# Siglas só em MAIÚSCULAS (senão "us" de "contact us" viraria Estados Unidos)
OTHER_PLACES = (
    r"(?-i:US|USA|U\.S\.A?\.?|UK|U\.K\.|EU|E\.U\.|EMEA|APAC|DACH)|united states|america|united kingdom|england|britain|canada"
    r"|europe|european union|australia|new zealand|india|philippines|germany|france|spain|portugal|italy|netherlands"
    r"|ireland|poland|mexico|singapore|japan|israel|north america|nordics?"
)
# Lugar no título só conta quando é a regra da vaga: "US Remote", "Remote - UK", "– US/Canada –".
# "for UK-based YouTuber" descreve o cliente e não entra aqui.
_LUGARES = rf"(?:{OTHER_PLACES})(?:\s*/\s*(?:{OTHER_PLACES}))*"
TITLE_PLACE = re.compile(
    rf"\b{_LUGARES}[\s-]*(remote|only)\b|\bremot[eo][\s,:–-]+{_LUGARES}\b|[–|]\s*{_LUGARES}\s*(?:[–|]|$)",
    re.I,
)

# "a US-based company" descreve o cliente, não o candidato: só conta quando fala de quem pode se candidatar.
EXPLICIT_ELSEWHERE = re.compile(
    rf"\b({OTHER_PLACES})[- ]only\b"
    rf"|\b({OTHER_PLACES})[- ]based (candidates?|applicants?|talent|people|engineers?|developers?|editors?|professionals?|freelancers?|only|residents?)\b"
    rf"|\bmust be ({OTHER_PLACES})[- ]based\b|\b({OTHER_PLACES}) (residents?|citizens?) only\b"
    rf"|\bopen (only )?to ({OTHER_PLACES}) (residents?|citizens?)\b"
    rf"|\bmust (be|live|reside)( currently)? (located |based |living |residing )?in (the )?({OTHER_PLACES})\b"
    rf"|\b(eligible|authori[sz]ed|legally able) to work in (the )?({OTHER_PLACES})\b"
    rf"|\bonly (accepting|hiring|considering|open to) (candidates|applicants|applications|people)? ?(from|in|based in|located in|residing in) (the )?({OTHER_PLACES})\b"
    rf"|\b(residents?|citizens?) of (the )?({OTHER_PLACES}) only\b",
    re.I,
)


def aceita_brasil(item: dict, src: dict) -> tuple[bool, str | None]:
    """(aceita?, motivo da recusa). Só recusa quando a restrição é explícita."""
    if src.get("pais") == "BR":
        return True, None
    title = item.get("title") or ""
    loc = (item.get("location_text") or "").strip()
    m = TITLE_REGION.search(title) or TITLE_PLACE.search(title)
    if m and not OK_REGION.search(m.group(0)) and not OK_REGION.search(title):
        return False, f"título restrito: {m.group(0).strip()}"
    if loc and not OK_REGION.search(loc):
        # Sobrou um lugar com nome (ex.: "Remote - US", "London", "Europe") => restrição explícita
        lugar = re.sub(r"(?i)\b(fully |100% ?)?(remote|remoto|remota|n/?a)\b|[-–,/|()\s]+", " ", loc).strip()
        if lugar:
            return False, f"local: {loc}"
    desc = (item.get("description") or "")[:4000]
    m = search_affirmative(EXPLICIT_ELSEWHERE, desc)
    if m and not re.search(r"bra[sz]il|latam|latin america|south america|worldwide|anywhere", desc[max(0, m.start() - 30) : m.end() + 30], re.I):
        return False, f"texto: “{m.group(0)}”"
    return True, None


# ------------------------------------------------------------------ palavras-chave
def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.replace("-", "").replace("/", " ")).strip()


def separar(texto: str | None) -> list[str]:
    """"python, cientista de dados" -> ["python", "cientista de dados"]."""
    return [t.strip() for t in re.split(r"[,;\n]", texto or "") if t.strip()]


def variantes(s: str | None) -> tuple[str, str]:
    """Duas versões do texto: "back-end" vira "backend" e "back end". Sem acento e sem o "(a)" de "Engenheiro(a)"."""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\((a|o|as|os|es)\)", "", s).replace("/", " ")
    return tuple(re.sub(r"\s+", " ", v).strip() for v in (s.replace("-", ""), s.replace("-", " ")))


def _limpar_termo(t: str | None) -> str:
    """"Community Manager." (o celular põe ponto sozinho) -> "Community Manager"; ".net" e "c++" ficam."""
    return re.sub(r"^[,;:!?…]+|[.,;:!?…]+$", "", (t or "").strip()).strip()


def _padrao(frase: str) -> str:
    """Cada palavra aceita singular e plural: "managers" acha "Manager" e vice-versa."""
    partes = []
    for w in frase.split():
        base = w[:-1] if len(w) > 3 and re.search(r"[^s]s$", w) else w
        partes.append(re.escape(base) + ("(?:e?s)?" if re.search(r"[a-z]$", base) else ""))
    return r"\s+".join(partes)


def _tem(termo: str, texto_v: tuple[str, str]) -> bool:
    """A palavra ou FRASE inteira precisa aparecer junta ("community manager" não casa com
    "Community Engagement Manager")."""
    tv = variantes(_limpar_termo(termo))
    return bool(tv[0]) and any(tv[i] and re.search(rf"(?<![a-z0-9]){_padrao(tv[i])}(?![a-z0-9])", texto_v[i]) for i in (0, 1))


def casa(job: dict, termos: list[str], excluir: list[str]) -> str | None:
    """Devolve 'titulo', 'texto' ou None. Palavra excluída no título descarta a vaga."""
    t = variantes(job.get("title"))
    if any(_tem(x, t) for x in excluir):
        return None
    if any(_tem(x, t) for x in termos):
        return "titulo"
    d = variantes((job.get("description") or "")[:6000])
    if any(_tem(x, d) for x in termos):
        return "texto"
    return None


# ------------------------------------------------------------------ gravação
def _ja_sincronizou(conn, src_id: str) -> bool:
    return db.get_meta(conn, f"bot_sync:{src_id}") == "1"


def ingerir(conn, src: dict, items: list[dict], cfg: dict, *, first_sync: bool = False) -> dict:
    """Grava só o que é remoto e aceita o Brasil. `first_sync` do pipeline é ignorado:
    o bot controla a própria primeira sincronização (para não avisar vaga antiga)."""
    stats = {"seen": len(items), "new": 0, "updated": 0, "skipped": 0, "new_ids": []}
    backfill = not _ja_sincronizou(conn, src["id"]) and src.get("modo") != "manual"
    now = now_iso()
    for item in items:
        if not item.get("source_item_id"):
            stats["skipped"] += 1
            continue
        item["source_item_id"] = str(item["source_item_id"])
        with db._lock:
            row = conn.execute("SELECT id FROM jobs WHERE source_id=? AND source_item_id=?",
                               (src["id"], item["source_item_id"])).fetchone()
            if row:
                conn.execute("UPDATE jobs SET last_seen_at=? WHERE id=?", (now, row["id"]))
                continue
        if src.get("classificar_posts"):
            tipo = classify_post(item.get("title"), item.get("description"), item.get("post_kind_hint"))
            # subs gerais têm muita discussão: lá só entra o que é claramente contratação
            if tipo == "for_hire" or (src.get("so_contratacao") and tipo != "hiring"):
                stats["skipped"] += 1
                continue
        modo = modo_trabalho(item, src)
        ok_pais, motivo = aceita_brasil(item, src)
        if modo != "remote" or not ok_pais:
            stats["skipped"] += 1
            continue
        if backfill:
            kind = "backfill"
        elif src.get("modo") == "manual":
            kind = "manual"
        else:
            age = hours_since(item.get("published_at"))
            kind = "late" if age is not None and age > pipeline.LIVE_MAX_AGE_H else "live"
        comp = item.get("comp") or extract_compensation(item.get("title"), item.get("description"))
        rec = {
            "source_id": src["id"], "source_item_id": item["source_item_id"], "source_name": src["name"],
            "community": item.get("community"), "source_url": item.get("source_url"),
            "canonical_url": dedupe.canonical_url(item.get("source_url")),
            "apply_url": item.get("apply_url") or item.get("source_url"), "title": item.get("title"),
            "company": item.get("company"), "author": item.get("author"), "description": item.get("description"),
            "published_at": item.get("published_at"), "discovered_at": now, "last_seen_at": now,
            "discovery_kind": kind, "post_kind": "hiring",
            "comp_min": (comp or {}).get("min"), "comp_max": (comp or {}).get("max"),
            "comp_currency": (comp or {}).get("currency"), "comp_unit": (comp or {}).get("unit"),
            "comp_text": (comp or {}).get("text"), "remote_scope": "worldwide",  # só chega aqui o que aceita o Brasil
            "location_text": item.get("location_text"), "title_norm": normalize_title(item.get("title")),
            "fingerprint": dedupe.fingerprint(item.get("title"), item.get("author") or item.get("company")),
            "content_hash": pipeline.content_hash(item),
            "raw": db.dumps({"item": item.get("raw"), "tags": item.get("tags")}),
        }
        with db._lock:
            dup_of, dup_reason = dedupe.find_duplicate(conn, rec)
            rec.update(duplicate_of=dup_of, dup_reason=dup_reason)
            cur = conn.execute(f"INSERT INTO jobs ({', '.join(rec)}) VALUES ({', '.join(':' + k for k in rec)})", rec)
            stats["new"] += 1
            stats["new_ids"].append(cur.lastrowid)
    if src.get("modo") != "manual":
        db.set_meta(conn, f"bot_sync:{src['id']}", "1")
    with db._lock:
        conn.commit()
    return stats


# ------------------------------------------------------------------ alertas
def termos_ativos(conn) -> list[str]:
    vistos, out = set(), []
    for r in conn.execute("SELECT termos FROM alertas WHERE ativo=1"):
        for t in separar(r["termos"]):
            if norm(t) not in vistos:
                vistos.add(norm(t))
                out.append(t)
    return out


def criar_alerta(conn, user_id, termos: str, excluir: str | None = None, *, canal_id) -> int:
    """Alerta que posta as vagas no canal do servidor onde foi criado."""
    if not separar(termos):
        raise ValueError("escreva pelo menos uma palavra, ex.: python, backend")
    with db._lock:
        cur = conn.execute("INSERT INTO alertas(user_id, termos, excluir, criado_em, canal_id) VALUES (?,?,?,?,?)",
                           (str(user_id), termos.strip(), (excluir or "").strip() or None, now_iso(), str(canal_id)))
        conn.commit()
    return cur.lastrowid


def desativar_canal(conn, canal_id) -> int:
    """Canal apagado: os alertas dele param."""
    with db._lock:
        cur = conn.execute("UPDATE alertas SET ativo=0 WHERE canal_id=?", (str(canal_id),))
        conn.commit()
    return cur.rowcount


def listar_alertas(conn, user_id) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM alertas WHERE user_id=? AND ativo=1 ORDER BY id", (str(user_id),))]


def apagar_alerta(conn, user_id, alerta_id: int) -> bool:
    with db._lock:
        cur = conn.execute("UPDATE alertas SET ativo=0 WHERE id=? AND user_id=?", (int(alerta_id), str(user_id)))
        conn.commit()
    return cur.rowcount > 0


def buscar(conn, termos: str, excluir: str | None = None, *, dias: int = 15, limite: int = 10) -> tuple[list[dict], int]:
    """Busca nas vagas já coletadas. Título primeiro, depois quem cita no texto."""
    ts, ex = separar(termos), separar(excluir)
    desde = iso(utcnow() - timedelta(days=dias))
    rows = conn.execute(
        """SELECT * FROM jobs WHERE duplicate_of IS NULL AND hidden=0 AND post_kind!='for_hire'
           AND COALESCE(published_at, discovered_at) >= ? ORDER BY COALESCE(published_at, discovered_at) DESC""",
        (desde,),
    ).fetchall()
    titulo, texto = [], []
    for r in rows:
        j = dict(r)
        onde = casa(j, ts, ex)
        j["_onde"] = onde
        if onde == "titulo":
            titulo.append(j)
        elif onde == "texto":
            texto.append(j)
    todos = titulo + texto
    return todos[:limite], len(todos)


def preparar_envios(conn, max_dias: int = 3) -> dict[str, list[dict]]:
    """{id do canal: vagas novas}. Só vagas descobertas depois do alerta, que combinam
    e que ainda não foram postadas naquele canal (dois alertas no mesmo canal não repetem vaga)."""
    desde = iso(utcnow() - timedelta(days=max_dias))
    jobs = [dict(r) for r in conn.execute(
        """SELECT * FROM jobs WHERE discovery_kind IN ('live','manual') AND duplicate_of IS NULL AND hidden=0
           AND discovered_at >= ? ORDER BY discovered_at""", (desde,))]
    envios: dict[str, list[dict]] = {}
    for a in conn.execute("SELECT * FROM alertas WHERE ativo=1 AND canal_id IS NOT NULL").fetchall():
        canal, ts, ex = a["canal_id"], separar(a["termos"]), separar(a["excluir"])
        ja = {r[0] for r in conn.execute("SELECT job_id FROM enviadas WHERE user_id=?", (canal,))}
        for j in jobs:
            if j["discovered_at"] < a["criado_em"] or j["id"] in ja:
                continue
            if casa(j, ts, ex) and all(j["id"] != x["id"] for x in envios.get(canal, [])):
                envios.setdefault(canal, []).append(j)
    return envios


def marcar_enviadas(conn, canal_id, job_ids) -> None:
    with db._lock:
        conn.executemany("INSERT OR IGNORE INTO enviadas(user_id, job_id, sent_at) VALUES (?,?,?)",
                         [(str(canal_id), int(j), now_iso()) for j in job_ids])
        conn.commit()


# ------------------------------------------------------------------ coleta
def _fonte_com_termos(src: dict, termos: list[str]) -> dict:
    s = copy.deepcopy(src)
    s["params"]["queries"] = termos
    return s


def coletar(conn, cfg: dict, *, force: bool = False) -> list[dict]:
    """Coleta as fontes que estão no horário. Fontes por termo usam as palavras dos alertas."""
    fontes = [f for f in cfg["fontes"] if f.get("enabled", True) and all(os.environ.get(v) for v in f.get("requer_env", []))]
    db.sync_sources(conn, [{**f, "type": f.get("modo")} for f in fontes])
    termos = termos_ativos(conn)
    resultados = []
    with pipeline._run_lock:
        for src in pipeline.due_sources(conn, fontes, force=force):
            if src.get("modo") == "termos":
                if not termos:
                    continue
                src = _fonte_com_termos(src, termos)
            res = pipeline.run_source(conn, src, cfg, ingest=ingerir)
            resultados.append(res)
            log.info("%-14s %-12s vistas=%-4s novas=%-4s descartadas=%-4s %s", src["id"], res["status"],
                     res["seen"], res["new"], res["skipped"], res["error"] or "")
    return resultados


def buscar_online(conn, cfg: dict, termos: list[str], cooldown_min: int = 30) -> None:
    """Na hora de um /vagas com palavra nova, consulta as fontes por termo (Himalayas, Freelancer…).
    Cada termo é consultado no máximo a cada `cooldown_min` minutos."""
    novos = []
    for t in termos:
        k = f"bot_termo:{norm(t)}"
        last = db.get_meta(conn, k)
        if last and hours_since(last) is not None and hours_since(last) * 60 < cooldown_min:
            continue
        db.set_meta(conn, k, now_iso())
        novos.append(t)
    if not novos:
        return
    with pipeline._run_lock:
        for src in cfg["fontes"]:
            if src.get("enabled", True) and src.get("modo") == "termos":
                try:
                    items = get_collector(src["method"])(_fonte_com_termos(src, novos))
                    ingerir(conn, src, items, cfg)
                except Exception as e:  # noqa: BLE001 — busca online é bônus; falha não atrapalha a resposta
                    log.warning("busca online em %s falhou: %s", src["id"], e)


# ------------------------------------------------------------------ vaga encaminhada / colada
ENCAMINHADAS = {"id": "encaminhadas", "name": "Encaminhada/colada no bot", "method": "manual", "modo": "manual",
                "default_remote": True, "interval_min": 525600, "enabled": True}

LINKEDIN_POST = re.compile(r"linkedin\.com/(posts|feed/update|pulse)/", re.I)
LINKEDIN_VAGA = re.compile(r"linkedin\.com/(comm/)?jobs/view/", re.I)
LINK_FONTE = re.compile(r"https?://\S*(linkedin\.com/(posts|feed/update|pulse|jobs/view|comm/jobs/view)|discord\.com/channels"
                        r"|upwork\.com/(jobs|freelance-jobs)|ytjobs\.co/job)\S*", re.I)

# Post do LinkedIn só entra com certeza: sinal de contratação + contato concreto + "remoto" escrito.
CONTRATANDO = re.compile(
    r"\b(we('re| are) hiring|now hiring|hiring|estamos contratando|contratando|contrata-se|recrutando)\b"
    r"|\bvagas? (aberta|abertas|dispon[ií]ve(l|is)|para|de)\b|\boportunidade (de|para|na|no)\b"
    r"|\b(procuramos|buscamos|estamos (procurando|buscando)|we('re| are) looking for|looking for an?|seeking an?)\b"
    r"|#(hiring|vagas?|vagaaberta|contratando|jobs?|oportunidade)\b",
    re.I,
)
PROCURANDO_EMPREGO = re.compile(
    r"#?open ?to ?work|\bestou (em busca|procurando|dispon[ií]vel|aberto|aberta)\b|\bbusco (uma )?(nova )?(oportunidade|recoloca|vaga)"
    r"|\bprocuro (uma )?(nova )?(vaga|oportunidade)|\bi('m| am) (looking for|open to|seeking) (a |my )?(new )?(role|job|position|opportunit)",
    re.I,
)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
LINK_CANDIDATURA = re.compile(r"https?://(?!(?:[\w-]+\.)?linkedin\.com/(?:in|posts|feed|company|pulse|hashtag)/)[^\s)>\]]+", re.I)
INSTRUCAO = re.compile(
    r"\b(envie|mande|encaminhe|enviar)\b.{0,30}\b(cv|curr[ií]culo|portf[oó]lio|mensagem|dm|inbox)\b"
    r"|\b(send|email|dm|message)\b.{0,25}\b(cv|resume|portfolio|me|us)\b"
    r"|\b(me|nos) (chame|chama|mande|envie)\b.{0,20}\b(direct|inbox|privado|dm|mensagem)\b"
    r"|\bcandidate-se\b|\binscreva-se\b|\bapply (here|now|at|via|through|on)\b|\binscri[cç][oõ]es\b",
    re.I,
)


def post_de_vaga_com_contato(texto: str) -> tuple[bool, str | None]:
    if PROCURANDO_EMPREGO.search(texto):
        return False, "parece alguém procurando emprego, não uma vaga"
    if not CONTRATANDO.search(texto):
        return False, "não achei sinal claro de contratação"
    if not (EMAIL.search(texto) or LINK_CANDIDATURA.search(texto) or INSTRUCAO.search(texto)):
        return False, "não achei contato (e-mail, link de candidatura ou “me mande mensagem”)"
    return True, None


def link_da_vaga(texto: str) -> str | None:
    """Num texto colado, prefere o link do post/vaga (LinkedIn, Discord, Upwork, YT Jobs) ao link de candidatura."""
    m = LINK_FONTE.search(texto or "") or re.search(r"https?://\S+", texto or "")
    return m.group(0).rstrip(".,)>]") if m else None


def _origem(link: str | None) -> tuple[str | None, dict]:
    """(nome da comunidade, regras da fonte) conforme o link."""
    if not link:
        return None, ENCAMINHADAS
    if LINKEDIN_POST.search(link):
        return "LinkedIn (post)", {**ENCAMINHADAS, "default_remote": False}
    if LINKEDIN_VAGA.search(link):
        return "LinkedIn (vaga)", {**ENCAMINHADAS, "default_remote": False}
    if "discord.com" in link:
        return "Discord", ENCAMINHADAS
    if "upwork.com" in link:
        return "Upwork", ENCAMINHADAS
    if "ytjobs.co" in link:
        return "YT Jobs", ENCAMINHADAS
    return None, ENCAMINHADAS


def importar(conn, texto: str, link: str | None, criada_em=None, autor: str | None = None) -> dict:
    """Vaga que alguém encaminhou ou colou para o bot. Mesmas regras: só remota e aberta ao Brasil.
    No LinkedIn (que dá muito post de presencial e de gente procurando emprego) a vaga precisa
    dizer "remoto" com todas as letras e, se for post, ter sinal de contratação e contato."""
    texto = (texto or "").strip()
    if len(texto) < 20:
        return {"status": "curta"}
    comunidade, regras = _origem(link)
    if comunidade == "LinkedIn (post)":
        ok, motivo = post_de_vaga_com_contato(texto)
        if not ok:
            return {"status": "sem_certeza", "motivo": motivo}
    titulo = next((l.strip(" *#_") for l in texto.splitlines() if l.strip()), "")[:140]
    item_id = f"msg:{link}" if link else f"txt:{sha1(texto[:800])[:16]}"
    item = {"source_item_id": item_id, "title": titulo, "description": texto, "source_url": link, "apply_url": link,
            "published_at": to_iso(criada_em), "author": autor, "company": None, "community": comunidade,
            "location_text": None, "tags": [], "comp": None, "employment_type": None, "post_kind_hint": "hiring", "raw": {}}
    modo = modo_trabalho(item, regras)
    if modo != "remote":
        return {"status": "nao_remota", "modo": modo}
    ok, motivo = aceita_brasil(item, regras)
    if not ok:
        return {"status": "outro_pais", "motivo": motivo}
    db.sync_sources(conn, [{**ENCAMINHADAS, "type": "manual"}])
    stats = ingerir(conn, ENCAMINHADAS, [item], {})
    row = conn.execute("SELECT * FROM jobs WHERE source_id='encaminhadas' AND source_item_id=?", (item_id,)).fetchone()
    return {"status": "salva" if stats["new"] else "repetida", "job": dict(row) if row else None}


# ------------------------------------------------------------------ limpeza do banco antigo
def remover_nao_aceitas(conn, cfg: dict) -> dict:
    """Apaga do banco as vagas presenciais, híbridas ou restritas a outros países."""
    fontes = {f["id"]: f for f in cfg["fontes"]}
    apagar, corrigir, motivos = [], [], {"presencial/híbrida": 0, "outro país": 0}
    for r in conn.execute("SELECT * FROM jobs").fetchall():
        j = dict(r)
        raw = (json.loads(j["raw"]) if j.get("raw") else None) or {}
        # O painel antigo gravava no campo de local um texto explicativo ("local não informado…");
        # o local que veio da fonte fica em raw["location_text"].
        loc = raw["location_text"] if "location_text" in raw else j["location_text"]
        item = {"title": j["title"], "description": j["description"], "location_text": loc,
                "tags": raw.get("tags") or [], "raw": raw.get("item") or {}}
        src = fontes.get(j["source_id"], {"default_remote": True})
        if modo_trabalho(item, src) != "remote":
            apagar.append(j["id"]); motivos["presencial/híbrida"] += 1
        elif not aceita_brasil(item, src)[0]:
            apagar.append(j["id"]); motivos["outro país"] += 1
        elif loc != j["location_text"]:  # devolve o local original da fonte (o painel antigo guardava uma explicação)
            corrigir.append((loc, j["id"]))
    with db._lock:
        conn.executemany("UPDATE jobs SET location_text=? WHERE id=?", corrigir)
        for i in apagar:  # repostagens ligadas a uma vaga apagada passam a apontar para nada
            conn.execute("UPDATE jobs SET duplicate_of=NULL WHERE duplicate_of=?", (i,))
        conn.executemany("DELETE FROM jobs WHERE id=?", [(i,) for i in apagar])
        conn.executemany("DELETE FROM enviadas WHERE job_id=?", [(i,) for i in apagar])
        conn.commit()
    return {"apagadas": len(apagar), **motivos}
