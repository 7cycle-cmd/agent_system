# -*- coding: utf-8 -*-
"""f_comfy_check.py — verify the ComfyUI environment BEFORE installing anything.

Phase 2 of the ComfyUI plan (`HANDOFF_comfyui_video_phase2.md` §6). This script
is READ-ONLY: it installs nothing, downloads nothing, starts nothing. It exists
so the install (Phase 3) is planned against MEASURED facts rather than
assumptions — the same rule `env_task_proof` enforces for UI actions.

Why a separate check rather than "just try the install":
  - The RTX 5060 Ti is Blackwell (sm_120). An older cu121 torch wheel will NOT
    work, and discovering that after a multi-GB download wastes the download and
    can leave the venv unusable.
  - A wrong assumption here is expensive; a check here is free.

Usage:
  python f_comfy_check.py            # human-readable report
  python f_comfy_check.py --json     # machine-readable
  python f_comfy_check.py --quiet    # exit code only

Exit codes (graded, so a caller can tell WHICH class of problem):
  0  all checks pass
  1  a required dependency is missing (ffmpeg / ComfyUI paths)
  2  GPU or CUDA problem (no GPU, driver too old for sm_120)
  3  disk or network problem
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

COMFY_ROOT = Path(r"C:\projects\ComfyUI")
COMFY_VENV_PY = COMFY_ROOT / ".venv" / "Scripts" / "python.exe"
COMFY_VERSION_FILE = COMFY_ROOT / "comfyui_version.py"
COMFY_REQUIREMENTS = COMFY_ROOT / "requirements.txt"

# Blackwell (sm_120) needs CUDA 12.8+. Driver 570+ ships CUDA 12.8.
MIN_DRIVER_FOR_SM120 = 570
MIN_DISK_GB = 60          # torch + one model + headroom
MODEL_HOST = "huggingface.co"
MODEL_HOST_PORT = 443

EXIT_OK = 0
EXIT_DEP = 1
EXIT_GPU = 2
EXIT_DISK_NET = 3


def _run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    """Run a command, return (returncode, stdout+stderr). Never raises.

    CREATE_NO_WINDOW: this runs inside the helper's request handler, and the
    preflight is polled every 5s by the queue page. Without this flag each poll
    spawns a console window — measured 2026-09-20 as a window that popped up
    every 5 seconds, forever, on /llm-tasks/task_center/queue. The checks are
    read-only and need no console.
    """
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "not found: %s" % cmd[0]
    except Exception as e:
        return 1, "%s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------------------
# individual checks — each returns a dict, never raises
# ---------------------------------------------------------------------------

def check_gpu() -> dict:
    """GPU name, VRAM, driver version, and whether the driver supports sm_120."""
    rc, out = _run([
        "nvidia-smi",
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ])
    if rc != 0:
        return {"ok": False, "severity": "gpu",
                "detail": "nvidia-smi failed (rc=%s): %s" % (rc, out.strip()[:200])}

    line = out.strip().splitlines()[0] if out.strip() else ""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 3:
        return {"ok": False, "severity": "gpu",
                "detail": "unexpected nvidia-smi output: %r" % line}

    name, vram_mib, driver = parts[0], parts[1], parts[2]
    try:
        vram_gb = round(int(float(vram_mib)) / 1024, 1)
    except Exception:
        vram_gb = None
    try:
        driver_major = int(str(driver).split(".")[0])
    except Exception:
        driver_major = None

    sm120_ok = driver_major is not None and driver_major >= MIN_DRIVER_FOR_SM120
    return {
        "ok": True,
        "severity": "gpu",
        "name": name,
        "vram_mib": vram_mib,
        "vram_gb": vram_gb,
        "driver": driver,
        "driver_major": driver_major,
        "sm120_supported": sm120_ok,
        "detail": "%s, %s MiB (%s GB), driver %s — sm_120 %s"
                  % (name, vram_mib, vram_gb, driver,
                     "OK" if sm120_ok else
                     "NEEDS driver >= %d" % MIN_DRIVER_FOR_SM120),
    }


def check_disk(path: Path = Path("C:/")) -> dict:
    """Free space on the target drive."""
    try:
        usage = shutil.disk_usage(str(path))
        free_gb = round(usage.free / (1024 ** 3), 1)
        ok = free_gb >= MIN_DISK_GB
        return {
            "ok": ok, "severity": "disk", "free_gb": free_gb,
            "required_gb": MIN_DISK_GB,
            "detail": "%s free on %s (need >= %s GB)"
                      % (free_gb, path, MIN_DISK_GB),
        }
    except Exception as e:
        return {"ok": False, "severity": "disk",
                "detail": "%s: %s" % (type(e).__name__, e)}


def check_ffmpeg() -> dict:
    """ffmpeg on PATH — needed for the compose stage of the 7-stage flow."""
    exe = shutil.which("ffmpeg")
    if not exe:
        return {"ok": False, "severity": "dep",
                "detail": "ffmpeg NOT on PATH (needed for the compose stage)"}
    rc, out = _run([exe, "-version"])
    first = out.strip().splitlines()[0] if out.strip() else ""
    return {"ok": rc == 0, "severity": "dep", "path": exe,
            "detail": first[:120] or "ffmpeg present"}


def check_network(host: str = MODEL_HOST, port: int = MODEL_HOST_PORT) -> dict:
    """TCP reachability to the model host (no download, just a connect)."""
    try:
        with socket.create_connection((host, port), timeout=8):
            return {"ok": True, "severity": "net",
                    "detail": "%s:%s reachable" % (host, port)}
    except Exception as e:
        return {"ok": False, "severity": "net",
                "detail": "%s:%s unreachable — %s: %s"
                          % (host, port, type(e).__name__, e)}


def check_comfy_paths() -> dict:
    """The ComfyUI clone and its venv exist where the handoff says they do."""
    missing = []
    if not COMFY_ROOT.is_dir():
        missing.append(str(COMFY_ROOT))
    if not COMFY_VENV_PY.is_file():
        missing.append(str(COMFY_VENV_PY))
    if not COMFY_REQUIREMENTS.is_file():
        missing.append(str(COMFY_REQUIREMENTS))
    if missing:
        return {"ok": False, "severity": "dep",
                "detail": "missing: %s" % ", ".join(missing)}
    return {"ok": True, "severity": "dep",
            "detail": "ComfyUI clone + venv + requirements.txt present"}


def check_comfy_version() -> dict:
    """Read the installed ComfyUI version from its own file."""
    if not COMFY_VERSION_FILE.is_file():
        return {"ok": False, "severity": "dep",
                "detail": "no %s" % COMFY_VERSION_FILE.name}
    try:
        text = COMFY_VERSION_FILE.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"ok": False, "severity": "dep",
                "detail": "%s: %s" % (type(e).__name__, e)}
    ver = None
    for line in text.splitlines():
        if "version" in line.lower() and "=" in line:
            ver = line.split("=", 1)[1].strip().strip("'\"")
            break
    return {"ok": True, "severity": "dep", "version": ver,
            "detail": "ComfyUI version %s" % (ver or "(unparsed)")}


def check_venv_python() -> dict:
    """The venv's Python version, and whether torch is importable."""
    if not COMFY_VENV_PY.is_file():
        return {"ok": False, "severity": "dep",
                "detail": "venv python missing: %s" % COMFY_VENV_PY}
    rc, out = _run([str(COMFY_VENV_PY), "--version"])
    pyver = out.strip() if rc == 0 else "unknown"

    # torch import is EXPECTED to fail before Phase 3. That is not an error —
    # it is the measured pre-install state, and reporting it as a failure would
    # make the check useless as a baseline.
    rc2, out2 = _run([str(COMFY_VENV_PY), "-c",
                      "import torch;print(torch.__version__)"])
    torch_ok = rc2 == 0
    torch_ver = out2.strip().splitlines()[-1] if torch_ok and out2.strip() else None
    return {
        "ok": True, "severity": "dep",
        "python": pyver,
        "torch_installed": torch_ok,
        "torch_version": torch_ver,
        "detail": "%s; torch %s" % (
            pyver,
            ("installed: %s" % torch_ver) if torch_ok
            else "NOT installed (expected before Phase 3)"),
    }


def check_site_packages() -> dict:
    """What is actually installed in the ComfyUI venv (should be pip only)."""
    sp = COMFY_ROOT / ".venv" / "Lib" / "site-packages"
    if not sp.is_dir():
        return {"ok": False, "severity": "dep",
                "detail": "no site-packages at %s" % sp}
    try:
        names = sorted(p.name for p in sp.iterdir() if p.is_dir())
    except Exception as e:
        return {"ok": False, "severity": "dep",
                "detail": "%s: %s" % (type(e).__name__, e)}
    return {"ok": True, "severity": "dep", "packages": names,
            "detail": "%d package dirs: %s" % (len(names), ", ".join(names[:8]))}


def check_port_free(port: int = 8188) -> dict:
    """Is the ComfyUI port already in use?

    PRE-INSTALL this is a failure: a stale server would confuse Phase 3.
    POST-INSTALL a listening port is the DESIRED state, so the caller passes
    `expect_listening=True` and the check inverts. Without this the check
    reports FAIL on a perfectly healthy install — a check that cries wolf is
    worse than no check, because it trains the reader to ignore it.
    """
    listening = False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            listening = True
    except Exception:
        listening = False

    expect = _EXPECT_LISTENING.get("value", False)
    if expect:
        return {
            "ok": listening, "severity": "dep", "listening": listening,
            "detail": "port %d %s (post-install: expected to be listening)"
                      % (port, "listening" if listening else "NOT listening"),
        }
    return {
        "ok": not listening, "severity": "dep", "listening": listening,
        "detail": "port %d %s" % (
            port,
            "is ALREADY listening — a server is up" if listening else "free"),
    }


# Set by main() from --post-install, so check_port_free can invert its meaning.
_EXPECT_LISTENING: dict = {"value": False}


# ---------------------------------------------------------------------------

CHECKS = (
    ("gpu", check_gpu),
    ("disk", check_disk),
    ("ffmpeg", check_ffmpeg),
    ("network", check_network),
    ("comfy_paths", check_comfy_paths),
    ("comfy_version", check_comfy_version),
    ("venv_python", check_venv_python),
    ("site_packages", check_site_packages),
    ("port_8188", check_port_free),
)


def run_all() -> dict:
    results = {}
    for name, fn in CHECKS:
        try:
            results[name] = fn()
        except Exception as e:
            results[name] = {"ok": False, "severity": "dep",
                             "detail": "check raised %s: %s"
                                       % (type(e).__name__, e)}

    # Graded exit code: the FIRST failing severity wins, in the order
    # gpu > dep > disk/net, so the caller learns the most fundamental problem.
    failed = [r for r in results.values() if not r.get("ok")]
    code = EXIT_OK
    if failed:
        sevs = {r.get("severity") for r in failed}
        if "gpu" in sevs:
            code = EXIT_GPU
        elif "dep" in sevs:
            code = EXIT_DEP
        else:
            code = EXIT_DISK_NET

    return {
        "ok": not failed,
        "exit_code": code,
        "checks": results,
        "failed": [k for k, r in results.items() if not r.get("ok")],
        "read_only": True,
        "note": "This check installs nothing. Phase 3 (install) needs separate approval.",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ComfyUI pre-install environment check")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--quiet", action="store_true", help="exit code only")
    ap.add_argument("--post-install", action="store_true",
                    help="invert the port check: a listening server is EXPECTED")
    args = ap.parse_args(argv)

    _EXPECT_LISTENING["value"] = bool(args.post_install)
    out = run_all()
    out["post_install"] = bool(args.post_install)

    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    elif not args.quiet:
        mode = "POST-INSTALL" if args.post_install else "PRE-INSTALL"
        print("=== ComfyUI environment check (%s, READ-ONLY) ===" % mode)
        for name, r in out["checks"].items():
            mark = "PASS" if r.get("ok") else "FAIL"
            print("  [%s] %-14s %s" % (mark, name, r.get("detail", "")))
        print()
        if out["ok"]:
            print("ALL CHECKS PASS")
        else:
            print("FAILED: %s" % ", ".join(out["failed"]))
            print("exit code %d" % out["exit_code"])
        print("(nothing was installed)")

    return out["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
