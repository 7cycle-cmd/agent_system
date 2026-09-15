"""Local vision analysis via Ollama (qwen2.5vl) for fault evidence PNGs."""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _load_dotenv() -> None:
    base = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(base, ".env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass


_load_dotenv()

DEFAULT_OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:18803")
DEFAULT_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
DEFAULT_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "180"))


@dataclass
class VisionResult:
    model: str
    summary: str
    detail: dict[str, Any]
    raw_text: str
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    duration_ms: int = 0
    raw_usage: dict[str, Any] | None = None


def _file_to_b64(path: str | Path) -> str:
    data = Path(path).read_bytes()
    return base64.b64encode(data).decode("ascii")


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    # Mouse Spot / skill verify: Result: YES|NO + Reason:
    parsed = parse_verify_response(text)
    if parsed.get("parser") != "none":
        return parsed
    return {
        "severity": "unknown",
        "ui_state": text[:500],
        "likely_cause": "model_did_not_return_json",
        "recommended_human_action": "Open the evidence PNG and inspect manually",
        "confidence": 0.0,
    }


def parse_verify_response(text: str) -> dict[str, Any]:
    """Parse skill verify output: Result YES/NO, JSON correct, or free yes/no."""
    raw = (text or "").strip()
    if not raw:
        return {
            "correct": None,
            "result": None,
            "reason": "empty model response",
            "confidence": 0.0,
            "parser": "none",
        }

    # 1) Structured Result: YES/NO
    m_res = re.search(r"result\s*:\s*(yes|no|pass|fail|true|false)\b", raw, re.I)
    m_reason = re.search(r"reason\s*:\s*(.+?)(?:\n|$)", raw, re.I)
    if m_res:
        token = m_res.group(1).lower()
        yes = token in ("yes", "pass", "true")
        return {
            "correct": yes,
            "result": "YES" if yes else "NO",
            "reason": (m_reason.group(1).strip() if m_reason else raw[:200]),
            "confidence": 0.9,
            "parser": "result_yes_no",
        }

    # 2) JSON object with correct / result
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return _normalize_verify_dict(obj, parser="json")
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return _normalize_verify_dict(obj, parser="json_embedded")
        except json.JSONDecodeError:
            pass

    # 3) Leading yes/no only (strict first token)
    first = re.split(r"[\s\.,:;!\?\n]+", raw.lower(), maxsplit=1)[0]
    if first in ("yes", "no"):
        rest = raw[len(first) :].lstrip(" \t:-.,")
        return {
            "correct": first == "yes",
            "result": "YES" if first == "yes" else "NO",
            "reason": rest.strip()[:200] or raw[:200],
            "confidence": 0.7,
            "parser": "leading_yes_no",
        }

    return {
        "correct": None,
        "result": None,
        "reason": raw[:200],
        "confidence": 0.0,
        "parser": "none",
        "ui_state": raw[:500],
    }


def _normalize_verify_dict(obj: dict[str, Any], *, parser: str) -> dict[str, Any]:
    out = dict(obj)
    correct = out.get("correct")
    if correct is None:
        rf = str(out.get("result") or out.get("verdict") or "").strip().upper()
        if rf in ("YES", "PASS", "SUCCESS", "TRUE"):
            correct = True
        elif rf in ("NO", "FAIL", "FALSE"):
            correct = False
    if isinstance(correct, str):
        cl = correct.strip().lower()
        if cl in ("yes", "true", "pass", "1"):
            correct = True
        elif cl in ("no", "false", "fail", "0"):
            correct = False
    if correct is not None:
        out["correct"] = bool(correct)
        out["result"] = "YES" if out["correct"] else "NO"
    if "reason" not in out or not out.get("reason"):
        out["reason"] = out.get("ui_state") or out.get("summary") or ""
    if "confidence" not in out:
        out["confidence"] = 0.8 if correct is not None else 0.0
    out["parser"] = parser
    return out


def analyze_evidence(
    image_path: str | Path | None,
    *,
    fault_type: str,
    worker_id: int | None = None,
    group_id: str | None = None,
    evidence_error: str | None = None,
    event_id: int | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
    prompt: str | None = None,
    format_json: bool | None = None,
    parse_mode: str = "auto",
) -> VisionResult:
    """Analyze a frozen/live PNG. If image_path is None, text-only fallback.

    Args:
        prompt: Optional custom prompt. If omitted, a default fault-analysis
            prompt is used.
        format_json: Force Ollama JSON mode. Default True for fault prompts,
            False when parse_mode is result_yes_no.
        parse_mode: auto | json | result_yes_no
    """
    model = model or DEFAULT_VISION_MODEL
    base_url = (base_url or DEFAULT_OLLAMA_BASE).rstrip("/")
    timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
    if format_json is None:
        format_json = parse_mode != "result_yes_no"

    if prompt is None:
        meta = {
            "event_id": event_id,
            "worker_id": worker_id,
            "group_id": group_id,
            "fault_type": fault_type,
            "evidence_error": evidence_error,
            "image_path": str(image_path) if image_path else None,
        }
        prompt = (
            "You are an ops assistant analyzing a Windows desktop screenshot for a worker fault.\n"
            "Return ONLY a JSON object with keys:\n"
            "  severity: one of low|medium|high|critical\n"
            "  ui_state: short description of what is on screen\n"
            "  likely_cause: likely cause given fault_type\n"
            "  recommended_human_action: what a human should do next\n"
            "  confidence: number 0..1\n"
            f"Fault metadata: {json.dumps(meta, ensure_ascii=False)}\n"
            "If there is no image, say so in ui_state and lower confidence.\n"
        )
        format_json = True if format_json is None else format_json

    if image_path and Path(image_path).is_file():
        b64 = _file_to_b64(image_path)
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [b64],
                }
            ],
            "stream": False,
        }
        if format_json:
            payload["format"] = "json"
    else:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt + "\nNo image was available.",
                }
            ],
            "stream": False,
        }
        if format_json:
            payload["format"] = "json"

    url = f"{base_url}/api/chat"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        return VisionResult(
            model=model,
            summary=f"vision HTTP error: {e.code}",
            detail={},
            raw_text=err,
            error=f"ollama_http_{e.code}",
        )
    except Exception as e:
        return VisionResult(
            model=model,
            summary=f"vision failed: {type(e).__name__}",
            detail={},
            raw_text=str(e),
            error=f"ollama_{type(e).__name__}",
        )

    message = (body.get("message") or {}) if isinstance(body, dict) else {}
    raw = message.get("content") or body.get("response") or ""
    if not isinstance(raw, str):
        raw = json.dumps(raw, ensure_ascii=False)

    if parse_mode == "result_yes_no":
        detail = parse_verify_response(raw)
    elif parse_mode == "json":
        detail = _extract_json(raw)
    else:
        detail = _extract_json(raw)
        if detail.get("correct") is None and detail.get("parser") not in (
            "result_yes_no",
            "leading_yes_no",
            "json",
            "json_embedded",
        ):
            alt = parse_verify_response(raw)
            if alt.get("correct") is not None:
                detail = alt

    if "severity" in detail:
        summary = (
            f"[{detail.get('severity', '?')}] {detail.get('ui_state', '')} | "
            f"cause={detail.get('likely_cause', '')}"
        ).strip()
    else:
        summary = detail.get("reason") or detail.get("ui_state") or raw[:200]
    if len(summary) > 280:
        summary = summary[:277] + "..."

    # Ollama token / timing fields (chat + generate style).
    prompt_tokens = int(body.get("prompt_eval_count") or body.get("prompt_tokens") or 0)
    completion_tokens = int(body.get("eval_count") or body.get("completion_tokens") or 0)
    total_tokens = prompt_tokens + completion_tokens
    # total_duration is nanoseconds in Ollama responses.
    total_ns = body.get("total_duration") or 0
    try:
        duration_ms = int(float(total_ns) / 1_000_000)
    except (TypeError, ValueError):
        duration_ms = 0
    raw_usage = {
        "prompt_eval_count": body.get("prompt_eval_count"),
        "eval_count": body.get("eval_count"),
        "total_duration": body.get("total_duration"),
        "load_duration": body.get("load_duration"),
        "prompt_eval_duration": body.get("prompt_eval_duration"),
        "eval_duration": body.get("eval_duration"),
    }

    return VisionResult(
        model=model,
        summary=summary or "analysis complete",
        detail=detail,
        raw_text=raw,
        error=None,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        duration_ms=duration_ms,
        raw_usage=raw_usage,
    )


def complete_text(
    prompt: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
    system: str | None = None,
    format_json: bool = False,
) -> VisionResult:
    """Text-only Ollama chat completion (no image). Optional JSON format."""
    model = model or os.environ.get("OLLAMA_TEXT_MODEL") or "qwen2.5:7b-instruct"
    base_url = (base_url or DEFAULT_OLLAMA_BASE).rstrip("/")
    timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if format_json:
        payload["format"] = "json"

    url = f"{base_url}/api/chat"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        return VisionResult(
            model=model,
            summary=f"text HTTP error: {e.code}",
            detail={},
            raw_text=err,
            error=f"ollama_http_{e.code}",
        )
    except Exception as e:
        return VisionResult(
            model=model,
            summary=f"text failed: {type(e).__name__}",
            detail={},
            raw_text=str(e),
            error=f"ollama_{type(e).__name__}",
        )

    message = (body.get("message") or {}) if isinstance(body, dict) else {}
    raw = message.get("content") or body.get("response") or ""
    if not isinstance(raw, str):
        raw = json.dumps(raw, ensure_ascii=False)
    detail = _extract_json(raw) if format_json else {}
    summary = (
        (detail.get("summary") or detail.get("notes") or raw[:200])
        if detail
        else (raw[:200] if raw else "complete")
    )
    if len(summary) > 280:
        summary = summary[:277] + "..."

    prompt_tokens = int(body.get("prompt_eval_count") or body.get("prompt_tokens") or 0)
    completion_tokens = int(body.get("eval_count") or body.get("completion_tokens") or 0)
    total_tokens = prompt_tokens + completion_tokens
    total_ns = body.get("total_duration") or 0
    try:
        duration_ms = int(float(total_ns) / 1_000_000)
    except (TypeError, ValueError):
        duration_ms = 0
    raw_usage = {
        "prompt_eval_count": body.get("prompt_eval_count"),
        "eval_count": body.get("eval_count"),
        "total_duration": body.get("total_duration"),
        "load_duration": body.get("load_duration"),
        "prompt_eval_duration": body.get("prompt_eval_duration"),
        "eval_duration": body.get("eval_duration"),
    }
    return VisionResult(
        model=model,
        summary=summary or "complete",
        detail=detail if isinstance(detail, dict) else {},
        raw_text=raw,
        error=None,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        duration_ms=duration_ms,
        raw_usage=raw_usage,
    )


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("image", nargs="?", help="PNG path")
    p.add_argument("--fault-type", default="hang")
    args = p.parse_args()
    r = analyze_evidence(args.image, fault_type=args.fault_type)
    print(json.dumps({
        "model": r.model,
        "summary": r.summary,
        "detail": r.detail,
        "error": r.error,
    }, ensure_ascii=False, indent=2))
