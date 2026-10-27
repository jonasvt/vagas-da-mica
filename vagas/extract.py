"""Extração por regras (sem LLM) dos campos normalizados de uma vaga.

Princípio: só registra o que está escrito no post. Se o valor, a data ou um
requisito não aparece, o campo fica vazio — nunca é inventado. Cada valor
lido guarda o trecho original (`comp_text`) para conferência.
"""
from __future__ import annotations

import re

# ----------------------------------------------------------------- tipo de post
HIRING_TITLE = re.compile(
    r"\[(hiring|task|paid|job|hire)\]|\bhiring\b"
    r"|\b(looking for|lf|lfg|in search of|searching for)\b (an? |some(one)? )?([\w-]+ ){0,5}(editors?|editing|animators?|clippers?|designers?)"
    r"|\b(editors?|animators?|clippers?)\s(needed|required|wanted)\b"
    r"|\b(need|needed|seeking|wanted|want)\b.{0,30}\b(editor|editing|animator|clipper)|\bpaying\b|\bbudget\b|\bpaid\b.{0,20}\b(editor|project|gig)",
    re.I,
)
FORHIRE_TITLE = re.compile(
    r"\[(for ?hire|fh|offer|offering)\]|\bfor[- ]hire\b|\bavailable for\b|\bmy (portfolio|work|reel)\b|\bhire me\b"
    r"|\bi('m| am) an? .{0,25}(editor|animator)|\bi will (edit|make|create)\b|\bopen for (work|commissions)|\btaking (clients|commissions)"
    r"|\blooking for (an? |my )?(first )?(job|work|clients?|gigs?|opportunit)|\bhow (do|can|should) i\b|\bhow much (should|do|can|would) (i|you)\b.{0,25}\b(charge|ask|price|quote)|\bfree (video )?(editor|editing|offer|edits?)\b"
    r"|^\W*(i|we) (build|edit|create|make|offer|help)\b|^\W*(professional|experienced|expert|skilled|creative|freelance)\s+(video\s+)?(editor|monteur)\b",
    re.I,
)
HIRING_BODY = re.compile(
    r"\b(we('re| are)|i('m| am)) (hiring|looking for|searching for|in need of)\b|\bsend (me )?(your|a) (portfolio|samples|work|rates)"
    r"|\bdm me (your|with)|\bbudget\b|\bwill pay\b|\bpaying\b|\bper video\b|\bapply\b",
    re.I,
)
FORHIRE_BODY = re.compile(
    r"\b(check out|here'?s|here is) my (portfolio|work|reel)|\bmy rates\b|\bi('m| am) an? .{0,30}(editor|animator)\b(?!.{0,40}looking for)"
    r"|\bi (can|will) edit\b|\bhire me\b|\bmy services\b|\bi offer\b",
    re.I,
)


def classify_post(title: str, body: str, hint: str | None = None) -> str:
    """hiring | for_hire | unclear."""
    if hint in ("hiring", "for_hire"):
        return hint
    t = title or ""
    if FORHIRE_TITLE.search(t) and not re.search(r"\[(hiring|task)\]", t, re.I):
        return "for_hire"
    if HIRING_TITLE.search(t):
        return "hiring"
    b = (body or "")[:1500]
    h, f = len(HIRING_BODY.findall(b)), len(FORHIRE_BODY.findall(b))
    if h > f:
        return "hiring"
    if f > h:
        return "for_hire"
    return "unclear"


# ----------------------------------------------------------------- remuneração
_CUR = r"(?P<cur>US\s?\$|USD\s?\$?|CA\$|C\$|CAD\s?\$?|A\$|AU\$|AUD\s?\$?|R\$|€|£|₹|Rs\.?|INR|EUR|GBP|\$)"
# 1,500.50 (EUA) | 4.500,00 (Brasil) | 4.5 / 20
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+(?:\.\d+)?"
MONEY = re.compile(
    rf"{_CUR}\s?(?P<a>{_NUM})\s?(?P<ak>k\b)?"
    rf"(?:\s?(?:-|–|—|to|~|a|/)\s?(?:{_CUR.replace('cur', 'cur2')})?\s?(?P<b>{_NUM})\s?(?P<bk>k\b)?)?"
    rf"|(?P<a2>{_NUM})\s?(?P<a2k>k\b)?\s?(?:-|–|to)?\s?(?P<b2>{_NUM})?\s?(?P<b2k>k\b)?\s?(?P<cur3>USD|usd|dollars|EUR|eur|euros|GBP|INR|rupees|rs\b|CAD|AUD|BRL|reais)",
    re.I,
)
CURRENCY_MAP = {
    "US$": "USD", "USD": "USD", "USD$": "USD", "DOLLARS": "USD", "$": "$",
    "CA$": "CAD", "C$": "CAD", "CAD": "CAD", "CAD$": "CAD",
    "A$": "AUD", "AU$": "AUD", "AUD": "AUD", "AUD$": "AUD",
    "R$": "BRL", "BRL": "BRL", "REAIS": "BRL",
    "€": "EUR", "EUR": "EUR", "EUROS": "EUR",
    "£": "GBP", "GBP": "GBP",
    "₹": "INR", "RS": "INR", "RS.": "INR", "INR": "INR", "RUPEES": "INR",
}
UNIT_PATTERNS = [
    ("hour", r"/\s?h(?:ou)?r\b|/\s?h\b|per hour|an hour|hourly|p/h|ph\b|por hora"),
    ("short", r"per (?:short|reel|clip|tiktok)|/\s?(?:short|reel|clip)|each (?:short|reel|clip)|a (?:short|reel|clip)"),
    ("minute", r"per (?:finished )?min(?:ute)?|/\s?min(?:ute)?\b|a minute"),
    ("video", r"per (?:\w+[- ])?(?:video|vid|edit|episode|upload|long[- ]?form)|/\s?(?:video|vid|edit|episode)|each video|a video|for (?:the|a|each|one) (?:complete |whole |entire |full |finished )?video"),
    ("month", r"per month|/\s?mo(?:nth)?\b|monthly|a month|p/m|por m[eê]s|/\s?m[eê]s\b|mensa(?:l|is)"),
    ("week", r"per week|/\s?w(?:ee)?k\b|weekly|a week"),
    ("year", r"per year|/\s?y(?:ea)?r\b|annual(?:ly)?|a year|salary|p\.?a\.?\b|por ano|anua(?:l|is)"),
    ("project", r"per project|flat|fixed|total|for the (?:whole )?project|one[- ]time|for (?:all|both|the) videos?"),
]
UNIT_PATTERNS = [(u, re.compile(p, re.I)) for u, p in UNIT_PATTERNS]
REV_SHARE = re.compile(r"rev(?:enue)?[\s-]?share|% of (?:the )?(?:revenue|profits?|earnings|adsense)|percentage of (?:revenue|profits?)|profit[- ]share", re.I)
PAY_CONTEXT = re.compile(r"pay|budget|rate|compensation|salary|offer|paid|\$|usd|per video|per hour|retainer|fee|price", re.I)


def _to_num(s: str | None, k: str | None) -> float | None:
    if not s:
        return None
    try:
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", s):  # formato brasileiro
            s = s.replace(".", "").replace(",", ".")
        n = float(s.replace(",", ""))
    except ValueError:
        return None
    if k:
        n *= 1000
    return n


def _unit_near(text: str, start: int, end: int) -> str:
    after = text[end : end + 45]
    for unit, rx in UNIT_PATTERNS:
        if rx.search(after[:28]):
            return unit
    before = text[max(0, start - 45) : start]
    for unit, rx in UNIT_PATTERNS:
        if rx.search(before):
            return unit
    for unit, rx in UNIT_PATTERNS:
        if rx.search(after):
            return unit
    return "unknown"


BARE_PAY = re.compile(
    r"\b(?:pay(?:ing)?|budget(?: is)?|rate(?: is)?|offer(?:ing)?)\s(?:of\s|around\s|about\s|up to\s)?(?P<a>\d{2,6})(?:\s?(?:-|–|to)\s?(?P<b>\d{2,6}))?\s?(?=per|/|a |each|for)",
    re.I,
)


def extract_compensation(title: str, body: str) -> dict | None:
    """Primeiro valor monetário plausível (título tem prioridade sobre o corpo).

    Valor sem moeda explícita ("paying 200 per reel") é guardado com moeda vazia —
    a moeda nunca é presumida.
    """
    found = _extract_money(title, body)
    if found:
        return found
    for text in (title or "", body or ""):
        m = BARE_PAY.search(text)
        if m:
            lo = float(m.group("a"))
            hi = float(m.group("b")) if m.group("b") else lo
            snippet = text[max(0, m.start() - 30) : m.end() + 40].replace("\n", " ").strip()
            return {"min": lo, "max": max(lo, hi), "currency": None, "unit": _unit_near(text, m.start(), m.end()), "text": snippet}
    return None


def _extract_money(title: str, body: str) -> dict | None:
    for text in (title or "", body or ""):
        for m in MONEY.finditer(text):
            g = m.groupdict()
            if g.get("a"):
                cur_raw, a, ak, b, bk = g["cur"], g["a"], g["ak"], g["b"], g["bk"]
            else:
                cur_raw, a, ak, b, bk = g["cur3"], g["a2"], g["a2k"], g["b2"], g["b2k"]
            tail = text[m.end() : m.end() + 12].lower()
            if re.match(r"\s?(m\b|mil|million|b\b|billion|\+? ?subs|\+? ?views|\+? ?followers)", tail):
                continue  # receita de empresa, inscritos, visualizações — não é pagamento
            lo, hi = _to_num(a, ak), _to_num(b, bk)
            if lo is None or lo <= 0:
                continue
            if hi is not None and (hi < lo or hi > lo * 50):
                hi = None  # "a/b" que não é faixa (ex.: datas, "2/3")
            cur = CURRENCY_MAP.get((cur_raw or "").upper().replace(" ", ""), (cur_raw or "").upper())
            after_cur = re.match(r"\s?(usd|cad|aud|us dollars)\b", text[m.end() : m.end() + 14], re.I)
            if cur == "$" and after_cur:  # "$180 USD" => USD explícito
                cur = CURRENCY_MAP.get(after_cur.group(1).upper(), "USD")
            if g.get("cur3") is None and cur == "$" and lo < 1:
                continue
            ctx = text[max(0, m.start() - 60) : m.end() + 60]
            if not g.get("a") and not PAY_CONTEXT.search(ctx):
                continue
            unit = _unit_near(text, m.start(), m.end())
            snippet = text[max(0, m.start() - 40) : m.end() + 40].replace("\n", " ").strip()
            return {"min": lo, "max": hi if hi is not None else lo, "currency": cur, "unit": unit, "text": snippet}
    return None


def payment_model(comp: dict | None, title: str, body: str) -> str:
    text = f"{title}\n{body}"
    if comp:
        u = comp.get("unit")
        if u in ("video", "short", "minute"):
            return "per_video"
        if u == "hour":
            return "hourly"
        if u in ("month", "week"):
            return "monthly_retainer"
        if u == "year":
            return "salary"
        if u == "project":
            return "fixed"
    if REV_SHARE.search(text):
        return "revenue_share"
    if re.search(r"\bretainer\b", text, re.I):
        return "monthly_retainer"
    return "unspecified"


# ----------------------------------------------------------------- tipo de contrato
EMPLOYMENT = [
    ("Full-time", r"full[- ]?time|\bft\b|tempo integral"),
    ("Part-time", r"part[- ]?time|\bpt\b"),
    ("Contract", r"\bcontract(or)?\b|\bcontratante\b"),
    ("Freelance", r"freelanc"),
    ("Internship", r"\bintern(ship)?\b"),
    ("Long-term", r"long[- ]term|ongoing|recurring|retainer|consistent work"),
    ("One-off", r"one[- ]?(off|time) (project|job|gig)|single (video|project)"),
]


def employment_type(given: str | None, title: str, body: str) -> str | None:
    found = []
    if given:
        found.append(given if isinstance(given, str) else ", ".join(given))
    text = f"{title}\n{body}"
    for label, rx in EMPLOYMENT:
        if re.search(rx, text, re.I) and not any(label.lower() in f.lower() for f in found):
            found.append(label)
    return ", ".join(found) if found else None


# ----------------------------------------------------------------- remoto / localização
ONSITE = re.compile(r"\bon[- ]?site\b|\bin[- ]office\b|\bin person\b|\bhybrid\b|\brelocat|\bvideographer\b.{0,40}\b(in|at)\b [A-Z]", re.I)
RESTRICTED = re.compile(
    r"\b(us|usa|u\.s\.?|uk|canada|eu|europe|australia|philippines|india|latam|na)[- ](only|based|residents?|citizens?)\b"
    r"|\bmust (be|live) (located|based|living|residing|in)\b|\bonly (us|usa|uk|canada|eu)\b|\bresidents? of\b"
    r"|\b(eligible|authori[sz]ed) to work in\b|\bwork authori[sz]ation\b|\bcitizens? (of|only)\b"
    r"|\b(est|edt|pst|pdt|cst|cdt|gmt|bst|cet|aest|ist)\b.{0,25}\b(hours|time ?zone|overlap|required|availability)"
    r"|\b(time ?zone|timezone)\b.{0,30}\b(required|must|overlap|only)",
    re.I,
)
TITLE_REGION = re.compile(
    r"[(\[](us|usa|u\.s\.|uk|eu|emea|europe|latam|apac|na|north america|canada|australia|india|philippines|germany|france|spain|brazil|mexico|sg)"
    r"([- ]?(only|based|remote|residents?))?(/remote)?[)\]]|\bremote[- ](us|usa|uk|eu|canada|europe|latam)\b",
    re.I,
)
WORLDWIDE = re.compile(
    r"\bworld[- ]?wide\b|\banywhere\b|\bany country\b|\bglobal(ly)?\b|\binternational (applicants|editors|candidates)?\s?(welcome|ok|accepted)?"
    r"|\ball countries\b|\bfrom anywhere\b|\bopen to (all|everyone|anyone)\b|\bno location (requirement|restriction)",
    re.I,
)


def remote_scope(location_text: str | None, title: str, body: str, *, default_remote: bool) -> tuple[str, str | None]:
    """(escopo, motivo). escopo: worldwide | restricted | onsite | unknown."""
    text = f"{title}\n{body}"
    loc = (location_text or "").strip()
    m = TITLE_REGION.search(title or "")
    if m:
        return "restricted", f"região no título: “{m.group(0)}”"
    if loc and (WORLDWIDE.search(loc) or re.search(r"sem restri", loc, re.I)):
        return "worldwide", f"local informado: {loc}"
    m = search_affirmative(ONSITE, text) or (ONSITE.search(loc) if loc else None)
    if m and not re.search(r"\bremote\b", loc, re.I):
        return "onsite", f"presencial/híbrido: “{m.group(0)}”"
    if loc:
        if not re.fullmatch(r"(?i)remote|remoto|n/?a", loc):
            return "restricted", f"local informado: {loc}"
    m = RESTRICTED.search(text)
    if m:
        return "restricted", f"restrição no texto: “{m.group(0).strip()}”"
    m = WORLDWIDE.search(text)
    if m:
        return "worldwide", f"aceita qualquer país: “{m.group(0).strip()}”"
    return ("unknown", "local não informado (remoto implícito)") if default_remote else ("unknown", "local não informado")


# ----------------------------------------------------------------- software, nicho, experiência, idioma
SOFTWARE = {
    "Premiere Pro": r"premiere|\bppro\b",
    "After Effects": r"after ?effects|\bae\b",
    "DaVinci Resolve": r"davinci|\bresolve\b",
    "Final Cut Pro": r"final ?cut|\bfcpx?\b",
    "CapCut": r"cap ?cut",
    "Photoshop": r"photoshop",
    "Illustrator": r"illustrator",
    "Blender": r"blender",
    "Cinema 4D": r"cinema ?4d|\bc4d\b",
    "Audition": r"\baudition\b",
    "Descript": r"descript\b",
    "Canva": r"\bcanva\b",
    "Vegas Pro": r"vegas pro|sony vegas",
    "Avid": r"\bavid\b|media composer",
}

SPECIALIZATIONS = {
    "youtube": r"youtube|\byt\b|youtuber",
    "long_form": r"long[- ]?form|long videos?|full[- ]length|\b(8|10|12|15|20|30)\s?(-|–|to)\s?\d{2}\s?min|\bmain channel videos?\b",
    "short_form": r"\bshorts?\b|\breels?\b|tik ?toks?|short[- ]?form|vertical (videos?|content)|\b9:16\b",
    "gaming": r"gaming|gameplay|minecraft|roblox|fortnite|\bgta\b|valorant|\bcs2\b|league of legends|streamer|twitch|\bgames?\b",
    "podcast": r"podcast",
    "repurposing": r"repurpos|\bclips?\b|clipping|clipper|highlights?|stream (vods?|highlights)|cut ?downs?",
    "motion_graphics": r"motion graphics?|motion design|kinetic typography|animated (text|captions|infographics)",
    "documentary": r"documentar|video essay|storytelling|narrat",
    "talking_head": r"talking[- ]head",
    "vlog": r"\bvlogs?\b",
    "ads_ugc": r"\bugc\b|\bads?\b|commercials?|promo(tional)? videos?|\bvsl\b|direct response",
    "animation": r"animat(e|ion|or|ed)\b|\b2d\b|\b3d\b",
    "thumbnail": r"thumbnail",
}

LANGS = {
    "English": r"english|native speaker", "Spanish": r"spanish|español", "Portuguese": r"portugu",
    "French": r"french|français", "German": r"german|deutsch", "Hindi": r"hindi", "Arabic": r"arabic",
    "Italian": r"italian", "Japanese": r"japanese", "Russian": r"russian",
}


NEGATION = re.compile(r"\b(no|not|never|without|isn'?t|aren'?t|won'?t|don'?t|nor|zero)\b[^.!?\n]{0,40}$", re.I)


def search_affirmative(pattern, text: str):
    """Como re.search, mas ignora ocorrências negadas ("No unpaid work", "not a free test")."""
    rx = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern, re.I)
    for m in rx.finditer(text or ""):
        if not NEGATION.search(text[max(0, m.start() - 60) : m.start()]):
            return m
    return None


def find_all(mapping: dict, text: str) -> list[str]:
    return [k for k, rx in mapping.items() if re.search(rx, text, re.I)]


def experience(title: str, body: str) -> str | None:
    text = f"{title}\n{body}"
    m = re.search(r"(\d{1,2})\s?\+?\s?(?:years?|yrs?)(?: of)?(?: \w+){0,3} (?:experience|exp)", text, re.I)
    if m:
        return f"{m.group(1)}+ anos"
    if re.search(r"beginners? (welcome|ok|friendly)|no experience (needed|required)|entry[- ]level|newbies?", text, re.I):
        return "Iniciante aceito"
    if re.search(r"\bjunior\b", text, re.I):
        return "Júnior"
    if re.search(r"\bsenior\b|\bexpert\b|highly experienced|proven (track|experience)|professional experience", text, re.I):
        return "Experiente"
    if re.search(r"\bexperience[d]?\b", text, re.I):
        return "Pede experiência"
    return None


def languages(title: str, body: str) -> list[str]:
    text = f"{title}\n{body}"
    found = find_all(LANGS, text)
    # "English" só conta se o texto fala de requisito de idioma, não só porque o post é em inglês
    if "English" in found and not re.search(r"(fluent|native|good|strong|excellent|proficient|speak|understand)\w* (in )?english|english (speaker|fluency|required|proficiency)", text, re.I):
        found.remove("English")
    return found


# ----------------------------------------------------------------- legitimidade
LEGIT_RULES = [
    ("Contato só por Telegram/WhatsApp", r"telegram|whats ?app|signal app", 0.15),
    ("Pede pagamento/taxa do candidato", r"(registration|application|training|starter|onboarding) fee|pay (a|the|an) (small )?fee|security deposit|buy (a|our) (course|kit|license)", 0.4),
    ("Pagamento em cripto", r"\b(crypto|usdt|bitcoin|btc|ethereum)\b", 0.05),
    ("Teste não pago", r"(free|unpaid) (test|trial|sample)( edit)?", 0.1),
    ("Pede dados pessoais sensíveis", r"(ssn|social security|passport|bank (login|password)|id card)", 0.4),
    ("Promessa vaga de dinheiro fácil", r"easy money|earn \$?\d+.{0,20}(daily|a day|per day) from home|get rich", 0.3),
    ("Sem pagamento / exposição", r"\bunpaid\b|for exposure|\bvolunteer\b|no pay\b|free of charge", 0.2),
]


def legitimacy(title: str, body: str, comp: dict | None, base: float) -> tuple[float, list[str]]:
    text = f"{title}\n{body}"
    conf, flags = base, []
    for label, rx, penalty in LEGIT_RULES:
        if search_affirmative(rx, text):
            flags.append(label)
            conf -= penalty
    if comp and comp.get("currency") in ("USD", "$"):
        hi, unit = comp.get("max") or 0, comp.get("unit")
        if (unit in ("video", "short") and hi > 5000) or (unit == "hour" and hi > 200) or (unit == "project" and hi > 20000):
            flags.append("Valor muito acima do comum — confira")
            conf -= 0.1
    return round(max(0.05, min(0.95, conf)), 2), flags


# ----------------------------------------------------------------- tudo junto
def extract_all(item: dict, *, default_remote: bool, base_legit: float) -> dict:
    title, body = item.get("title") or "", item.get("description") or ""
    comp = item.get("comp") or extract_compensation(title, body)
    scope, scope_reason = remote_scope(item.get("location_text"), title, body, default_remote=default_remote)
    text = f"{title}\n{body}\n{' '.join(map(str, item.get('tags') or []))}"
    legit_conf, legit_flags = legitimacy(title, body, comp, base_legit)
    return {
        "post_kind": classify_post(title, body, item.get("post_kind_hint")),
        "comp": comp,
        "payment_model": payment_model(comp, title, body),
        "employment_type": employment_type(item.get("employment_type"), title, body),
        "remote_scope": scope,
        "remote_reason": scope_reason,
        "software": find_all(SOFTWARE, text),
        "specializations": find_all(SPECIALIZATIONS, text),
        "experience": experience(title, body),
        "languages": languages(title, body),
        "legit_confidence": legit_conf,
        "legit_flags": legit_flags,
    }
