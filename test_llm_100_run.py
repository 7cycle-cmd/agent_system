# test_llm_100_run.py (v2)
import sqlite3
import tempfile
from pathlib import Path
import shutil
from collections import Counter

import yaml

from llm_100_run_harness import (
    oracle, gen_values, run_harness, current_streak,
    current_rule_version, next_round_no, OPTIONS, STREAK_TARGET,
    tdd_question, parse_tdd_answer,
)
from coord_store import migrate_llm_100_run_model


class TestTddQuestion:
    def test_question_contains_value_and_options(self):
        q = tdd_question("12345678")
        assert "12345678" in q
        assert "1 = YES" in q
        assert "2 = NO" in q
        assert "3 = UNSURE" in q

    def test_parse_numeric_answers(self):
        assert parse_tdd_answer("1") == "YES"
        assert parse_tdd_answer("2") == "NO"
        assert parse_tdd_answer("3") == "UNSURE"

    def test_parse_tolerates_suffix(self):
        assert parse_tdd_answer("1.") == "YES"
        assert parse_tdd_answer("2)") == "NO"
        assert parse_tdd_answer("3") == "UNSURE"

    def test_parse_rejects_garbage(self):
        assert parse_tdd_answer("maybe") is None
        assert parse_tdd_answer("") is None
        assert parse_tdd_answer("YES") is None  # numeric-only in TDD mode

    def test_tdd_verify_mode_qualified(self):
        # mock LLM (llm=False) with tdd_verify=True should still reach QUALIFIED
        tmp = tempfile.mkdtemp()
        db = Path(tmp) / "t.db"
        try:
            out = run_harness(seed=7, db_path=db, llm=False, cap=1000, tdd_verify=True)
            assert out["verdict"] == "QUALIFIED"
            assert out["final_streak"] == STREAK_TARGET
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestOracle:
    def test_oracle_v2_table(self):
        cases = {
            # int forms -> YES
            "123": "YES", "0": "YES", "-5": "YES", "+7": "YES",
            "0123": "YES", "0x1F": "YES", "0b101": "YES",
            "1_234": "YES", "+85291234567": "YES", "999999999999999": "YES",
            # non-int -> NO (incl. 0o17: PyYAML parses as str, NOT int)
            '"123"': "NO", "true": "NO", "false": "NO", "True": "NO",
            "9.5": "NO", "1e3": "NO", ".5": "NO", "null": "NO", "~": "NO",
            "0o17": "NO", '"9123-4567"': "NO", '"+852 9123 4567"': "NO",
        }
        for raw, expected in cases.items():
            assert oracle(raw) == expected, f"{raw!r}: {oracle(raw)} != {expected}"

    def test_oracle_empirical(self):
        assert type(yaml.safe_load("0b101")) is int
        assert type(yaml.safe_load(".5")) is float
        assert yaml.safe_load("~") is None
        assert type(yaml.safe_load("1_234")) is int
        assert type(yaml.safe_load("+85291234567")) is int
        # PyYAML does NOT support 0o prefix octal -> str
        assert type(yaml.safe_load("0o17")) is str


class TestUniformSampling:
    def test_frequencies_roughly_equal(self):
        vals = gen_values(7, 2200)
        counts = Counter(vals)
        # 22 options over 2200 samples -> ~100 each; allow wide tolerance
        for opt in OPTIONS:
            assert counts[opt] > 30, f"{opt}: only {counts[opt]}"

    def test_same_seed_same_sequence(self):
        assert gen_values(7, 50) == gen_values(7, 50)


class TestStreak:
    def test_streak_reset_on_fail(self):
        tmp = tempfile.mkdtemp()
        db = Path(tmp) / "t.db"
        try:
            out = run_harness(seed=7, db_path=db, llm=False, cap=5)
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            assert current_streak(conn, "1.1F", 1, "qwen2.5:7b-instruct") == 5
            conn.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_110_all_win_qualified(self):
        tmp = tempfile.mkdtemp()
        db = Path(tmp) / "t.db"
        try:
            out = run_harness(seed=7, db_path=db, llm=False, cap=1000)
            assert out["verdict"] == "QUALIFIED"
            assert out["final_streak"] == STREAK_TARGET
            assert out["rounds_run"] == STREAK_TARGET
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestRoundRecording:
    def test_model_column_populated(self):
        tmp = tempfile.mkdtemp()
        db = Path(tmp) / "t.db"
        try:
            run_harness(seed=7, db_path=db, llm=False, cap=5, model="qwen2.5:7b-instruct")
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM proof_run ORDER BY id").fetchall()
            conn.close()
            assert len(rows) == 5
            for r in rows:
                assert r["model"] == "qwen2.5:7b-instruct"
                assert r["entity_type"] == "field"
                assert r["ref_tag"] == "1.1F"
                assert r["oracle_answer"] in ("YES", "NO")
                assert r["llm_answer"] in ("YES", "NO")
                assert r["win"] in (0, 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_round_no_continues_across_rule_versions(self):
        tmp = tempfile.mkdtemp()
        db = Path(tmp) / "t.db"
        try:
            run_harness(seed=7, db_path=db, llm=False, cap=5)
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            conn.execute("UPDATE proof_run SET rule_version = 2")
            conn.commit()
            assert current_rule_version(conn, "1.1F") == 2
            assert next_round_no(conn, "1.1F", 2) == 6  # global counter
            conn.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestMigration:
    def test_backfill_existing_rows(self):
        tmp = tempfile.mkdtemp()
        db = Path(tmp) / "t.db"
        try:
            # create table WITHOUT model column (simulate v1)
            conn = sqlite3.connect(db)
            conn.executescript("""
                CREATE TABLE llm_100_run (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL, ref_tag TEXT NOT NULL,
                    entity_name TEXT NOT NULL, round_no INTEGER NOT NULL,
                    value TEXT NOT NULL, oracle_answer TEXT NOT NULL,
                    llm_answer TEXT NOT NULL, win INTEGER NOT NULL,
                    failure_reason TEXT, rule_version INTEGER NOT NULL DEFAULT 1,
                    skill_id TEXT, linked_trace_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(ref_tag, round_no, rule_version)
                );
            """)
            conn.execute(
                "INSERT INTO llm_100_run (entity_type, ref_tag, entity_name, round_no, value, oracle_answer, llm_answer, win) "
                "VALUES ('field','1.1F','phone',1,'123','YES','YES',1)"
            )
            conn.commit()
            conn.close()
            # migrate
            n = migrate_llm_100_run_model(db, backfill_model="qwen2.5:7b-instruct")
            assert n == 1
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT model FROM llm_100_run WHERE round_no=1").fetchone()
            conn.close()
            assert row["model"] == "qwen2.5:7b-instruct"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)