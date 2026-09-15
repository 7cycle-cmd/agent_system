"""Minimal OpenClaw Companion Local MCP client (Windows loopback).

Enable Local MCP in Companion → Permissions, then set:
  OPENCLAW_MCP_URL=http://127.0.0.1:<port>/...
  OPENCLAW_MCP_TOKEN=<bearer>

Bridge must run on Windows — WSL 127.0.0.1 is not the Companion host.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _load_dotenv(path: str | None = None) -> None:
    """Tiny .env loader (no dependency). Does not override existing env."""
    base = os.path.dirname(os.path.abspath(__file__))
    env_path = path or os.path.join(base, ".env")
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


@dataclass
class McpConfig:
    url: str
    token: str
    timeout_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "McpConfig":
        url = (os.environ.get("OPENCLAW_MCP_URL") or "").strip()
        token = (os.environ.get("OPENCLAW_MCP_TOKEN") or "").strip()
        timeout = float(os.environ.get("OPENCLAW_MCP_TIMEOUT", "60"))
        if not url:
            raise ValueError(
                "OPENCLAW_MCP_URL is not set. Enable Companion Local MCP and copy the endpoint."
            )
        if not token:
            raise ValueError(
                "OPENCLAW_MCP_TOKEN is not set. Copy the bearer token from Companion Local MCP."
            )
        return cls(url=url, token=token, timeout_seconds=timeout)


@dataclass
class McpClient:
    config: McpConfig
    _initialized: bool = False
    _tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    _id: int = 0
    _session_id: str | None = None

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _headers(self) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.config.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.config.url,
            data=data,
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                if sid:
                    self._session_id = sid
                body = resp.read().decode("utf-8", errors="replace")
                content_type = (resp.headers.get("Content-Type") or "").lower()
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            raise RuntimeError(f"MCP HTTP {e.code}: {err_body or e.reason}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"MCP connection failed: {e.reason}") from e

        return self._parse_response(body, content_type)

    @staticmethod
    def _parse_response(body: str, content_type: str) -> dict[str, Any]:
        body = body.strip()
        if not body:
            return {}
        # Streamable HTTP may return SSE: lines of "data: {...}"
        if "text/event-stream" in content_type or body.startswith("event:") or "\ndata:" in body or body.startswith("data:"):
            last_obj: dict[str, Any] | None = None
            for line in body.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    last_obj = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
            if last_obj is None:
                raise RuntimeError(f"MCP SSE response had no JSON data: {body[:300]}")
            return last_obj
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"MCP non-JSON response: {body[:300]}") from e

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        resp = self._post(payload)
        if "error" in resp and resp["error"]:
            raise RuntimeError(f"MCP error: {resp['error']}")
        return resp.get("result")

    def initialize(self) -> Any:
        result = self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agent-system-bridge", "version": "0.1.0"},
            },
        )
        # Best-effort notifications/initialized (some servers require it)
        try:
            note = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
            data = json.dumps(note).encode("utf-8")
            req = urllib.request.Request(
                self.config.url,
                data=data,
                headers=self._headers(),
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as resp:
                resp.read()
        except Exception:
            pass
        self._initialized = True
        return result

    def ensure_ready(self) -> None:
        if not self._initialized:
            self.initialize()
            self.refresh_tools()

    def refresh_tools(self) -> dict[str, dict[str, Any]]:
        result = self.request("tools/list", {}) or {}
        tools = result.get("tools") or []
        self._tools = {}
        for t in tools:
            name = t.get("name")
            if name:
                self._tools[name] = t
        return self._tools

    def list_tool_names(self) -> list[str]:
        self.ensure_ready()
        return sorted(self._tools.keys())

    def resolve_tool(self, *candidates: str) -> str | None:
        """Match exact name or common dotted/underscored variants."""
        self.ensure_ready()
        names = self._tools
        for c in candidates:
            if c in names:
                return c
        lowered = {k.lower(): k for k in names}
        for c in candidates:
            if c.lower() in lowered:
                return lowered[c.lower()]
        # fuzzy: endswith
        for c in candidates:
            needle = c.lower().replace("_", ".")
            for k, orig in lowered.items():
                if k == needle or k.endswith("." + needle) or k.endswith("_" + needle.replace(".", "_")):
                    return orig
        return None

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.ensure_ready()
        return self.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )

    def notify(self, title: str, body: str) -> Any:
        tool = self.resolve_tool("system.notify", "notify", "SystemNotify")
        if not tool:
            raise RuntimeError(
                f"system.notify not in MCP tools: {self.list_tool_names()}"
            )
        # Try common argument shapes
        attempts = [
            {"title": title, "body": body},
            {"title": title, "message": body},
            {"message": f"{title}\n{body}"},
            {"text": f"{title}\n{body}"},
        ]
        last_err: Exception | None = None
        for args in attempts:
            try:
                return self.call_tool(tool, args)
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(f"system.notify failed: {last_err}")

    def screen_snapshot(self, arguments: dict[str, Any] | None = None) -> Any:
        tool = self.resolve_tool("screen.snapshot", "screen_snapshot", "ScreenSnapshot")
        if not tool:
            raise RuntimeError(
                f"screen.snapshot not in MCP tools: {self.list_tool_names()}"
            )
        args = arguments or {"screenIndex": 0, "maxWidth": 1280}
        return self.call_tool(tool, args)


def extract_image_bytes(tool_result: Any) -> tuple[bytes | None, str | None]:
    """Best-effort extract PNG/JPEG bytes from MCP tool result structures.

    Companion screen.snapshot often returns:
      {"content":[{"type":"text","text":"{\\"format\\":\\"png\\",\\"base64\\":\\"...\\"}"}], ...}
    """
    import base64
    import json
    import re

    def _looks_like_image(b: bytes | None) -> bool:
        if not b or len(b) < 24:
            return False
        # PNG / JPEG / GIF / WEBP
        if b.startswith(b"\x89PNG\r\n\x1a\n"):
            return True
        if b[:3] == b"\xff\xd8\xff":
            return True
        if b[:6] in (b"GIF87a", b"GIF89a"):
            return True
        if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
            return True
        return False

    def from_b64(s: str) -> bytes | None:
        s = s.strip()
        m = re.match(r"^data:image/[^;]+;base64,(.+)$", s, re.DOTALL)
        if m:
            s = m.group(1)
        # whitespace/newlines in b64
        s = re.sub(r"\s+", "", s)
        try:
            raw = base64.b64decode(s, validate=False)
        except Exception:
            return None
        return raw if _looks_like_image(raw) else None

    def from_text_blob(text: str) -> tuple[bytes | None, str | None]:
        if not text:
            return None, None
        if os.path.isfile(text):
            with open(text, "rb") as f:
                data = f.read()
            return (data, text) if _looks_like_image(data) else (None, text)
        # Companion: JSON string with base64 field
        t = text.strip()
        if t.startswith("{") and "base64" in t:
            try:
                obj = json.loads(t)
            except Exception:
                obj = None
            if isinstance(obj, dict):
                b64 = obj.get("base64") or obj.get("data") or obj.get("png")
                if isinstance(b64, str):
                    b = from_b64(b64)
                    if b:
                        return b, None
                # nested
                nested = extract_image_bytes(obj)
                if nested[0]:
                    return nested
        b = from_b64(t)
        if b:
            return b, None
        return None, None

    if tool_result is None:
        return None, None
    if isinstance(tool_result, (bytes, bytearray)):
        b = bytes(tool_result)
        return (b, None) if _looks_like_image(b) else (None, None)
    if isinstance(tool_result, str):
        return from_text_blob(tool_result)

    if isinstance(tool_result, dict):
        # Direct base64 fields first (including parsed snapshot JSON)
        for key in ("base64", "data", "image", "png"):
            v = tool_result.get(key)
            if isinstance(v, str):
                b = from_b64(v)
                if b:
                    return b, None
            if isinstance(v, (bytes, bytearray)) and _looks_like_image(bytes(v)):
                return bytes(v), None
        for key in ("path", "file", "filename", "imagePath"):
            p = tool_result.get(key)
            if isinstance(p, str) and os.path.isfile(p):
                with open(p, "rb") as f:
                    data = f.read()
                if _looks_like_image(data):
                    return data, p
        content = tool_result.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "image":
                    data = item.get("data") or item.get("image") or item.get("base64")
                    if isinstance(data, str):
                        b = from_b64(data)
                        if b:
                            return b, None
                if item.get("type") == "text" and isinstance(item.get("text"), str):
                    b, p = from_text_blob(item["text"])
                    if b:
                        return b, p
                # recursive item
                b, p = extract_image_bytes(item)
                if b:
                    return b, p
        for nest_key in ("result", "output", "snapshot", "image"):
            if nest_key in tool_result and nest_key not in ("image",):
                b, p = extract_image_bytes(tool_result[nest_key])
                if b:
                    return b, p
            elif nest_key == "image" and isinstance(tool_result.get("image"), dict):
                b, p = extract_image_bytes(tool_result["image"])
                if b:
                    return b, p
    if isinstance(tool_result, list):
        for item in tool_result:
            b, p = extract_image_bytes(item)
            if b:
                return b, p
    return None, None


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="OpenClaw Local MCP smoke test")
    parser.add_argument("--ping", action="store_true", help="initialize + tools/list")
    parser.add_argument("--notify", metavar="MSG", help="send system.notify test")
    args = parser.parse_args()

    try:
        cfg = McpConfig.from_env()
    except ValueError as e:
        print(f"CONFIG: {e}")
        return 2

    client = McpClient(cfg)
    try:
        client.ensure_ready()
        names = client.list_tool_names()
        print("tools:", names)
        if args.notify:
            r = client.notify("agent_system bridge", args.notify)
            print("notify result:", r)
        elif not args.ping:
            print("OK (use --ping or --notify)")
        return 0
    except Exception as e:
        print(f"FAIL: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
