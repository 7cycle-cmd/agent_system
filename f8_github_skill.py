# -*- coding: utf-8 -*-
"""
Tool #5: F8 = TOOL.F8.GITHUB.SKILL — GitHub proofed skill finder (versioned).

One-key "GitHub 搵 proofed skill": fetch a GitHub repo/doc URL, extract
reusable proofed lessons (via skill_learning.import_github_lessons, 7B),
and record them into the skill library as a NEW VERSION of the SAME skill_id
(version bump, never a new skill_id).

Python = orchestration + report SSOT. AHK (F8::) = hotkey entry.

Modes:
  --check [--url U] [--skill-id S]
      Validate inputs (url + skill_id present). exit 0 = ok, 1 = missing.
      No side effects.
  --run [--url U] [--skill-id S] [--api URL]
      1. import_github_lessons(url, skill_id, is_url=True)
      2. version bump: latest published version + 1 (patch)
      3. create_skill_version(skill_id, new_version, bundle_yaml=lessons)
         + set_version_status(skill_id, new_version, 'published')
      4. write github_skill_report.json (+ optional --api POST)
      exit 0 = ok, 1 = import failed, 2 = version write failed.

Versioning rule (user requirement): same skill_id, version +1 each update;
old versions are kept (UNIQUE(skill_id, version)).
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime

import cdp_common  # shared log()

BASE = r"C:\projects\agent_system"
sys.path.insert(0, BASE)

import skill_learning  # noqa: E402
import skill_library_api  # noqa: E402

REPORT = os.path.join(BASE, "github_skill_report.json")
CONFIG = os.path.join(BASE, "f8_config.json")


def log(msg):
    cdp_common.log(msg, tag="F8")


def _load_config():
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _resolve(args):
    cfg = _load_config()
    url = (getattr(args, "url", None) or cfg.get("url") or "").strip()
    skill_id = (getattr(args, "skill_id", None) or cfg.get("skill_id") or "").strip()
    return url, skill_id


def _next_version(skill_id):
    """Latest published version + 1 (patch). No version -> 1.0."""
    latest = skill_library_api.get_latest_published(skill_id)
    if not latest:
        return "1.0"
    m = re.match(r"^(\d+)\.(\d+)$", str(latest.get("version") or ""))
    if not m:
        return "1.0"
    return "%d.%d" % (int(m.group(1)), int(m.group(2)) + 1)


def _finish(report, api_url):
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log("report written: %s" % REPORT)
    if api_url:
        try:
            req = urllib.request.Request(
                api_url,
                data=json.dumps(report).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                log("api POST %s -> %s" % (api_url, r.status))
        except Exception as e:
            log("api POST failed: %s" % e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--url", default=None)
    ap.add_argument("--skill-id", dest="skill_id", default=None)
    ap.add_argument("--api", default=None)
    args = ap.parse_args()

    url, skill_id = _resolve(args)

    if args.check:
        ok = bool(url) and bool(skill_id)
        print(json.dumps({"ok": ok, "url": url, "skill_id": skill_id}, ensure_ascii=False))
        log("check: ok=%s url=%s skill_id=%s" % (ok, url, skill_id))
        sys.exit(0 if ok else 1)

    if not (url and skill_id):
        log("FAIL: --url and --skill-id required (or f8_config.json)")
        print(json.dumps({"ok": False, "error": "url + skill_id required"}, ensure_ascii=False))
        sys.exit(1)

    log("run: url=%s skill_id=%s" % (url, skill_id))
    try:
        imp = skill_learning.import_github_lessons(url, skill_id, is_url=True)
    except Exception as e:
        imp = {"ok": False, "error": "fetch/import exception: %s" % e}
    if not imp.get("ok"):
        log("FAIL: import_github_lessons: %s" % imp.get("error"))
        _finish({"ok": False, "error": imp.get("error"), "skill_id": skill_id,
                 "url": url, "timestamp": datetime.now().isoformat(timespec="seconds")}, args.api)
        sys.exit(1)

    old_version = None
    latest = skill_library_api.get_latest_published(skill_id)
    if latest:
        old_version = latest.get("version")
    new_version = _next_version(skill_id)

    lessons = imp.get("lessons") or []
    bundle_yaml = json.dumps(
        {"skill_id": skill_id, "version": new_version, "source_ref": imp.get("source_ref"),
         "lesson_keys": lessons, "imported": imp.get("imported")},
        ensure_ascii=False, indent=2)

    cv = skill_library_api.create_skill_version(skill_id, new_version, bundle_yaml=bundle_yaml)
    if not cv.get("ok"):
        log("FAIL: create_skill_version")
        _finish({"ok": False, "error": "create_skill_version failed", "skill_id": skill_id,
                 "url": url, "timestamp": datetime.now().isoformat(timespec="seconds")}, args.api)
        sys.exit(2)
    skill_library_api.set_version_status(skill_id, new_version, "published")

    report = {
        "ok": True,
        "skill_id": skill_id,
        "old_version": old_version,
        "new_version": new_version,
        "imported": imp.get("imported"),
        "lessons": lessons,
        "source_ref": imp.get("source_ref"),
        "url": url,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    log("OK: %s %s -> %s (imported %s)" % (skill_id, old_version or "none", new_version, imp.get("imported")))
    _finish(report, args.api)
    print(json.dumps(report, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
