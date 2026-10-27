"""Carrega configuração (config/*.json) e segredos (.env, fora do controle de versão)."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = Path(os.environ.get("VAGAS_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("VAGAS_DB", DATA_DIR / "vagas.db"))


def load_env(path: Path = ROOT / ".env") -> None:
    """Lê KEY=VALUE do .env sem sobrescrever variáveis já definidas."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_json(name: str) -> dict:
    return json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))


def load_sources() -> list[dict]:
    return load_json("sources.json")["sources"]


def load_profile() -> dict:
    return load_json("profile.json")
