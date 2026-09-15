"""Inspect OpenClaw Companion screen.snapshot result format."""
from mcp_client import McpClient, McpConfig, extract_image_bytes
import json

c = McpClient(McpConfig.from_env())
r = c.call_tool('screen.snapshot', {})
print('result type:', type(r))
if isinstance(r, dict):
    print('keys:', list(r.keys()))
    print('value types:', json.dumps({k: type(v).__name__ for k, v in r.items()}, indent=2))
    if 'content' in r and isinstance(r['content'], list):
        for i, item in enumerate(r['content']):
            print(f"content[{i}] type={item.get('type')} text preview=", (item.get('text') or '')[:200])
b, p = extract_image_bytes(r)
print('extracted bytes:', len(b) if b else None, 'path:', p)
