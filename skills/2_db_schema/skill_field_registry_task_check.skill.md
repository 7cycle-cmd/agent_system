---
task_id: "DB.FIELD.REGISTER"
name: "skill_field_register_task_check"
final_verdict: "PASS"
ingested: "yes"
modified_files: ["skill_field_register.py"]
qc_summary: "Scan all skill frontmatter and build schema register for hard validation"
reason: "自動提取所有skill嘅YAML欄位，存入field_register表，提供task payload硬校驗，唔依賴LLM"
artifacts: ["coords.db"]
schema: "field_register table"
---

# DB.FIELD.REGISTER
## 功能
1. 掃描 skills/**/*.skill.md，提取每一份skill嘅YAML frontmatter
2. 自動推斷欄位型態、標記必填，存入 field_register
3. 提供 validate_task_payload()，做硬校驗，攔截格式錯誤嘅task資料
4. 配合 watcher：每次skill變動時，自動重新掃描更新schema

## QC 檢查點
- [x] 執行 scan_all_skill_fields()，無exception（176 field rows upserted）
- [x] coords.db 入面 field_register 有記錄
- [x] validate_task_payload 可以成功偵測缺欄位、型態錯誤（pytest 14/14 passed）
