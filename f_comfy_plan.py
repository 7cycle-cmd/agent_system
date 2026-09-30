# -*- coding: utf-8 -*-
"""f_comfy_plan.py — print every step Phase 3 WOULD run. Executes nothing.

Phase 2 of the ComfyUI plan (`HANDOFF_comfyui_video_phase2.md` §6). This is a
DRY RUN: it prints the exact pip command, the model URL, the download size, and
the server start command, and then stops. It installs nothing, downloads
nothing, starts nothing.

Why a dry run rather than "just run it":
  - The torch wheel for Blackwell (sm_120) is a multi-GB download. Discovering
    the wrong wheel AFTER the download wastes it and can leave the venv unusable.
  - A plan a human can read is the thing that gets approved. Approval of an
    idea is not approval of artifacts that do not exist yet
    (`skill_research_gate`).

Model route is a DECISION, not a guess. The default is the Phase 1
recommendation (LTXV 2B distilled — the only route with independent proof of
running in <=8 GB). Override with --model.

Usage:
  python f_comfy_plan.py --dry-run
  python f_comfy_plan.py --dry-run --model wan22_ti2v_5b
  python f_comfy_plan.py --dry-run --json

Exit codes: 0 plan printed, 2 unknown model route, 3 environment not ready.
"""
from __future__ import annotations

import argparse
import json
import sys

# ---------------------------------------------------------------------------
# Model routes. Sizes are the OFFICIAL figures from the Phase 1 report
# (`evidence/EVID-comfyui_research-20260920-055404/report.md` §2), not guesses.
# ---------------------------------------------------------------------------
MODELS = {
    "ltxv_2b_distilled": {
        "label": "LTXV 2B distilled",
        "repo": "Lightricks/LTX-Video",
        "file": "ltxv-2b-0.9.8-distilled.safetensors",
        "url": ("https://huggingface.co/Lightricks/LTX-Video/resolve/main/"
                "ltxv-2b-0.9.8-distilled.safetensors"),
        "dest": "models/checkpoints",
        "approx_gb": 4.0,
        "official_vram": "~8 GB (community proof: RTX 4060 8GB)",
        "fits_16gb": True,
        "license": "Apache-2.0",
        "why": ("Only route with independent proof of running in <=8 GB. "
                "8-step distilled sampling -> fast iteration for Phase 4."),
    },
    "ltxv_13b_distilled": {
        "label": "LTXV 13B distilled",
        "repo": "Lightricks/LTX-Video",
        "file": "ltxv-13b-0.9.8-distilled.safetensors",
        "url": ("https://huggingface.co/Lightricks/LTX-Video/resolve/main/"
                "ltxv-13b-0.9.8-distilled.safetensors"),
        "dest": "models/checkpoints",
        "approx_gb": 13.0,
        "official_vram": "not officially quantified for 16 GB",
        "fits_16gb": None,
        "license": "Apache-2.0",
        "why": "Upgrade path once the pipeline is proven. VRAM unproven at 16 GB.",
    },
    "wan22_ti2v_5b": {
        "label": "Wan2.2 TI2V-5B",
        "repo": "Wan-AI/Wan2.2-TI2V-5B",
        "file": "(whole repo snapshot)",
        "url": "https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B",
        "dest": "models/checkpoints",
        "approx_gb": 20.0,
        "official_vram": "24 GB (README: 'at least 24GB VRAM, e.g. RTX 4090')",
        "fits_16gb": False,
        "license": "Apache-2.0",
        "why": ("NOT viable on 16 GB. The README offers --offload_model as an OOM "
                "workaround but does not claim 16 GB support."),
    },
    "wan22_a14b": {
        "label": "Wan2.2 A14B (T2V/I2V)",
        "repo": "Wan-AI/Wan2.2-T2V-A14B",
        "file": "(whole repo snapshot)",
        "url": "https://huggingface.co/Wan-AI/Wan2.2-T2V-A14B",
        "dest": "models/checkpoints",
        "approx_gb": 60.0,
        "official_vram": "80 GB (README: 'at least 80GB VRAM')",
        "fits_16gb": False,
        "license": "Apache-2.0",
        "why": "Out of reach on 16 GB. The handoff's '~14GB' was the param count.",
    },
    "svd_xt": {
        "label": "SVD-XT",
        "repo": "stabilityai/stable-video-diffusion-img2vid-xt",
        "file": "svd_xt.safetensors",
        "url": ("https://huggingface.co/stabilityai/"
                "stable-video-diffusion-img2vid-xt/resolve/main/svd_xt.safetensors"),
        "dest": "models/checkpoints",
        "approx_gb": 9.5,
        "official_vram": "fits 16 GB",
        "fits_16gb": True,
        "license": "research-only (not permissive)",
        "why": ("REJECTED as primary: image-to-video ONLY, so it cannot serve a "
                "topic-driven contract; research-only license; repo deprecated."),
    },
}

DEFAULT_MODEL = "ltxv_2b_distilled"

# The torch build for Blackwell. cu128 is the minimum for sm_120.
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
TORCH_PACKAGES = ["torch", "torchvision", "torchaudio"]

SERVER_PORT = 8188


def build_plan(model_key: str) -> dict:
    """Return the ordered Phase 3 steps. Pure: no side effects."""
    m = MODELS[model_key]
    return {
        "model_key": model_key,
        "model": m,
        "steps": [
            {
                "n": 1,
                "stage": "torch",
                "why": ("Blackwell sm_120 needs CUDA 12.8+. An older cu121 wheel "
                        "will NOT work."),
                "command": ("%s -m pip install %s --index-url %s"
                            % ("<comfy-venv-python>", " ".join(TORCH_PACKAGES),
                               TORCH_INDEX)),
                "verify": ("<comfy-venv-python> -c \"import torch; "
                           "print(torch.__version__, torch.cuda.is_available())\""),
                "expect": "torch.cuda.is_available() == True",
                "approx_download_gb": 3.0,
            },
            {
                "n": 2,
                "stage": "requirements",
                "why": "ComfyUI's own deps (transformers, av, torchsde, ...).",
                "command": ("<comfy-venv-python> -m pip install -r "
                            "C:\\projects\\ComfyUI\\requirements.txt"),
                "verify": "<comfy-venv-python> -c \"import comfy; print('ok')\"",
                "expect": "import succeeds",
                "approx_download_gb": 2.0,
            },
            {
                "n": 3,
                "stage": "model",
                "why": m["why"],
                "command": ("download %s -> C:\\projects\\ComfyUI\\%s\\%s"
                            % (m["url"], m["dest"], m["file"])),
                "verify": "file exists and size is plausible",
                "expect": "~%.1f GB" % m["approx_gb"],
                "approx_download_gb": m["approx_gb"],
            },
            {
                "n": 4,
                "stage": "server",
                "why": "Prove the server answers before Phase 4 uses its API.",
                "command": ("<comfy-venv-python> C:\\projects\\ComfyUI\\main.py "
                            "--port %d" % SERVER_PORT),
                "verify": "GET http://127.0.0.1:%d/system_stats" % SERVER_PORT,
                "expect": "HTTP 200 with a JSON body",
                "approx_download_gb": 0.0,
            },
        ],
        "total_download_gb": round(
            3.0 + 2.0 + m["approx_gb"], 1),
        "ffmpeg_required": True,
        "ffmpeg_note": ("The compose stage of VIDEO_7_STAGE_FLOW needs ffmpeg. "
                        "It is NOT on PATH (f_comfy_check.py). Decision 3."),
        "read_only": True,
        "note": ("DRY RUN — nothing above has been executed. Phase 3 needs "
                 "separate approval."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ComfyUI Phase 3 dry-run planner")
    ap.add_argument("--dry-run", action="store_true",
                    help="required; this script never executes")
    ap.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(MODELS),
                    help="model route (default: %s)" % DEFAULT_MODEL)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list-models", action="store_true")
    args = ap.parse_args(argv)

    if args.list_models:
        print("Model routes (official VRAM figures from the Phase 1 report):")
        for k, m in sorted(MODELS.items()):
            fits = {True: "FITS", False: "NO", None: "UNPROVEN"}[m["fits_16gb"]]
            print("  %-22s %-24s %-9s %s" % (k, m["label"], fits, m["official_vram"]))
        print("\n  default: %s" % DEFAULT_MODEL)
        return 0

    if not args.dry_run:
        print("This script is a DRY RUN only. Pass --dry-run.")
        print("It never installs, downloads, or starts anything.")
        return 2

    plan = build_plan(args.model)

    if args.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0

    m = plan["model"]
    print("=== ComfyUI Phase 3 plan (DRY RUN — nothing executed) ===")
    print()
    print("Model route : %s (%s)" % (m["label"], plan["model_key"]))
    print("  repo      : %s" % m["repo"])
    print("  license   : %s" % m["license"])
    print("  official  : %s" % m["official_vram"])
    print("  fits 16GB : %s" % {True: "yes", False: "NO", None: "unproven"}[m["fits_16gb"]])
    print("  why       : %s" % m["why"])
    print()
    print("Steps that WOULD run:")
    for s in plan["steps"]:
        print()
        print("  %d. %s" % (s["n"], s["stage"]))
        print("     why     : %s" % s["why"])
        print("     command : %s" % s["command"])
        print("     verify  : %s" % s["verify"])
        print("     expect  : %s" % s["expect"])
        print("     download: ~%.1f GB" % s["approx_download_gb"])
    print()
    print("Total download: ~%.1f GB" % plan["total_download_gb"])
    print("ffmpeg required: %s" % plan["ffmpeg_note"])
    print()
    print("(nothing was installed, downloaded, or started)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
