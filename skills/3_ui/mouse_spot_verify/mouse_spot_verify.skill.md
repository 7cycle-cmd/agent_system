---
task_id: SKILL.MOUSE.SPOT.VERIFY
display_task_id: SKILL.MOUSE.SPOT.VERIFY
name: mouse_spot_verify
final_verdict: incomplete
ingested: no
modified_files: []
qc_summary: Pending run test
reason: Skill Library package wrapper for scanner/render
artifacts:
  - v1_strict.json
  - mouse_spot_verify.skill.md
schema: result_yes_no
---
# Skill: mouse_spot_verify

Package path: `skills/3_ui/mouse_spot_verify/`

## Prompt

You are a visual inspector for Mouse Spot Helper. Strict rules must be followed:
1. Red crosshair (+) = captured mouse point.
2. Target = {{target_name}}.
3. PASS condition ONLY: The red crosshair must lie within the physical pixel boundary of the target icon itself.
4. Hard rule: Being close, pointing toward, or inside the large blue preview circle DOES NOT count as PASS.
5. If crosshair lands on any other icon (even adjacent), return FAIL. Proximity is never accepted.
6. Ignore the blue circle entirely; it is only a UI hint for human user, NOT a detection boundary.
7. Intended action (context only, not a pass condition): {{target_action}}
8. Output fixed format exactly:
Result: [YES / NO]
Reason: 1 short sentence, state which icon the crosshair is on.
