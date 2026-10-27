"""Coletores. Cada coletor recebe a configuração da fonte e devolve uma lista
de itens brutos (dicts) com campos comuns:

    source_item_id, title, description, source_url, apply_url, published_at,
    author, company, community, location_text, tags, comp (dict|None),
    employment_type, post_kind_hint, raw

Coletores não decidem relevância nem duplicatas — isso é do pipeline.
"""
from __future__ import annotations

from . import boards, brasil, email_alertas, reddit_rss

COLLECTORS = {
    "email_alertas": email_alertas.email_alertas,
    "github_issues": brasil.github_issues,
    "gupy_api": brasil.gupy,
    "reddit_rss": reddit_rss.collect,
    "himalayas_api": boards.himalayas,
    "remoteok_api": boards.remoteok,
    "remotive_api": boards.remotive,
    "jobicy_api": boards.jobicy,
    "workingnomads_api": boards.workingnomads,
    "freelancer_api": boards.freelancer,
    "wwr_rss": boards.wwr_rss,
}


def get_collector(method: str):
    return COLLECTORS.get(method)
