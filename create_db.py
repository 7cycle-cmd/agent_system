import os
import sqlite3
import sys
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "agent.db")
SQL_PATH = os.path.join(BASE_DIR, "init_db.sql")


def create_database():
    # 读取同目录下的 init_db.sql
    with open(SQL_PATH, "r", encoding="utf-8") as file:
        sql = file.read()

    conn = sqlite3.connect(DB_PATH)
    try:
        # 开启外键约束（每个连接都要单独开启）
        conn.execute("PRAGMA foreign_keys = ON;")
        # executescript 会执行脚本中的多条语句
        conn.executescript(sql)
        conn.commit()

        # 验证外键是否生效
        fk = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
        print(f"OK: {DB_PATH}")
        print(f"foreign_keys = {fk}")

        # 打印已创建的表
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name;"
        ).fetchall()
        print("tables:", [t[0] for t in tables])
    finally:
        conn.close()


def migrate_existing():
    """Additive migration for live agent.db without DROP."""
    from db_schema import ensure_schema

    path = ensure_schema(DB_PATH)
    conn = sqlite3.connect(path)
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name;"
        ).fetchall()
        print(f"OK migrate: {path}")
        print("tables:", [t[0] for t in tables])
    finally:
        conn.close()
    return path


def spawn_pending_qc(*, with_mcp: bool = False, db_path: str = DB_PATH) -> int:
    """After migrate: detached schema_qc for pending/fail/running qc.verify_schema.

    Never raises into migrate failure — iron rule.
    Returns number of children attempted.
    """
    try:
        from schema_qc import list_pending_qc_tasks, spawn_detached_qc, connect
    except Exception as e:
        print(f"qc spawn import failed: {e}")
        return 0

    try:
        conn = connect(db_path)
    except Exception as e:
        print(f"qc spawn db open failed: {e}")
        return 0

    try:
        tasks = list_pending_qc_tasks(conn)
    finally:
        conn.close()

    if not tasks:
        print("qc spawn: no pending qc.verify_schema tasks")
        return 0

    py = sys.executable
    script = str(Path(BASE_DIR) / "schema_qc.py")
    n = 0
    for t in tasks:
        table = t.get("table")
        label = t.get("task_label")
        if not table:
            print(f"qc spawn skip task_id={t.get('id')} label={label}: no payload.table")
            continue
        argv = [
            py,
            script,
            "--table",
            str(table),
            "--write-ssot",
            "--db",
            str(db_path),
        ]
        if label:
            argv.extend(["--task-label", str(label)])
        browser = t.get("browser_url")
        if browser:
            argv.extend(["--browser-url", str(browser)])
        if with_mcp:
            argv.append("--mcp-shot")
        # expected columns from payload if present
        exp = (t.get("payload") or {}).get("expected_columns")
        if isinstance(exp, list) and exp:
            argv.extend(["--expected", ",".join(str(x) for x in exp)])
        log_name = f"qc_spawn_{label or t.get('id')}_{table}.log"
        try:
            spawn_detached_qc(argv, log_name=log_name)
            n += 1
        except Exception as e:
            print(f"qc spawn error task={label}: {e}")
    print(f"qc spawn done: attempted={n} discovered={len(tasks)} mcp={with_mcp}")
    return n


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        create_database()
        return 0

    if argv[0] not in ("--migrate", "migrate"):
        # unknown → greenfield create (legacy)
        create_database()
        return 0

    # parse flags after migrate
    rest = argv[1:]
    do_qc = False
    do_qc_mcp = False
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--qc",):
            do_qc = True
        elif a in ("--qc-mcp",):
            do_qc = True
            do_qc_mcp = True
        elif a in ("-h", "--help"):
            print(
                "Usage:\n"
                "  python create_db.py                 # greenfield init_db.sql\n"
                "  python create_db.py --migrate       # additive ensure_schema\n"
                "  python create_db.py --migrate --qc  # + spawn pending schema_qc\n"
                "  python create_db.py --migrate --qc-mcp  # + MCP screenshot\n"
            )
            return 0
        else:
            print(f"WARN unknown migrate flag: {a}")
        i += 1

    migrate_existing()
    # Iron rule: QC spawn after successful migrate; failures do not change exit
    if do_qc:
        try:
            spawn_pending_qc(with_mcp=do_qc_mcp, db_path=DB_PATH)
        except Exception as e:
            print(f"qc spawn outer failed (migrate still OK): {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
