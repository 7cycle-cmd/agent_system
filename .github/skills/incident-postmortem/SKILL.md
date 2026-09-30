---
name: incident-postmortem
description: "Use when: an incident trigger has fired and a postmortem is required — a repeat of the same error class in one session, 3+ failed fixes on one problem, an architecture change being required, a recorded PASS later shown wrong, evidence/provenance destroyed by a gate, or the human being asked after 3+ failures. Enforces triggers DEFINED IN ADVANCE (no 'this one is minor' escape) and the review gate: an unreviewed postmortem might as well never have existed. Also use when you catch yourself deciding after the fact whether something 'counts' as an incident."
---

# Incident Postmortem (triggers defined in advance)

**Goal:** 觸發條件**事前**定義；**未經覆核嘅事後報告等於從來冇存在過。**

Full skill: `skills/1_core/incident_postmortem/incident_postmortem.skill.md`
Executable: `incident_postmortem.py`（觸發清單 + 覆核閘）· `incident_detector.py`
Proof: `_proof_incident_waiting.py`（27 個檢查）

Source: Google SRE Book ch.15 (postmortem culture) — the transferable part is the
**trigger list** and the **review gate**; the rest is culture and cannot be
installed.

## When to use

- 任何一條觸發條件 fire（見下表）
- 你正在**事後**判斷「呢個算唔算事故」 ← 呢個判斷空間本身就是要移除嘅問題
- 你寫低咗教訓，但**冇任何嘢**話呢個必須寫
- 一個 PASS 後來證明係錯

## 觸發清單（事前定義，六條）

| key | 觸發條件 |
|---|---|
| `repeat_same_class` | 同一 session 內同類錯誤重複（≥2） |
| `fix_attempts_ge_3` | 同一問題修復失敗 ≥3 次 |
| `architecture_change_required` | 有效修復需要**架構**改變，唔係參數改變 |
| `false_pass_recorded` | 記錄咗 PASS 但後來證明錯 |
| `evidence_destroyed` | 有閘丟棄或覆寫咗真實 provenance |
| `human_asked_after_3` | 失敗 ≥3 次之後問人類 |

**冇「輕微事故」逃生門。** 任何一條 fire → 需要事後報告。

## 閘（Gate）

```python
from incident_postmortem import assert_reviewed, may_close

assert_reviewed(postmortem, incident)   # 唔合規 → raise PostmortemRequired
may_close(incident, postmortem)         # → bool，唔 raise
```

## 為何要有呢個 skill（實測）

實測缺口：**教訓係想起才寫。** 冇任何嘢話「呢個算係事故」，
所以「算唔算事故」變成事後判斷 — 而事後判斷永遠可以選擇排除自己。

## Note on this file

This is a **pointer**, not a second copy. The canonical skill lives at
`skills/1_core/incident_postmortem/` so it is visible to the Skill Library
scanner, has a contract and can accumulate a streak. Editing this file does not
change the enforced rule.
