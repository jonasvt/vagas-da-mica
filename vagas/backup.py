"""Backup e restauração do banco (cópia consistente via API de backup do SQLite)."""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path


def backup(db_path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"vagas-{datetime.now():%Y%m%d-%H%M%S}"
    dest, n = dest_dir / f"{stamp}.db", 1
    while dest.exists():  # nunca sobrescrever um backup existente
        dest, n = dest_dir / f"{stamp}-{n}.db", n + 1
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(dest))
    with dst:
        src.backup(dst)
    dst.execute("PRAGMA journal_mode=DELETE")  # backup em arquivo único, sem -wal/-shm
    src.close()
    dst.close()
    return dest


def verify(path: Path) -> tuple[bool, str]:
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        ok = c.execute("PRAGMA integrity_check").fetchone()[0]
        jobs = c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        c.close()
        return ok == "ok", f"integridade={ok}, vagas={jobs}"
    except sqlite3.Error as e:
        return False, str(e)


def restore(backup_path: Path, db_path: Path, dest_dir: Path) -> Path | None:
    """Restaura um backup. Antes, salva o banco atual como backup de segurança."""
    ok, info = verify(backup_path)
    if not ok:
        raise ValueError(f"backup inválido: {info}")
    safety = backup(db_path, dest_dir) if db_path.exists() else None
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            side.unlink()
    shutil.copy2(backup_path, db_path)
    return safety
