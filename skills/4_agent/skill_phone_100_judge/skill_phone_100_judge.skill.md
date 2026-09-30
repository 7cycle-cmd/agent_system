---
task_id: "11.1-T-1"
name: "skill_phone_100_judge"
catalog_id: 11
subcatalog_id: 1
prompt_setting_id: 449
final_verdict: "PASS"
ingested: "yes"
modified_files: []
qc_summary: "Judge instruction for phone 100 連勝 proof — YAML field-type judge (YES/NO) + TDD verify (1/2/3)"
reason: "LLM judge instruction for phone field type-judgment 100 連勝 harness"
artifacts: []
schema: ""
---

# SKILL.PHONE.100.JUDGE

## 功能
提供 LLM judge instruction：判斷 raw value 經 `yaml.safe_load` 後係咪 Python `int` 型態，答 YES/NO。

## 兩種問題模式

### 1. TDD verify 模式（簡單問題 + 1/2/3 數字答案 + 7B 免費）
**用途**：驗 harness 邏輯（streak/round recording/parse），唔係驗 LLM 能力。
**唔使 DeepSeek** — 用本地 7B 就得，慳錢。

問題格式（`tdd_question(raw)`）：
```
Is the value "{raw}" an integer (int)?
Reply with exactly one number:
1 = YES (it is an int)
2 = NO (it is not an int)
3 = UNSURE
```

答案 parse（`parse_tdd_answer`）：`1→YES, 2→NO, 3→UNSURE`（UNSURE 當 fail）。
**點解 7B 100% 答到**：問題係簡單 int 分類，答案係單一數字，唔使 YAML 語法知識。

### 2. Proof 模式（難問題 + YES/NO + DeepSeek）
**用途**：驗 LLM 能力 → sign ACTIVE。先 7B，fail 先 handoff DeepSeek。

## Judge instruction v2 (full YAML type spec) — proof 模式用
You are a YAML field-type judge. Given a raw YAML value, answer with exactly "YES" or "NO".
Answer YES if and only if yaml.safe_load(value) has Python type int (type is int).
YAML int forms (all YES): decimal 123, -5, +7; legacy leading-zero 0123; hex 0x1F;
binary 0b101; underscores allowed 1_234; leading + allowed +85291234567.
NOT int (all NO): bool true/false/True; floats 9.5 / 1e3 / .5; null / ~ / empty;
0o17 (PyYAML parses as str); any quoted value ("123"); symbols "9123-4567";
emoji; text with spaces "+852 9123 4567".
Reply with one word only.

## QC 檢查點
- [x] Judge instruction v2 完整 YAML type 語義（principled spec，唔係 patch）
- [x] 覆蓋：decimal/octal(legacy)/hex/binary/underscore/leading+/bool/float/null/quoted/symbol/emoji/space
- [x] 修正：0o17 喺 PyYAML 係 str（唔係 int），已移去 non-int 類
- [x] TDD verify 模式：簡單問題 + 1/2/3 數字答案 + 7B（免費，唔使 DeepSeek）
- [x] Proof 模式：難問題 + YES/NO + DeepSeek（驗 LLM 能力）