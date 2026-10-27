"""Pontuação de relevância transparente.

Cada ponto ganho ou perdido vem com um motivo legível. Nada de ranking de IA
sem explicação. Os pesos ficam em config/profile.json.
"""
from __future__ import annotations

import re

from .extract import search_affirmative


def _usd(value: float, currency: str | None, rates: dict) -> float | None:
    if value is None:
        return None
    cur = (currency or "").upper()
    if cur in ("USD", "$"):
        return value
    rate = rates.get(cur)
    return value * rate if rate else None


def score_job(job: dict, ext: dict, profile: dict) -> tuple[int, list[str]]:
    title = job.get("title") or ""
    body = job.get("description") or ""
    reasons: list[str] = []
    total = 0

    def add(points: int, why: str):
        nonlocal total
        total += points
        reasons.append(f"{'+' if points >= 0 else ''}{points} {why}")

    # 1) Tipo de post
    kind = ext["post_kind"]
    if kind == "hiring":
        add(10, "post de contratação")
    elif kind == "for_hire":
        add(-60, "post de editor oferecendo serviço (não é vaga)")
    else:
        add(0, "tipo de post incerto — confira")

    # 2) Especialidades-alvo (título vale mais que descrição)
    spec_pts = 0
    for t in profile["targets"]:
        rx = re.compile(t["pattern"], re.I)
        if rx.search(title):
            p = t["weight"]
            where = "título"
        elif rx.search(body):
            p = t["weight"] // 2
            where = "descrição"
        else:
            continue
        if spec_pts + p > profile.get("target_cap", 50):
            p = max(0, profile.get("target_cap", 50) - spec_pts)
        if p:
            spec_pts += p
            add(p, f"{t['label']} ({where})")
    if spec_pts == 0:
        add(-25, "nenhuma especialidade-alvo encontrada")
    if profile.get("editing_title") and not re.search(profile["editing_title"], title, re.I):
        add(-15, "título não fala de edição (pode ser outra função)")

    # 3) Palavras excluídas
    for ex in profile.get("excluded", []):
        if re.search(ex["pattern"], f"{title}\n{body}", re.I):
            add(-abs(ex.get("weight", 40)), f"palavra excluída: {ex['label']}")

    # 4) Remoto / localização
    scope = ext["remote_scope"]
    if scope == "worldwide":
        add(15, "aceita qualquer país")
    elif scope == "restricted":
        add(-15, f"restrição de país/fuso ({ext.get('remote_reason')})")
    elif scope == "onsite":
        add(-40, "presencial ou híbrido")

    # 5) Remuneração (não penaliza valor não informado)
    comp = ext.get("comp")
    if comp:
        rates = profile.get("approx_usd_rates", {})
        unit = comp.get("unit")
        if unit == "video" and "short_form" in ext["specializations"] and "long_form" not in ext["specializations"]:
            unit = "short"
        minimum = profile.get("min_comp_usd", {}).get(unit)
        val = _usd(comp.get("max") or comp.get("min"), comp.get("currency"), rates)
        if minimum is None or val is None:
            add(3, "valor informado")
        elif val >= minimum * profile.get("good_pay_multiplier", 1.5):
            add(15, f"paga bem (~US${val:,.0f} por {unit}; mínimo {minimum})")
        elif val >= minimum:
            add(8, f"dentro do mínimo (~US${val:,.0f} por {unit})")
        else:
            add(-15, f"abaixo do mínimo (~US${val:,.0f} por {unit}; mínimo {minimum})")
    elif ext["payment_model"] == "revenue_share":
        add(-10, "só divisão de receita")
    else:
        add(0, "valor não informado (sem penalidade)")

    # 6) Software
    mine = set(profile.get("my_software", []))
    req = set(ext.get("software") or [])
    if req & mine:
        add(5, f"usa {', '.join(sorted(req & mine))}")

    # 7) Experiência / iniciante
    if ext.get("experience") == "Iniciante aceito":
        add(3, "aceita iniciante")

    # 8) Legitimidade
    for flag in ext.get("legit_flags") or []:
        add(-10, f"alerta: {flag}")

    return max(0, min(100, total)), reasons
