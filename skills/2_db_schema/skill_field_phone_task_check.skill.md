---
task_id: "DB.FIELD.PHONE"
name: "skill_field_phone_task_check"
final_verdict: "PASS"
ingested: "yes"
modified_files: ["field_validators.py", "skill_field_register.py", "test_field_phone.py"]
qc_summary: "Phone field hard validator + hardcoded dispatch in validate_task_payload; TDD green; persisted register_id + field_tdd_rule + task_ssot"
reason: "第一個 field 層 TDD 工作單：validate_phone 硬校驗 + validate_task_payload dispatch，唔依賴 LLM"
artifacts: ["qc_evidence/field_phone_tdd.json"]
schema: "field_tdd_rule (existing, no DDL change)"
---

# DB.FIELD.PHONE
## 功能
1. `field_validators.validate_phone(val)` — 硬校驗 phone 欄位：type 必須係 int（bool 自動 reject），guard range 8-15 位
2. `validate_task_payload()` 加 hardcoded dispatch：payload 有 `phone` 就 call `validate_phone`
3. v1 scope：dispatch 係 hardcode，唔聲稱 rule-driven（>1 field 先做 rule-driven）

## QC 檢查點
- [x] pytest test_field_phone.py 13/13 passed（含 4 個 integration case）
- [x] 無回歸：test_skill_field_register.py + test_api_task.py 18 passed
- [x] code_register 有真實 register_id（reg_field_validators_validate_phone_cf48f96bc7f3）
- [x] field_tdd_rule 有 phone row（id=16，rule 含 ref_tag=1.1F）
- [x] validate_task_payload reject "12345678"(field_register層)/true/9.5/"9123-4567"/"📱"/83(dispatch guard)；accept 12345678
- [x] final_verdict = PASS，qc_evidence/field_phone_tdd.json 已寫分層 rejection reason