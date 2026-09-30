# test_skill_field_registry.py
import sqlite3
import tempfile
from pathlib import Path
import shutil
from skill_field_registry import extract_frontmatter, init_table, scan_all_skill_fields, validate_task_payload

class TestSkillFieldRegister:
    def setup_method(self):
        # 建立臨時資料夾 + 臨時DB，唔會污染真實coords.db
        self.tmp_dir = tempfile.mkdtemp()
        self.skill_path = Path(self.tmp_dir) / "test.skill.md"
        self.db_path = Path(self.tmp_dir) / "test_coords.db"

        # 覆寫模組入面嘅DB_PATH同SKILL_ROOT
        import skill_field_registry
        skill_field_registry.DB_PATH = str(self.db_path)
        skill_field_registry.SKILL_ROOT = Path(self.tmp_dir)

        init_table()

    def teardown_method(self):
        shutil.rmtree(self.tmp_dir)

    def test_extract_frontmatter_valid(self):
        """正常frontmatter提取"""
        md = """---
task_id: "T01"
name: "test task"
final_verdict: "PASS"
modified_files: []
---
# content
"""
        fm = extract_frontmatter(md)
        assert fm is not None
        assert fm["task_id"] == "T01"
        assert fm["modified_files"] == []

    def test_extract_frontmatter_no_start_dash(self):
        """開頭冇 ---，返回None"""
        md = """task_id: "T01"
name: "test"
---
"""
        fm = extract_frontmatter(md)
        assert fm is None

    def test_extract_frontmatter_no_end_dash(self):
        """冇結尾 ---，返回None"""
        md = """---
task_id: "T01"
name: "test"
"""
        fm = extract_frontmatter(md)
        assert fm is None

    def test_scan_and_db_write(self):
        """掃描skill，寫入field_registry表"""
        content = """---
task_id: "T01"
name: "test task"
final_verdict: "INCOMPLETE"
ingested: "yes"
modified_files: []
qc_summary: "test summary"
---
# Test Skill
"""
        self.skill_path.write_text(content, encoding="utf-8")
        scan_all_skill_fields()

        conn = sqlite3.connect(self.db_path)
        rows = conn.execute("SELECT field_name, field_type FROM field_registry WHERE task_id=?", ("T01",)).fetchall()
        conn.close()
        row_dict = {r[0]: r[1] for r in rows}
        assert "task_id" in row_dict
        assert row_dict["modified_files"] == "list"

    def test_validate_payload_correct(self):
        """完全正確payload → 校驗通過"""
        content = """---
task_id: "T01"
name: "test task"
final_verdict: "INCOMPLETE"
ingested: "yes"
modified_files: []
qc_summary: "test summary"
reason: "test reason"
artifacts: ["test.md"]
schema: "test table"
---
"""
        self.skill_path.write_text(content, encoding="utf-8")
        scan_all_skill_fields()

        good_payload = {
            "task_id": "T01",
            "name": "test",
            "final_verdict": "INCOMPLETE",
            "ingested": "yes",
            "modified_files": [],
            "qc_summary": "ok",
            "reason": "ok",
            "artifacts": ["a.md"],
            "schema": "table"
        }
        ok, errors = validate_task_payload("T01", good_payload)
        assert ok is True
        assert len(errors) == 0

    def test_validate_payload_missing_required(self):
        """缺少必填欄位 → 報錯"""
        content = """---
task_id: "T01"
name: "test task"
final_verdict: "INCOMPLETE"
---
"""
        self.skill_path.write_text(content, encoding="utf-8")
        scan_all_skill_fields()
        payload = {"task_id": "T01"}
        ok, errors = validate_task_payload("T01", payload)
        assert ok is False
        assert "Missing required field: name" in errors

    def test_validate_payload_type_mismatch(self):
        """型態唔匹配：list傳入string → 報錯"""
        content = """---
task_id: "T01"
name: "test task"
modified_files: []
---
"""
        self.skill_path.write_text(content, encoding="utf-8")
        scan_all_skill_fields()
        bad_payload = {
            "task_id": "T01",
            "name": "test",
            "modified_files": "[]"
        }
        ok, errors = validate_task_payload("T01", bad_payload)
        assert ok is False
        assert "type mismatch" in errors[0]

    def test_yaml_boolean_trap(self):
        """測試陷阱：ingested: yes 唔加引號會變bool"""
        # 唔加引號，PyYAML會解析成 True
        md_raw = """---
task_id: "T02"
ingested: yes
---
"""
        fm = extract_frontmatter(md_raw)
        assert fm["ingested"] is True

        # 加雙引號就會保留為字串
        md_quoted = """---
task_id: "T03"
ingested: "yes"
---
"""
        fm2 = extract_frontmatter(md_quoted)
        assert fm2["ingested"] == "yes"

    def test_extract_frontmatter_empty_file(self):
        """邊界案例：空檔案，frontmatter 返回 None"""
        md = ""
        fm = extract_frontmatter(md)
        assert fm is None

        md_blank = "\n\n   \n"
        fm2 = extract_frontmatter(md_blank)
        assert fm2 is None

    def test_extract_frontmatter_broken_yaml(self):
        """邊界案例：YAML語法損壞，解析失敗返回None"""
        broken_md = """---
task_id: "T_BROKEN
name: test, missing quote
final_verdict: INCOMPLETE
---
"""
        fm = extract_frontmatter(broken_md)
        assert fm is None

    def test_scan_duplicate_task_id_upsert(self):
        """邊界案例：同一task_id重複掃描，測試ON CONFLICT更新，唔會重複新增多行"""
        # 第一版 skill
        content_v1 = """---
task_id: "T.DUP"
name: "old name"
final_verdict: "INCOMPLETE"
---
"""
        self.skill_path.write_text(content_v1, encoding="utf-8")
        scan_all_skill_fields()

        conn = sqlite3.connect(self.db_path)
        count_v1 = conn.execute("SELECT COUNT(*) FROM field_registry WHERE task_id = ?", ("T.DUP",)).fetchone()[0]

        # 第二版，修改name，覆寫同一個skill檔
        content_v2 = """---
task_id: "T.DUP"
name: "new updated name"
final_verdict: "PASS"
---
"""
        self.skill_path.write_text(content_v2, encoding="utf-8")
        scan_all_skill_fields()

        count_v2 = conn.execute("SELECT COUNT(*) FROM field_registry WHERE task_id = ?", ("T.DUP",)).fetchone()[0]
        conn.close()

        # 行數唔會增加，只會更新原有記錄（upsert，唔插入新行）
        assert count_v1 == count_v2

    def test_task_id_with_special_chars(self):
        """task_id包含特殊符號：. / - _，可以正常存入DB同校驗"""
        special_task_id = "DB.FIELD-REG/ISTER_01"
        content = f"""---
task_id: "{special_task_id}"
name: "special id test"
final_verdict: "INCOMPLETE"
modified_files: []
---
"""
        self.skill_path.write_text(content, encoding="utf-8")
        scan_all_skill_fields()

        good_payload = {
            "task_id": special_task_id,
            "name": "test",
            "final_verdict": "INCOMPLETE",
            "modified_files": []
        }
        ok, errors = validate_task_payload(special_task_id, good_payload)
        assert ok is True
        assert len(errors) == 0

    def test_validate_payload_list_inner_type_no_deep_check(self):
        """陣列外層型態檢查通過，但唔做內部元素深層校驗（當前模組設計限制）"""
        content = """---
task_id: "T.LIST.INNER"
name: "list inner test"
artifacts: ["a.md", "b.md"]
---
"""
        self.skill_path.write_text(content, encoding="utf-8")
        scan_all_skill_fields()

        # 外層係list，就算入面混合型態，而家嘅校驗唔會攔截
        payload = {
            "task_id": "T.LIST.INNER",
            "name": "test",
            "artifacts": ["ok", 123, None]
        }
        ok, errors = validate_task_payload("T.LIST.INNER", payload)
        assert ok is True
        assert len(errors) == 0

    def test_null_value_handling(self):
        """測試null值：frontmatter null，payload傳入None"""
        content = """---
task_id: "T.NULL"
name: "null test"
description: null
---
"""
        self.skill_path.write_text(content, encoding="utf-8")
        scan_all_skill_fields()

        # payload傳入None
        payload = {
            "task_id": "T.NULL",
            "name": "test",
            "description": None
        }
        ok, errors = validate_task_payload("T.NULL", payload)
        assert ok is True
        assert len(errors) == 0

        # 必填欄位唔可以傳None
        payload_bad = {
            "task_id": "T.NULL",
            "name": None
        }
        ok_bad, errors_bad = validate_task_payload("T.NULL", payload_bad)
        assert ok_bad is False
        # name 傳 None 會觸發 type mismatch（錯誤順序唔固定，用 any 檢查）
        assert any("type mismatch" in e for e in errors_bad)
