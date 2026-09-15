"""HKO dual-path weather proof: Playwright primary 🆚 OpenClaw secondary.

gate=never. Persist under hko_proof/. CLI: run|list|latest|selftest|contracts|html
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
PROOF_DIR = BASE_DIR / "hko_proof"
HKO_URL = "https://www.hko.gov.hk/en/index.html"
PIPELINE_ID = "HKO_dual_path_proof"
GATE_POLICY = "never"

COMPARE_FIELDS = (
    "observed_time",
    "region",
    "temperature_c",
    "temperature_text",
    "humidity",
    "weather_text",
)

# DOM / text scrape helpers
_TEMP_C_RE = re.compile(
    r"(?P<val>-?\d+(?:\.\d+)?)\s*(?:°\s*)?C\b|(?P<val2>-?\d+(?:\.\d+)?)\s*℃",
    re.I,
)
_TEMP_ANY_RE = re.compile(
    r"(?P<val>-?\d+(?:\.\d+)?)\s*(?:degrees?\s*)?(?:celsius|°\s*C|℃)",
    re.I,
)
_HUMIDITY_RE = re.compile(
    r"(?:humidity|相對濕度|相对湿度)[^\d%]{0,24}(?P<val>\d{1,3})\s*%|(?P<val2>\d{1,3})\s*%\s*(?:humidity|RH)",
    re.I,
)
_TIME_RE = re.compile(
    r"(?:at|as of|updated|時間|时间)?\s*"
    r"((?:\d{1,2}[:.]\d{2}\s*(?:am|pm)?)|"
    r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}[:.]\d{2})|"
    r"(?:\d{1,2}\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}(?:\s+\d{1,2}[:.]\d{2})?))",
    re.I,
)
_REGION_HINTS = (
    "Hong Kong Observatory",
    "Hong Kong",
    "HK",
    "香港",
    "天文台",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ts_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_dotenv() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.is_file():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
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


def contracts_doc() -> dict[str, Any]:
    return {
        "pipeline": PIPELINE_ID,
        "gate": GATE_POLICY,
        "url": HKO_URL,
        "paths": {
            "primary": "playwright_chromium_full_page",
            "secondary": "openclaw_webbrowser_plus_screen_snapshot",
        },
        "persist_dir": str(PROOF_DIR),
        "compare_fields": list(COMPARE_FIELDS),
        "cli": ["run", "list", "latest", "selftest", "contracts", "html"],
        "law": [
            "gate=never — proof/detect only",
            "Playwright is primary path",
            "OpenClaw is secondary (webbrowser fallback + screen.snapshot)",
            "normalize before compare",
            "missing playwright is graceful error, not crash",
        ],
        "ui": {
            "page": "/hko",
            "api": "/api/hko",
            "run": "POST /api/hko/run",
            "img": "/hko/img?f=",
        },
    }


def ensure_proof_dir() -> Path:
    PROOF_DIR.mkdir(parents=True, exist_ok=True)
    return PROOF_DIR


def _safe_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def normalize_temperature(raw: Any) -> dict[str, Any]:
    """Normalize temperature to float °C when possible."""
    if raw is None:
        return {"raw": None, "norm": None, "temperature_c": None, "temperature_text": None}
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        c = float(raw)
        return {
            "raw": raw,
            "norm": c,
            "temperature_c": c,
            "temperature_text": f"{c:g}°C",
        }
    s = str(raw).strip()
    if not s:
        return {"raw": raw, "norm": None, "temperature_c": None, "temperature_text": None}
    m = re.search(r"(-?\d+(?:\.\d+)?)", s.replace(",", ""))
    if not m:
        return {
            "raw": raw,
            "norm": None,
            "temperature_c": None,
            "temperature_text": s,
        }
    c = float(m.group(1))
    # crude F→C if labeled
    if re.search(r"°\s*F|\bFahrenheit\b", s, re.I):
        c = (c - 32.0) * 5.0 / 9.0
        c = round(c, 1)
    return {
        "raw": raw,
        "norm": c,
        "temperature_c": c,
        "temperature_text": f"{c:g}°C",
    }


def normalize_humidity(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"raw": None, "norm": None, "humidity": None}
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        h = int(round(float(raw)))
        return {"raw": raw, "norm": h, "humidity": h}
    s = str(raw).strip()
    m = re.search(r"(\d{1,3})", s)
    if not m:
        return {"raw": raw, "norm": None, "humidity": None}
    h = int(m.group(1))
    if h > 100:
        return {"raw": raw, "norm": None, "humidity": None}
    return {"raw": raw, "norm": h, "humidity": h}


def normalize_time(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"raw": None, "norm": None, "observed_time": None}
    s = str(raw).strip()
    if not s:
        return {"raw": raw, "norm": None, "observed_time": None}
    # collapse whitespace; keep human-readable canonical
    s2 = re.sub(r"\s+", " ", s)
    s2 = s2.replace(".", ":") if re.match(r"^\d{1,2}\.\d{2}", s2) else s2
    # normalize am/pm casing
    s2 = re.sub(r"\b(am|pm)\b", lambda m: m.group(1).upper(), s2, flags=re.I)
    return {"raw": raw, "norm": s2, "observed_time": s2}


def normalize_region(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"raw": None, "norm": None, "region": None}
    s = str(raw).strip()
    if not s:
        return {"raw": raw, "norm": None, "region": None}
    low = s.lower().replace(" ", "")
    if "hongkong" in low or s in ("HK", "香港", "香港天文台") or "observatory" in low:
        norm = "Hong Kong"
    else:
        norm = re.sub(r"\s+", " ", s)
    return {"raw": raw, "norm": norm, "region": norm}


def normalize_weather_text(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"raw": None, "norm": None, "weather_text": None}
    s = str(raw).strip()
    if not s:
        return {"raw": raw, "norm": None, "weather_text": None}
    s2 = re.sub(r"\s+", " ", s).strip(" .;|")
    return {"raw": raw, "norm": s2.lower(), "weather_text": s2}


def normalize_observation(obs: dict[str, Any] | None) -> dict[str, Any]:
    """Pure normalize of a path observation dict."""
    obs = dict(obs or {})
    t = normalize_temperature(
        obs.get("temperature_c", obs.get("temperature", obs.get("temperature_text")))
    )
    h = normalize_humidity(obs.get("humidity"))
    tm = normalize_time(obs.get("observed_time", obs.get("time")))
    rg = normalize_region(obs.get("region", obs.get("location")))
    wt = normalize_weather_text(obs.get("weather_text", obs.get("weather")))
    conf = obs.get("confidence")
    try:
        conf_n = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        conf_n = None
    return {
        "observed_time": tm.get("observed_time"),
        "region": rg.get("region"),
        "temperature_c": t.get("temperature_c"),
        "temperature_text": t.get("temperature_text")
        or _safe_str(obs.get("temperature_text")),
        "humidity": h.get("humidity"),
        "weather_text": wt.get("weather_text"),
        "confidence": conf_n,
        "norms": {
            "observed_time": tm.get("norm"),
            "region": rg.get("norm"),
            "temperature_c": t.get("norm"),
            "humidity": h.get("norm"),
            "weather_text": wt.get("norm"),
        },
        "raw": obs,
    }


def scrape_dom_text(text: str) -> dict[str, Any]:
    """Regex extract weather fields from page body text."""
    text = text or ""
    flat = re.sub(r"[ \t]+", " ", text)
    out: dict[str, Any] = {
        "observed_time": None,
        "region": None,
        "temperature_c": None,
        "temperature_text": None,
        "humidity": None,
        "weather_text": None,
        "confidence": 0.0,
        "source": "dom_regex",
    }
    hits = 0
    m = _TEMP_C_RE.search(flat) or _TEMP_ANY_RE.search(flat)
    if m:
        val = m.groupdict().get("val") or m.groupdict().get("val2")
        try:
            out["temperature_c"] = float(val)
            out["temperature_text"] = f"{float(val):g}°C"
            hits += 1
        except (TypeError, ValueError):
            pass
    mh = _HUMIDITY_RE.search(flat)
    if mh:
        hv = mh.groupdict().get("val") or mh.groupdict().get("val2")
        try:
            out["humidity"] = int(hv)
            hits += 1
        except (TypeError, ValueError):
            pass
    mt = _TIME_RE.search(flat)
    if mt:
        out["observed_time"] = mt.group(1).strip()
        hits += 1
    for hint in _REGION_HINTS:
        if hint.lower() in flat.lower():
            out["region"] = "Hong Kong" if hint != "香港" else "Hong Kong"
            hits += 1
            break
    # weather keywords
    for kw in (
        "Fine",
        "Sunny",
        "Cloudy",
        "Overcast",
        "Rain",
        "Showers",
        "Thunderstorm",
        "Mist",
        "Fog",
        "Haze",
        "Windy",
        "Hot",
        "Warm",
        "Cool",
        "Cold",
    ):
        if re.search(rf"\b{kw}\b", flat, re.I):
            out["weather_text"] = kw
            hits += 1
            break
    out["confidence"] = min(0.95, 0.15 * hits)
    return out


def compare_observations(
    primary: dict[str, Any] | None,
    secondary: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compare normalized fields. gate=never."""
    a = normalize_observation(primary)
    b = normalize_observation(secondary)
    fields: dict[str, Any] = {}
    matches = 0
    compared = 0
    for key in COMPARE_FIELDS:
        na = (a.get("norms") or {}).get(key, a.get(key))
        nb = (b.get("norms") or {}).get(key, b.get(key))
        # temperature_text follows temperature_c norm when both numeric
        if key == "temperature_text":
            na = (a.get("norms") or {}).get("temperature_c", na)
            nb = (b.get("norms") or {}).get("temperature_c", nb)
        empty_a = na is None or na == ""
        empty_b = nb is None or nb == ""
        if empty_a and empty_b:
            status = "both_empty"
            ok = None
        elif empty_a or empty_b:
            status = "one_empty"
            ok = False
            compared += 1
        else:
            compared += 1
            if key == "temperature_c" or key == "temperature_text":
                try:
                    ok = abs(float(na) - float(nb)) <= 1.0
                except (TypeError, ValueError):
                    ok = str(na).lower() == str(nb).lower()
            elif key == "humidity":
                try:
                    ok = abs(int(na) - int(nb)) <= 2
                except (TypeError, ValueError):
                    ok = str(na) == str(nb)
            elif key == "weather_text":
                sa, sb = str(na).lower(), str(nb).lower()
                ok = sa == sb or sa in sb or sb in sa
            elif key == "observed_time":
                sa, sb = str(na).lower(), str(nb).lower()
                ok = sa == sb or sa in sb or sb in sa
            else:
                ok = str(na).lower() == str(nb).lower()
            status = "match" if ok else "mismatch"
            if ok:
                matches += 1
        fields[key] = {
            "playwright": a.get(key),
            "openclaw": b.get(key),
            "playwright_norm": na,
            "openclaw_norm": nb,
            "match_ok": ok,
            "status": status,
        }
    ratio = (matches / compared) if compared else None
    overall = bool(ratio is not None and ratio >= 0.5 and matches >= 1)
    return {
        "gate": GATE_POLICY,
        "match_ok": overall,
        "matches": matches,
        "compared": compared,
        "match_ratio": ratio,
        "fields": fields,
        "playwright_norm": a,
        "openclaw_norm": b,
    }


def path_playwright(*, headed: bool = False, url: str = HKO_URL) -> dict[str, Any]:
    """Primary path: Playwright Chromium full-page screenshot + DOM text."""
    ensure_proof_dir()
    started = time.time()
    out: dict[str, Any] = {
        "path": "playwright",
        "ok": False,
        "url": url,
        "headed": bool(headed),
        "screenshot_path": None,
        "dom_text_path": None,
        "observation": {},
        "error": None,
        "elapsed_s": None,
        "gate": GATE_POLICY,
    }
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        out["error"] = f"playwright_missing:{type(e).__name__}:{e}"
        out["elapsed_s"] = round(time.time() - started, 3)
        return out

    slug = _ts_slug()
    shot = PROOF_DIR / f"pw_{slug}.png"
    dom_path = PROOF_DIR / f"pw_{slug}.txt"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headed)
            try:
                context = browser.new_context(
                    viewport={"width": 1400, "height": 900},
                    locale="en-HK",
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=90_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=20_000)
                except Exception:
                    page.wait_for_timeout(2500)
                page.wait_for_timeout(1500)
                page.screenshot(path=str(shot), full_page=True)
                try:
                    body_text = page.inner_text("body")
                except Exception:
                    body_text = page.content()
                dom_path.write_text(body_text or "", encoding="utf-8", errors="replace")
                scraped = scrape_dom_text(body_text or "")
                out["observation"] = scraped
                out["screenshot_path"] = str(shot)
                out["dom_text_path"] = str(dom_path)
                out["ok"] = True
            finally:
                browser.close()
    except Exception as e:
        out["error"] = f"playwright_run:{type(e).__name__}:{e}"
        out["trace"] = traceback.format_exc()[-1500:]
    out["elapsed_s"] = round(time.time() - started, 3)
    return out


def path_openclaw(*, delay: float = 4.0, url: str = HKO_URL) -> dict[str, Any]:
    """Secondary path: traced webbrowser.open + screen.snapshot."""
    ensure_proof_dir()
    started = time.time()
    out: dict[str, Any] = {
        "path": "openclaw",
        "ok": False,
        "url": url,
        "delay": float(delay),
        "screenshot_path": None,
        "observation": {},
        "open_result": None,
        "snapshot_meta": None,
        "error": None,
        "elapsed_s": None,
        "gate": GATE_POLICY,
    }
    slug = _ts_slug()
    shot = PROOF_DIR / f"oc_{slug}.png"
    warns: list[str] = []

    # open browser
    try:
        from openclaw_mcp_trace import traced_webbrowser_open

        inv = traced_webbrowser_open(
            url, delay=float(delay), tacid="hko.webbrowser"
        )
        out["open_result"] = {
            "ok": inv.get("ok"),
            "error": inv.get("error"),
            "register_id": inv.get("register_id"),
            "traced": inv.get("traced"),
        }
        if not inv.get("ok"):
            warns.append(f"webbrowser:{inv.get('error') or 'failed'}")
    except Exception as e:
        try:
            import webbrowser

            webbrowser.open(url)
            if delay and delay > 0:
                time.sleep(float(delay))
            out["open_result"] = {"ok": True, "fallback": "webbrowser"}
        except Exception as e2:
            out["error"] = (
                f"open_failed:{type(e).__name__}:{e}|{type(e2).__name__}:{e2}"
            )
            out["elapsed_s"] = round(time.time() - started, 3)
            return out

    # snapshot
    result = None
    try:
        from openclaw_mcp_trace import traced_screen_snapshot

        inv = traced_screen_snapshot(tacid="hko.screen_snapshot")
        out["snapshot_meta"] = {
            "ok": inv.get("ok"),
            "error": inv.get("error"),
            "register_id": inv.get("register_id"),
            "traced": inv.get("traced"),
        }
        if not inv.get("ok"):
            out["error"] = f"snapshot:{inv.get('error') or 'failed'}"
            if warns:
                out["error"] += ";" + ";".join(warns)
            out["elapsed_s"] = round(time.time() - started, 3)
            return out
        result = inv.get("result")
    except Exception as e:
        try:
            from mcp_client import McpClient, McpConfig

            client = McpClient(McpConfig.from_env())
            result = client.screen_snapshot()
            out["snapshot_meta"] = {"ok": True, "fallback": "mcp_client"}
        except Exception as e2:
            out["error"] = (
                f"snapshot:{type(e).__name__}:{e}|{type(e2).__name__}:{e2}"
            )
            if warns:
                out["error"] += ";" + ";".join(warns)
            out["elapsed_s"] = round(time.time() - started, 3)
            return out

    try:
        from mcp_client import extract_image_bytes

        data, existing = extract_image_bytes(result)
        if existing and Path(existing).is_file():
            shot.write_bytes(Path(existing).read_bytes())
            out["screenshot_path"] = str(shot)
            out["ok"] = True
        elif data:
            shot.write_bytes(data)
            out["screenshot_path"] = str(shot)
            out["ok"] = True
        else:
            out["error"] = "snapshot:no_image_bytes"
    except Exception as e:
        out["error"] = f"extract_image:{type(e).__name__}:{e}"

    if warns and out.get("ok"):
        out["warning"] = ";".join(warns)
    elif warns and out.get("error"):
        out["error"] = str(out["error"]) + ";" + ";".join(warns)

    out["elapsed_s"] = round(time.time() - started, 3)
    return out


def ollama_extract(
    image_path: str | Path | None,
    *,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Vision JSON: observed_time, region, temperature_c, temperature_text, humidity, weather_text, confidence."""
    try:
        from vision_analyze import (
            DEFAULT_OLLAMA_BASE,
            DEFAULT_TIMEOUT,
            DEFAULT_VISION_MODEL,
            _extract_json,
            _file_to_b64,
        )
    except Exception as e:
        return {
            "ok": False,
            "error": f"import_{type(e).__name__}:{e}",
            "observation": {},
            "model": model or "unknown",
            "raw_text": str(e),
        }

    model = model or DEFAULT_VISION_MODEL
    base_url = (base_url or DEFAULT_OLLAMA_BASE).rstrip("/")
    timeout = timeout if timeout is not None else DEFAULT_TIMEOUT

    prompt = (
        "You are reading a screenshot of the Hong Kong Observatory (HKO) website "
        "or desktop showing HKO weather.\n"
        "Return ONLY a JSON object with keys:\n"
        "  observed_time: string or null (local time shown for the weather reading)\n"
        "  region: string or null (e.g. Hong Kong)\n"
        "  temperature_c: number or null (air temperature in Celsius)\n"
        "  temperature_text: string or null (as displayed, e.g. '28°C')\n"
        "  humidity: number or null (percent 0-100)\n"
        "  weather_text: string or null (short condition, e.g. Cloudy)\n"
        "  confidence: number 0..1\n"
        "If the image is not HKO weather, still extract any visible weather numbers "
        "and lower confidence. No markdown.\n"
    )

    if image_path and Path(image_path).is_file():
        try:
            b64 = _file_to_b64(image_path)
        except Exception as e:
            return {
                "ok": False,
                "error": f"image_read_{type(e).__name__}",
                "observation": {},
                "model": model,
                "raw_text": str(e),
            }
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [b64],
                }
            ],
            "stream": False,
            "format": "json",
        }
    else:
        return {
            "ok": False,
            "error": "no_image",
            "observation": {},
            "model": model,
            "raw_text": "",
        }

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
        return {
            "ok": False,
            "error": f"ollama_http_{e.code}",
            "observation": {},
            "model": model,
            "raw_text": err,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"ollama_{type(e).__name__}:{e}",
            "observation": {},
            "model": model,
            "raw_text": str(e),
        }

    message = (body.get("message") or {}) if isinstance(body, dict) else {}
    raw = message.get("content") or body.get("response") or ""
    if not isinstance(raw, str):
        raw = json.dumps(raw, ensure_ascii=False)
    detail = _extract_json(raw)
    obs = {
        "observed_time": detail.get("observed_time") or detail.get("time"),
        "region": detail.get("region") or detail.get("location"),
        "temperature_c": detail.get("temperature_c") or detail.get("temperature"),
        "temperature_text": detail.get("temperature_text"),
        "humidity": detail.get("humidity"),
        "weather_text": detail.get("weather_text") or detail.get("weather"),
        "confidence": detail.get("confidence"),
        "source": "ollama_vision",
    }
    # coerce numbers
    try:
        if obs["temperature_c"] is not None:
            obs["temperature_c"] = float(obs["temperature_c"])
    except (TypeError, ValueError):
        pass
    try:
        if obs["humidity"] is not None:
            obs["humidity"] = float(obs["humidity"])
    except (TypeError, ValueError):
        pass
    try:
        if obs["confidence"] is not None:
            obs["confidence"] = float(obs["confidence"])
    except (TypeError, ValueError):
        obs["confidence"] = None

    return {
        "ok": True,
        "error": None,
        "observation": obs,
        "model": model,
        "raw_text": raw,
        "detail": detail,
    }


def _merge_obs(*parts: dict[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for p in parts:
        if not p:
            continue
        for k, v in p.items():
            if v is None or v == "":
                continue
            if k not in merged or merged[k] in (None, ""):
                merged[k] = v
    return merged


def run_proof(
    *,
    headed: bool = False,
    openclaw_delay: float = 4.0,
    skip_openclaw: bool = False,
    skip_playwright: bool = False,
    skip_ollama: bool = False,
    url: str = HKO_URL,
) -> dict[str, Any]:
    """Run both paths, optional Ollama extract, normalize+compare, save JSON."""
    ensure_proof_dir()
    run_id = _ts_slug()
    started = time.time()
    result: dict[str, Any] = {
        "run_id": run_id,
        "pipeline": PIPELINE_ID,
        "gate": GATE_POLICY,
        "url": url,
        "created_at": _utc_now_iso(),
        "playwright": None,
        "openclaw": None,
        "compare": None,
        "ok": False,
        "error": None,
        "json_path": None,
        "elapsed_s": None,
    }

    pw = None
    if not skip_playwright:
        pw = path_playwright(headed=headed, url=url)
        if pw.get("ok") and not skip_ollama and pw.get("screenshot_path"):
            vis = ollama_extract(pw["screenshot_path"])
            pw["vision"] = {
                "ok": vis.get("ok"),
                "error": vis.get("error"),
                "model": vis.get("model"),
            }
            # prefer vision numbers; keep DOM as fallback
            pw["observation"] = _merge_obs(
                vis.get("observation") or {},
                pw.get("observation") or {},
            )
            if vis.get("ok"):
                pw["observation"]["source"] = "ollama+dom"
        result["playwright"] = pw
    else:
        result["playwright"] = {"path": "playwright", "ok": False, "skipped": True}

    oc = None
    if not skip_openclaw:
        oc = path_openclaw(delay=openclaw_delay, url=url)
        if oc.get("ok") and not skip_ollama and oc.get("screenshot_path"):
            vis = ollama_extract(oc["screenshot_path"])
            oc["vision"] = {
                "ok": vis.get("ok"),
                "error": vis.get("error"),
                "model": vis.get("model"),
            }
            oc["observation"] = _merge_obs(
                vis.get("observation") or {},
                oc.get("observation") or {},
            )
            if vis.get("ok"):
                oc["observation"]["source"] = "ollama_vision"
        result["openclaw"] = oc
    else:
        result["openclaw"] = {"path": "openclaw", "ok": False, "skipped": True}

    pw_obs = (pw or {}).get("observation") if pw else None
    oc_obs = (oc or {}).get("observation") if oc else None
    result["compare"] = compare_observations(pw_obs, oc_obs)

    pw_ok = bool(pw and pw.get("ok"))
    oc_ok = bool(oc and oc.get("ok"))
    result["ok"] = pw_ok or oc_ok
    if not result["ok"]:
        errs = []
        if pw and pw.get("error"):
            errs.append(f"pw:{pw['error']}")
        if oc and oc.get("error"):
            errs.append(f"oc:{oc['error']}")
        result["error"] = ";".join(errs) or "both_paths_failed"

    out_path = PROOF_DIR / f"run_{run_id}.json"
    result["json_path"] = str(out_path)
    result["elapsed_s"] = round(time.time() - started, 3)
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # latest pointer
    (PROOF_DIR / "latest.json").write_text(
        json.dumps({"run_id": run_id, "json_path": str(out_path)}, indent=2),
        encoding="utf-8",
    )
    return result


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    ensure_proof_dir()
    files = sorted(PROOF_DIR.glob("run_*.json"), reverse=True)
    out: list[dict[str, Any]] = []
    for p in files[: max(1, limit)]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            out.append({"json_path": str(p), "error": str(e)})
            continue
        cmp_ = data.get("compare") or {}
        pw = data.get("playwright") or {}
        oc = data.get("openclaw") or {}
        out.append(
            {
                "run_id": data.get("run_id"),
                "created_at": data.get("created_at"),
                "ok": data.get("ok"),
                "match_ok": cmp_.get("match_ok"),
                "match_ratio": cmp_.get("match_ratio"),
                "playwright_ok": pw.get("ok"),
                "openclaw_ok": oc.get("ok"),
                "pw_shot": pw.get("screenshot_path"),
                "oc_shot": oc.get("screenshot_path"),
                "json_path": str(p),
                "error": data.get("error"),
            }
        )
    return out


def load_latest() -> dict[str, Any] | None:
    ensure_proof_dir()
    pointer = PROOF_DIR / "latest.json"
    if pointer.is_file():
        try:
            meta = json.loads(pointer.read_text(encoding="utf-8"))
            path = Path(meta.get("json_path") or "")
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    files = sorted(PROOF_DIR.glob("run_*.json"), reverse=True)
    if not files:
        return None
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except Exception:
        return None


def load_run(run_id: str) -> dict[str, Any] | None:
    ensure_proof_dir()
    p = PROOF_DIR / f"run_{run_id}.json"
    if not p.is_file():
        # allow bare filename
        p2 = PROOF_DIR / run_id
        if p2.is_file():
            p = p2
        else:
            return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def render_html_table(run: dict[str, Any] | None = None) -> str:
    """HTML table Field | Playwright | OpenClaw | Match."""
    run = run or load_latest() or {}
    cmp_ = run.get("compare") or {}
    fields = cmp_.get("fields") or {}
    rows = []
    for key in COMPARE_FIELDS:
        f = fields.get(key) or {}
        mk = f.get("match_ok")
        if mk is True:
            badge = '<span class="badge pass">match</span>'
        elif mk is False:
            badge = '<span class="badge fail">mismatch</span>'
        else:
            badge = '<span class="badge">n/a</span>'
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(key)}</code></td>"
            f"<td><code>{html.escape(str(f.get('playwright') if f.get('playwright') is not None else ''))}</code></td>"
            f"<td><code>{html.escape(str(f.get('openclaw') if f.get('openclaw') is not None else ''))}</code></td>"
            f"<td>{badge}</td>"
            "</tr>"
        )
    tbody = "".join(rows) or '<tr><td colspan="4">(no compare data)</td></tr>'
    pw = run.get("playwright") or {}
    oc = run.get("openclaw") or {}
    meta = (
        f"run_id={html.escape(str(run.get('run_id') or ''))} · "
        f"match_ok={html.escape(str(cmp_.get('match_ok')))} · "
        f"pw_ok={html.escape(str(pw.get('ok')))} · "
        f"oc_ok={html.escape(str(oc.get('ok')))} · "
        f"gate={GATE_POLICY}"
    )
    return f"""
<p class="meta">{meta}</p>
<table>
<thead><tr><th>Field</th><th>Playwright</th><th>OpenClaw</th><th>Match</th></tr></thead>
<tbody>{tbody}</tbody>
</table>
""".strip()


def hko_dashboard(limit: int = 15) -> dict[str, Any]:
    latest = load_latest()
    runs = list_runs(limit=limit)
    return {
        "gate": GATE_POLICY,
        "pipeline": PIPELINE_ID,
        "url": HKO_URL,
        "proof_dir": str(PROOF_DIR),
        "latest": latest,
        "runs": runs,
        "counts": {
            "listed": len(runs),
            "ok": sum(1 for r in runs if r.get("ok")),
            "match": sum(1 for r in runs if r.get("match_ok")),
        },
        "table_html": render_html_table(latest) if latest else None,
        "contracts": contracts_doc(),
    }


def _rel_proof_name(path: str | None) -> str | None:
    if not path:
        return None
    try:
        p = Path(path).resolve()
        base = PROOF_DIR.resolve()
        if base in p.parents or p.parent == base:
            return p.name
    except Exception:
        return None
    return None


def safe_proof_image(name: str) -> Path | None:
    """Resolve image only inside hko_proof/."""
    ensure_proof_dir()
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    p = (PROOF_DIR / name).resolve()
    try:
        if PROOF_DIR.resolve() not in p.parents and p.parent != PROOF_DIR.resolve():
            return None
    except Exception:
        return None
    if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        return p
    return None


def selftest() -> dict[str, Any]:
    """Offline unit tests for normalize/compare (no network)."""
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        if not cond:
            failures.append(f"{name}: {detail or 'failed'}")

    # temperature
    t = normalize_temperature("28°C")
    check("temp_c", t["temperature_c"] == 28.0, str(t))
    t2 = normalize_temperature("82°F")
    check("temp_f", t2["temperature_c"] is not None and 27.0 <= float(t2["temperature_c"]) <= 28.5, str(t2))
    t3 = normalize_temperature(None)
    check("temp_none", t3["temperature_c"] is None)

    # humidity
    h = normalize_humidity("Humidity 75%")
    check("hum", h["humidity"] == 75, str(h))

    # region
    r = normalize_region("Hong Kong Observatory")
    check("region", r["norm"] == "Hong Kong", str(r))

    # time
    tm = normalize_time("  3:00 pm ")
    check("time", tm["norm"] == "3:00 PM", str(tm))

    # weather
    w = normalize_weather_text("  Cloudy  ")
    check("weather", w["norm"] == "cloudy", str(w))

    # scrape
    sample = (
        "Hong Kong Observatory\n"
        "Air temperature 29°C\n"
        "Relative Humidity 80%\n"
        "Updated at 15:30\n"
        "Mainly Cloudy\n"
    )
    sc = scrape_dom_text(sample)
    check("scrape_temp", sc.get("temperature_c") == 29.0, str(sc))
    check("scrape_hum", sc.get("humidity") == 80, str(sc))
    check("scrape_region", sc.get("region") == "Hong Kong", str(sc))

    # compare match
    a = {
        "observed_time": "15:30",
        "region": "Hong Kong",
        "temperature_c": 29,
        "temperature_text": "29°C",
        "humidity": 80,
        "weather_text": "Cloudy",
    }
    b = {
        "observed_time": "at 15:30",
        "region": "HK",
        "temperature_c": 29.0,
        "temperature_text": "29 °C",
        "humidity": 81,
        "weather_text": "Mainly Cloudy",
    }
    cmp_ = compare_observations(a, b)
    check("compare_overall", cmp_["match_ok"] is True, str(cmp_))
    check(
        "compare_temp",
        (cmp_["fields"]["temperature_c"]["match_ok"] is True),
        str(cmp_["fields"]["temperature_c"]),
    )
    check(
        "compare_region",
        cmp_["fields"]["region"]["match_ok"] is True,
        str(cmp_["fields"]["region"]),
    )

    # compare mismatch
    c = dict(a)
    c["temperature_c"] = 10
    cmp2 = compare_observations(a, c)
    check(
        "compare_mismatch_temp",
        cmp2["fields"]["temperature_c"]["match_ok"] is False,
        str(cmp2["fields"]["temperature_c"]),
    )

    # normalize_observation pure
    n1 = normalize_observation(a)
    n2 = normalize_observation(a)
    check("normalize_pure", n1["norms"] == n2["norms"], str(n1))

    # graceful playwright missing import path still returns dict shape
    # (do not call real playwright here)
    check("contracts", contracts_doc()["gate"] == "never")

    ok = len(failures) == 0
    return {
        "ok": ok,
        "failed": failures,
        "n_failed": len(failures),
        "gate": GATE_POLICY,
        "pipeline": PIPELINE_ID,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HKO dual-path weather proof (gate=never)")
    ap.add_argument(
        "cmd",
        nargs="?",
        default="contracts",
        choices=["run", "list", "latest", "selftest", "contracts", "html"],
    )
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--delay", type=float, default=4.0, help="OpenClaw open delay seconds")
    ap.add_argument("--skip-openclaw", action="store_true")
    ap.add_argument("--skip-playwright", action="store_true")
    ap.add_argument("--skip-ollama", action="store_true")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--url", default=HKO_URL)
    args = ap.parse_args(argv)

    if args.cmd == "contracts":
        print(json.dumps(contracts_doc(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "selftest":
        r = selftest()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1
    if args.cmd == "list":
        print(json.dumps(list_runs(limit=args.limit), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "latest":
        data = load_latest()
        print(json.dumps(data if data is not None else {"error": "no_runs"}, ensure_ascii=False, indent=2))
        return 0 if data else 1
    if args.cmd == "html":
        print(render_html_table(load_latest()))
        return 0
    if args.cmd == "run":
        r = run_proof(
            headed=bool(args.headed),
            openclaw_delay=float(args.delay),
            skip_openclaw=bool(args.skip_openclaw),
            skip_playwright=bool(args.skip_playwright),
            skip_ollama=bool(args.skip_ollama),
            url=str(args.url),
        )
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 2
    ap.error(f"unknown cmd {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
