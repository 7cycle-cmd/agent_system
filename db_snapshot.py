"""DB SNAPSHOT — a consistent snapshot of the live DB (plan CHAT.PIPELINE.S9, S9-pre-a).

WHY THIS EXISTS
---------------
MEASURED 2026-09-30 (S9-pre pre-flight, round 3):
  * `agent.db` is 97 MB, live (a writer is active), and NOT in Git
    (`.gitignore:35 *.db`).
  * 66 ad-hoc backups (`agent.db.bak_*`) total 2.4 GB, all made with
    `shutil.copy2` — which is NOT consistent against a live writer.
  * `VACUUM INTO` on the live DB (opened read-only) produced a consistent,
    compacted snapshot in 0.24s (101.75 MB -> 88.19 MB, integrity ok).

So a snapshot is `VACUUM INTO`, never a raw file copy. It reads the live DB and
writes a NEW file; the live DB is untouched.

KEEP-LAST-N
-----------
88 MB per snapshot. Without a cap the dir grows like the 2.4 GB of ad-hoc
backups. `snapshot_db` keeps the newest `keep` snapshots and deletes the rest.

THE DIR IS ALREADY IGNORED
--------------------------
`agent.db.snapshots/*.db` matches `.gitignore:35 *.db`, so snapshots never enter
Git. No `.gitignore` change is needed.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_KEEP = 5
SNAPSHOT_DIR_NAME = "agent.db.snapshots"


def snapshot_dir(db_path: str | Path) -> Path:
    """The snapshot directory, next to the DB."""
    return Path(db_path).resolve().parent / SNAPSHOT_DIR_NAME


def snapshot_db(db_path: str | Path, name: str, *, keep: int = DEFAULT_KEEP) -> dict:
    """Write ONE consistent snapshot of `db_path` and keep the newest `keep`.

    `name` is the snapshot file stem (e.g. a run_id). Refuses a blank name.
    Returns `{ok, path, bytes, kept, deleted}`.
    """
    stem = str(name or "").strip()
    if not stem:
        return {"ok": False, "why": "snapshot name is required"}
    src = Path(db_path).resolve()
    if not src.exists():
        return {"ok": False, "why": "db not found: %s" % src}
    d = snapshot_dir(src)
    d.mkdir(parents=True, exist_ok=True)
    out = d / ("%s.db" % stem)
    # READ-ONLY on the live DB: VACUUM INTO reads it and writes a NEW file.
    conn = sqlite3.connect("file:%s?mode=ro" % src.as_posix(), uri=True)
    try:
        conn.execute("VACUUM INTO ?", (str(out),))
    finally:
        conn.close()
    deleted = _keep_last(d, max(keep, 0))
    return {"ok": True, "path": str(out), "bytes": out.stat().st_size,
            "kept": keep, "deleted": deleted}


def _keep_last(d: Path, keep: int) -> list[str]:
    """Delete all but the newest `keep` snapshots. Returns the deleted names."""
    snaps = sorted(d.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    deleted = []
    for old in snaps[keep:]:
        old.unlink()
        deleted.append(old.name)
    return deleted
