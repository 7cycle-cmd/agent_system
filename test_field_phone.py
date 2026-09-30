# test_field_phone.py
import sqlite3
import tempfile
from pathlib import Path
import shutil

from field_validators import validate_phone
from skill_field_registry import validate_task_payload


class TestValidatePhone:
    def test_accept_int(self):
        assert validate_phone(12345678) is None

    def test_reject_null(self):
        assert validate_phone(None) is not None

    def test_reject_empty(self):
        assert validate_phone("") is not None

    def test_reject_text(self):
        assert validate_phone("12345678") is not None

    def test_reject_bool(self):
        assert validate_phone(True) is not None

    def test_reject_symbol(self):
        assert validate_phone("9123-4567") is not None

    def test_reject_emoji(self):
        assert validate_phone("📱") is not None

    def test_reject_float(self):
        assert validate_phone(9.5) is not None

    def test_reject_guard(self):
        assert validate_phone(83) == "value out of range (8-15 digits)"


class TestIntegrationDispatch:
    """Prove the dispatch wiring fires inside validate_task_payload."""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.tmp_dir) / "test_coords.db"
        import skill_field_registry
        skill_field_registry.DB_PATH = str(self.db_path)
        skill_field_registry.SKILL_ROOT = Path(self.tmp_dir)
        from skill_field_registry import init_table
        init_table()
        # Register phone as int in field_registry so the field_registry layer
        # type-checks it (str -> mismatch) before dispatch.
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO field_registry (task_id, field_name, field_type, required) "
            "VALUES ('T1', 'phone', 'int', 1)"
        )
        conn.commit()
        conn.close()

    def teardown_method(self):
        shutil.rmtree(self.tmp_dir)

    def test_accept_valid_int(self):
        ok, errors = validate_task_payload("T1", {"phone": 12345678})
        assert ok is True, errors
        assert errors == []

    def test_reject_text_field_registry_layer(self):
        ok, errors = validate_task_payload("T1", {"phone": "12345678"})
        assert ok is False
        assert any("type mismatch" in e for e in errors)

    def test_reject_bool(self):
        ok, errors = validate_task_payload("T1", {"phone": True})
        assert ok is False
        assert any("type mismatch" in e for e in errors)

    def test_reject_guard_dispatch_fires(self):
        # 83 is int (passes field_registry type check) but out of guard range.
        # This is the proof the dispatch wiring fires.
        ok, errors = validate_task_payload("T1", {"phone": 83})
        assert ok is False
        assert any("value out of range" in e for e in errors)