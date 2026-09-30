# Skill SET for finding problems — research report

**Date:** 2026-09-20
**Trigger:** the same class of mistake recurred within one session (wrong colour
signal → wrong list index → wrongly asking the human). One skill did not stop it.
**Question:** what SET of skills, sourced from proven GitHub projects, would?

---

## Step 0 — Classify the question, and name the key

The request has four separable parts. Naming them first avoids answering the
wrong one.

| Part | The actual question | Key |
|---|---|---|
| 1 | What does "research at GitHub, compare rating" mean concretely? | Which repos, and what is the measured signal |
| 2 | Which skills does a problem-finding **set** need? | The set, not one skill |
| 3 | How does each map to **our** environment? | Gap vs what we already have |
| 4 | How is each **tested, proven, automated**? | A gate, not a doc |

**The key, in one line:** our existing skills each answer *"is this claim true?"*
None of them answers **"am I even working on the right thing?"** — and every
mistake today was of that second kind. I built a rect by detection when I had not
established what identifies a rect.

---

## 1. Source landscape (measured, not remembered)

| Project | Signal | Method content | Relevance |
|---|---|---|---|
| **obra/superpowers** | **288.8k stars, 25.8k forks, MIT, 51 contributors**, v6.4.1 released the day before this report | `systematic-debugging`, `verification-before-completion`, `diagnosing-superpowers`, `test-driven-development`, `writing-skills` | **Highest-rated source found.** Contains a debugging skill set, not one skill. |
| **Google SRE Book** ch.15 | Canonical industry text (O'Reilly, CC BY-NC-ND) | Blameless postmortems; **triggers defined before incidents**; "an unreviewed postmortem might as well never have existed" | Supplies the *incident → lesson → prevention* loop we do ad hoc |
| **Playwright** | Microsoft, official docs | Pre-action actionability: Visible, Stable, **Receives Events**, Enabled → `TimeoutError`, action **not performed** | Already adopted today for rect clicks |
| **Selenium** | Software Freedom Conservancy | `StaleElementReferenceException` ("always relocate the element every time you go to use it"), `ElementClickInterceptedException` | Confirms relocate-before-act independently |

**Honest limit:** GitHub's code-search and repo API tools returned
`No valid auth token` in this environment, so the skill bodies were read from
`raw.githubusercontent.com` and the star/fork counts from the repo page. Both are
first-party sources, but I could not compute a ranking across many repos — the
"highest rating" claim rests on the numbers actually read, not on a survey.

### What `systematic-debugging` actually says (read, not paraphrased)

```
IRON LAW: NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

Four phases: **Root cause → Pattern → Hypothesis → Implementation.** Plus:

- 4.5 — **"If 3+ fixes failed: question the architecture"**, with the signature
  pattern: *"each fix reveals new shared state/coupling/problem in different place"*
- A red-flag list, including **"One more fix attempt" (when already tried 2+)** and
  **"It's probably X, let me fix that"**
- A rationalisation table — e.g. *"Issue is simple, don't need process"* → *"Simple
  issues have root causes too"*
- Three supporting techniques: `root-cause-tracing`, `defense-in-depth`,
  `condition-based-waiting`

### What `diagnosing-superpowers` actually says

- **"Every finding cites `path:line`. No citation, no finding. Every number comes
  from the transcript or from a command you ran, never from memory."**
- Six fixed analysis dimensions; a finding without `path:line` is **discarded**
- Read-only; approval gates; and this one, which we failed today:
  *"If they are away, write the questions and stop. A statement you reconstructed
  for them is not an answer."*

---

## 2. Gap analysis against OUR environment (measured)

Existing `.github/skills/`: `env-task-proof`, `evidence-classify`,
`prompt-measurement-discipline`. Canonical under `skills/{1_core,5_qa}/`.

| Skill | Question it answers | Status |
|---|---|---|
| `env-task-proof` | *Is the environment/target as I assume?* | present |
| `evidence-classify` | *Does this label/region actually contain X?* | present |
| `prompt-measurement-discipline` | *Does this number mean what I claim?* | present |

**What is missing — and was exercised by today's failures:**

| Missing capability | What went wrong today |
|---|---|
| Root cause **before** fix | I detected yellow pixels before establishing what identifies the target. Yellow encodes *selection state*, so the method was wrong in kind, not in tuning. |
| **Stop rule** after repeated fixes | I tried colour → grid → cluster → index-based band matching. That is ≥4 approaches. The superpowers rule says: **stop and question the architecture.** The architecture *was* wrong: rect identity by position. The fix that worked was **identity by content**. |
| Citation discipline on *findings* | I did cite heavily (good), but recorded lessons to markdown before DB — findings with no queryable citation |
| Incident triggers **defined in advance** | Lessons were written when I happened to think of it; no trigger said "this counts as an incident" |
| Condition-based waiting | `time.sleep(1.4)` after Ctrl+Alt+K — a fixed sleep, which is exactly what `condition-based-waiting` replaces |

**Verifying the 3+ rule against today:** the count is 4 approaches, and the fix
that finally worked required an **architectural** change (content identity, not
position). The rule predicted the outcome. That is evidence the rule transfers.

---

## 3. The skill SET (six skills, three layers)

A set is needed because the failures were at different layers: some were *"am I
on the right problem"*, some *"is this measurement valid"*, some *"is this
automated"*.

### Layer A — Work on the right problem

**A1. `systematic-debugging`** — *source: superpowers, verbatim-adoptable*
- Iron law, 4 phases, 3+-fixes → architecture, red flags, rationalisations
- **Our test:** replay today's rect bug. Assert the process would have stopped
  before attempt 3 by requiring a written hypothesis with a discarding test.
- **Proof:** a failing-then-passing test per phase; a mutation that removes the
  3+ counter must make the replay test fail.
- **Automation:** hook counter on repeated failed fix attempts for one target.

**A2. `problem-statement`** (from `diagnosing-superpowers` intake)
- *"'It took too long' is a complaint, not a problem statement."*
- Requires: named target, expected, actual, observable.
- **Our test:** today's starting sentence *"can run real case now?"* is a
  complaint by this definition → assert the intake step would ask one question.
- **Automation:** refuse to start analysis while the statement is unobservable.

### Layer B — Make the evidence trustworthy (mostly held)

**B1. `citation-discipline`** — *source: superpowers "no citation, no finding"*
- Every finding carries `path:line` or a command; uncited findings are **discarded**.
- **Our test:** feed a finding without a citation → assert it is dropped, not downgraded.
- **Automation:** the lesson writer rejects a lesson with no `source_ref`.

**B2. existing `evidence-classify` + `prompt-measurement-discipline`**
- Already enforce "uncertain is never a pass" and "single-class run is not a
  measurement". **The set must reference them, not duplicate them.**

### Layer C — Close the loop

**C1. `incident-postmortem`** — *source: Google SRE ch.15*
- Triggers **declared in advance**; blameless; action items with owners; and the
  hard rule *"an unreviewed postmortem might as well never have existed."*
- **Our test:** a trigger fires → a postmortem row exists; an unreviewed one is
  refused at merge.
- **Automation:** our `skill_lesson` + `skill_contract_*` tables already model
  this; add a review status gate (draft → reviewed) that blocks merge.

**C2. `condition-based-waiting`** — *source: superpowers supporting technique*
- Replace fixed sleeps with a polled condition + timeout.
- **Our test:** `sleep(1.4)` becomes `wait_until(menu_visible, timeout=3.0)`;
  assert it returns as soon as true and fails loudly on timeout.

---

## 4. What is genuinely automatic already (measured today)

| Mechanism | Automation | Evidence |
|---|---|---|
| `auto_rect_audit.audit_rects()` | callable | judged=1 mismatched=0 not_applicable=3 |
| `assert_rect_ok(id)` | pre-action gate | raised `RectNotProven`; cursor did not move |
| `auto_rect_audit.py --watch N` | schedulable | present |
| Findings → `skill_lesson` + `source_ref` | dedup | re-run skipped both |
| `_check_skill_ssot_drift.py` | file↔SSOT sha256 | 3 skills `in sync` |

**So Layer C is partly built.** What is missing is the **Layer A stop rule** —
nothing today said "you have tried this 4 times, stop and re-frame".

---

## 5. Recommended order (cost, value)

| # | Skill | Why first | Effort |
|---|---|---|---|
| 1 | `systematic-debugging` (A1) | Directly prevents today's failure class; source is verbatim-adoptable; our test case already exists | low |
| 2 | `problem-statement` (A2) | Cheapest gate; stops analysis on an unscoped complaint | low |
| 3 | `citation-discipline` (B1) | One rule, mechanical to enforce | low |
| 4 | `incident-postmortem` (C1) | Needs a trigger list agreed in advance | medium |
| 5 | `condition-based-waiting` (C2) | Localised change, few call sites | low |

**Adopt, do not copy.** `systematic-debugging` is MIT and its text is short; the
value is the *stop rule and the citation requirement*, which we can implement and
test against our own incidents. Copying the prose would add a document without
adding a gate — the exact defect three of our skills were written to fix.

---

## 6. Honest limits of this report

1. **No cross-repo rating survey.** Auth was unavailable for GitHub code/repo
   search. The star count read (288.8k) is large and from the repo page, but it is
   one project, not a ranking.
2. **The "3+ fixes" rule was tested by replay, not by experiment.** I matched it
   against today's sequence after the fact. That is suggestive, not a controlled
   trial.
3. **SRE postmortem culture is an organisational practice**, not a library. Its
   transferable part is the *trigger list* and the *review gate*; the rest is
   cultural and cannot be installed.
4. **I am the party that made the mistakes.** A rule I write to stop my own error
   class deserves adversarial testing by someone else before it is trusted.

---

## Sources (first-party, read in this session)

- `github.com/obra/superpowers` — repo page (288.8k stars, MIT, v6.4.1)
- `raw.githubusercontent.com/obra/superpowers/main/skills/systematic-debugging/SKILL.md`
- `raw.githubusercontent.com/obra/superpowers/main/skills/diagnosing-superpowers/SKILL.md`
- `sre.google/sre-book/postmortem-culture/` — chapter 15
- `playwright.dev/docs/actionability`
- `selenium.dev/documentation/webdriver/troubleshooting/errors/`
