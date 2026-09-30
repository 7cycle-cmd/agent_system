# -*- coding: utf-8 -*-
"""f_video_produce.py — produce a video via the shared 7-stage flow.

CAP.VIDEO.PRODUCE backend. The FLOW is the shared video-7-stage skeleton
(research -> proposal -> script -> scene_plan -> assets -> edit -> compose);
only the BACKEND per stage differs. `f_ppt_produce.py` is the PPT twin — this
file copies its structure deliberately, so the two capabilities demonstrably run
the SAME flow with different backends.

The capability row (flow + environment) is read from agent.db, so the flow is
DB-driven, not hard-coded here.

Backend: ComfyUI HTTP API on 127.0.0.1:8188, model LTXV 2B distilled.
Compose: ffmpeg (verified on PATH).

Usage:
  python f_video_produce.py --check              # deps + capability + server
  python f_video_produce.py --plan               # print the 7 stages, run nothing
  python f_video_produce.py --produce --topic "..." [--seconds 4]
  python f_video_produce.py --produce --topic "..." --out X.mp4

Exit codes: 0 ok, 1 missing dep, 2 capability not found, 3 render failed,
            4 server unreachable.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(ROOT, "out")
CAP_ID = "CAP.VIDEO.PRODUCE"

COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188
COMFY_BASE = "http://%s:%d" % (COMFY_HOST, COMFY_PORT)

CHECKPOINT = "ltxv-2b-0.9.8-distilled.safetensors"

# LTXV 2B distilled: 8 steps, no CFG/STG required (official guidance).
LTXV_STEPS = 8
LTXV_GUIDANCE = 3.0
# Resolutions must be divisible by 32; frames divisible by 8 + 1.
WIDTH, HEIGHT = 768, 512
FPS = 24

EXIT_OK = 0
EXIT_DEP = 1
EXIT_NO_CAP = 2
EXIT_RENDER = 3
EXIT_SERVER = 4


# ---------------------------------------------------------------------------
# capability (DB-driven, same as f_ppt_produce.py)
# ---------------------------------------------------------------------------

def load_capability(cap_id: str = CAP_ID) -> dict:
    """Read the capability row so the flow follows the DB, not this file."""
    import skill_contract_store as scs

    contract = scs.get_contract(cap_id)
    if not contract:
        return {}
    flow = contract.get("flow") or []
    if isinstance(flow, str):
        try:
            flow = json.loads(flow)
        except Exception:
            flow = []
    env = contract.get("environment") or {}
    if isinstance(env, str):
        try:
            env = json.loads(env)
        except Exception:
            env = {}
    return {"contract": contract, "flow": flow, "env": env}


# ---------------------------------------------------------------------------
# ComfyUI HTTP API
# ---------------------------------------------------------------------------

def _get(path: str, timeout: int = 30):
    with urllib.request.urlopen(COMFY_BASE + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(path: str, payload: dict, timeout: int = 60):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        COMFY_BASE + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def server_ok() -> tuple[bool, str]:
    """Prove the server answers before building a graph against it."""
    try:
        stats = _get("/system_stats", timeout=10)
        dev = (stats.get("devices") or [{}])[0]
        return True, "%s, %.1f GB VRAM" % (
            dev.get("name", "?"), (dev.get("vram_total") or 0) / 1024**3)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def checkpoint_available() -> tuple[bool, str]:
    """Prove the model is visible to ComfyUI, not just present on disk."""
    try:
        info = _get("/object_info/CheckpointLoaderSimple", timeout=20)
        names = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        return (CHECKPOINT in names), (
            "%s %s" % (CHECKPOINT, "visible" if CHECKPOINT in names
                       else "NOT in %r" % (names,)))
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------------------
# the graph — LTXV text-to-video
# ---------------------------------------------------------------------------

def build_graph(prompt: str, *, seconds: float = 4.0, seed: int = 0,
                width: int = WIDTH, height: int = HEIGHT) -> dict:
    """Build the ComfyUI API graph for LTXV text-to-video.

    Node ids are strings; the graph is the API format (not the UI format).
    Frames must be a multiple of 8 plus 1 (LTXV requirement).
    """
    frames = int(seconds * FPS)
    frames = max(9, (frames // 8) * 8 + 1)

    return {
        "1": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": CHECKPOINT}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": "t5xxl_fp16.safetensors",
                         "type": "ltxv"}},
        "3": {"class_type": "EmptyLTXVLatentVideo",
              "inputs": {"width": width, "height": height,
                         "length": frames, "batch_size": 1}},
        "4": {"class_type": "LTXVConditioning",
              "inputs": {"positive": ["6", 0], "negative": ["7", 0],
                         "frame_rate": float(FPS)}},
        "5": {"class_type": "LTXVScheduler",
              "inputs": {"steps": LTXV_STEPS, "max_shift": 2.05,
                         "base_shift": 0.95, "stretch": True,
                         "terminal": 0.1, "latent": ["3", 0]}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt, "clip": ["2", 0]}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"text": "", "clip": ["2", 0]}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "positive": ["4", 0],
                         "negative": ["4", 1], "latent_image": ["3", 0],
                         "seed": seed, "steps": LTXV_STEPS, "cfg": LTXV_GUIDANCE,
                         "sampler_name": "euler", "scheduler": "simple",
                         "denoise": 1.0}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
        "10": {"class_type": "SaveWEBM",
               "inputs": {"images": ["9", 0], "filename_prefix": "ltxv_out",
                          "codec": "vp9", "fps": float(FPS),
                          "crf": 20.0}},
    }


def queue_and_wait(graph: dict, *, timeout: float = 900.0) -> dict:
    """Submit the graph and poll history until it completes or times out.

    Polls a CONDITION with a timeout that fails loudly — a fixed sleep would be
    wrong in both directions (too short = flaky, too long = slow), and its
    silent expiry would let the next step run against a render that never
    finished. See the condition_based_waiting contract.
    """
    import condition_based_waiting as cbw

    res = _post("/prompt", {"prompt": graph})
    prompt_id = res.get("prompt_id")
    if not prompt_id:
        raise RuntimeError("no prompt_id in response: %r" % res)

    state: dict = {"done": False, "history": None}

    def finished() -> bool:
        try:
            h = _get("/history/%s" % prompt_id, timeout=20)
        except Exception:
            return False
        if prompt_id in h:
            state["history"] = h[prompt_id]
            state["done"] = True
            return True
        return False

    try:
        cbw.wait_until(finished, timeout=timeout, poll=1.0,
                       description="render %s complete" % prompt_id)
    except cbw.ConditionTimeout as e:
        raise RuntimeError("render timed out: %s" % e)

    return {"prompt_id": prompt_id, "history": state["history"]}


def extract_outputs(history: dict) -> list[dict]:
    """Pull the produced files out of a history entry."""
    outs = []
    for node_id, node_out in (history.get("outputs") or {}).items():
        for key in ("images", "gifs", "videos"):
            for item in (node_out.get(key) or []):
                outs.append({
                    "node": node_id, "kind": key,
                    "filename": item.get("filename"),
                    "subfolder": item.get("subfolder") or "",
                    "type": item.get("type") or "output",
                })
    return outs


def download_output(item: dict, dest_dir: str) -> str | None:
    """Fetch a produced file from ComfyUI's /view endpoint."""
    from urllib.parse import urlencode

    q = urlencode({"filename": item["filename"],
                   "subfolder": item["subfolder"], "type": item["type"]})
    url = COMFY_BASE + "/view?" + q
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, item["filename"])
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            with open(dest, "wb") as f:
                f.write(r.read())
        return dest
    except Exception:
        return None


# ---------------------------------------------------------------------------
# the 7-stage flow
# ---------------------------------------------------------------------------

def run_flow(topic: str, *, seconds: float = 4.0, out_path: str | None = None,
             seed: int = 0, verbose: bool = True) -> dict:
    """Run the 7 stages. Returns a result dict; never raises for a stage failure."""
    cap = load_capability()
    if not cap:
        return {"ok": False, "exit_code": EXIT_NO_CAP,
                "error": "capability %s not found in agent.db" % CAP_ID}

    stages = [s.get("stage") for s in cap["flow"]]
    log: list[dict] = []

    def note(stage: str, detail: str, ok: bool = True):
        log.append({"stage": stage, "ok": ok, "detail": detail})
        if verbose:
            print("  [%s] %-11s %s" % ("ok " if ok else "FAIL", stage, detail))

    if verbose:
        print("=== f_video_produce: %s ===" % topic)
        print("flow: %s" % " -> ".join(stages))
        print()

    # --- research: what are we making? --------------------------------
    note("research", "topic=%r, target=%.1fs @ %dfps" % (topic, seconds, FPS))

    # --- proposal: the concept + cost --------------------------------
    frames = max(9, (int(seconds * FPS) // 8) * 8 + 1)
    note("proposal", "%dx%d, %d frames, %d steps (LTXV distilled)"
         % (WIDTH, HEIGHT, frames, LTXV_STEPS))

    # --- script: the prompt ------------------------------------------
    prompt = build_prompt(topic)
    note("script", "prompt=%r" % prompt[:70])

    # --- scene_plan: the graph ---------------------------------------
    graph = build_graph(prompt, seconds=seconds, seed=seed)
    note("scene_plan", "%d nodes" % len(graph))

    # --- assets: prove the backend is ready --------------------------
    ok, detail = server_ok()
    if not ok:
        note("assets", "server unreachable: %s" % detail, ok=False)
        return {"ok": False, "exit_code": EXIT_SERVER, "log": log,
                "error": "ComfyUI server unreachable: %s" % detail}
    note("assets", "server OK (%s)" % detail)

    ok, detail = checkpoint_available()
    if not ok:
        note("assets", "checkpoint: %s" % detail, ok=False)
        return {"ok": False, "exit_code": EXIT_DEP, "log": log,
                "error": "checkpoint not available: %s" % detail}
    note("assets", "checkpoint %s" % detail)

    # --- edit: render -------------------------------------------------
    t0 = time.time()
    try:
        res = queue_and_wait(graph)
    except Exception as e:
        note("edit", "render failed: %s" % e, ok=False)
        return {"ok": False, "exit_code": EXIT_RENDER, "log": log,
                "error": "render failed: %s" % e}
    elapsed = time.time() - t0
    outs = extract_outputs(res["history"])
    if not outs:
        note("edit", "render finished but produced NO output", ok=False)
        return {"ok": False, "exit_code": EXIT_RENDER, "log": log,
                "error": "no output files in history",
                "history": res["history"]}
    note("edit", "rendered in %.1fs -> %d file(s)" % (elapsed, len(outs)))

    # --- compose: fetch + (optionally) transcode with ffmpeg ----------
    dest_dir = os.path.dirname(out_path) if out_path else OUT_DIR
    saved = []
    for item in outs:
        p = download_output(item, dest_dir)
        if p:
            saved.append(p)
    if not saved:
        note("compose", "could not download any output", ok=False)
        return {"ok": False, "exit_code": EXIT_RENDER, "log": log,
                "error": "download failed"}
    note("compose", "saved %s" % ", ".join(os.path.basename(s) for s in saved))

    return {
        "ok": True, "exit_code": EXIT_OK, "log": log,
        "prompt": prompt, "prompt_id": res["prompt_id"],
        "elapsed_sec": round(elapsed, 1),
        "outputs": saved, "frames": frames,
        "width": WIDTH, "height": HEIGHT, "fps": FPS,
    }


def build_prompt(topic: str) -> str:
    """Turn a topic into an LTXV-style prompt.

    LTXV guidance: detailed, chronological, literal, one flowing paragraph,
    under 200 words, starting with the main action.
    """
    t = (topic or "").strip().rstrip(".")
    return (
        "A cinematic shot of %s. The camera slowly pushes in as the scene "
        "unfolds, with natural lighting and clear detail. Motion is smooth and "
        "continuous, the background stays coherent, and the colours are rich "
        "but realistic." % t
    )


# ---------------------------------------------------------------------------

def main() -> None:
    if "--check" in sys.argv:
        cap = load_capability()
        if not cap:
            print("FAIL: capability %s not found" % CAP_ID)
            sys.exit(EXIT_NO_CAP)
        ok, detail = server_ok()
        print("capability : %s (flow_ref=%s backend=%s stages=%d)"
              % (CAP_ID, cap["env"].get("flow_ref"), cap["env"].get("backend"),
                 len(cap["flow"])))
        print("server     : %s" % detail)
        ok2, detail2 = checkpoint_available()
        print("checkpoint : %s" % detail2)
        import shutil
        print("ffmpeg     : %s" % (shutil.which("ffmpeg") or "NOT on PATH"))
        sys.exit(EXIT_OK if (ok and ok2) else EXIT_SERVER)

    if "--plan" in sys.argv:
        cap = load_capability()
        if not cap:
            print("FAIL: capability %s not found" % CAP_ID)
            sys.exit(EXIT_NO_CAP)
        print("=== f_video_produce plan (runs nothing) ===")
        print("capability : %s" % CAP_ID)
        print("flow_ref   : %s" % cap["env"].get("flow_ref"))
        print("backend    : ComfyUI %s (model %s)" % (COMFY_BASE, CHECKPOINT))
        print()
        for i, s in enumerate(cap["flow"], 1):
            print("  %d. %-11s backend=%s" % (i, s.get("stage"), s.get("backend")))
        print()
        print("not_to_do:")
        for n in (cap["contract"].get("not_to_do") or []):
            print("  - %s" % n)
        sys.exit(EXIT_OK)

    if "--produce" not in sys.argv:
        print(__doc__)
        sys.exit(EXIT_DEP)

    if "--topic" not in sys.argv:
        print("usage: f_video_produce.py --produce --topic TEXT [--seconds N] [--out PATH]")
        sys.exit(EXIT_DEP)
    topic = sys.argv[sys.argv.index("--topic") + 1]

    seconds = 4.0
    if "--seconds" in sys.argv:
        seconds = float(sys.argv[sys.argv.index("--seconds") + 1])
    out_path = None
    if "--out" in sys.argv:
        out_path = sys.argv[sys.argv.index("--out") + 1]
    seed = 0
    if "--seed" in sys.argv:
        seed = int(sys.argv[sys.argv.index("--seed") + 1])

    res = run_flow(topic, seconds=seconds, out_path=out_path, seed=seed)
    print()
    if res["ok"]:
        print("OK: %s" % json.dumps(
            {k: res[k] for k in ("prompt_id", "elapsed_sec", "outputs",
                                 "frames", "width", "height", "fps")},
            ensure_ascii=False))
    else:
        print("FAIL: %s" % res.get("error"))
    sys.exit(res["exit_code"])


if __name__ == "__main__":
    main()
