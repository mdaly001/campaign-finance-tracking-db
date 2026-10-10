#!/usr/bin/env python3
"""Minimal MCP Streamable-HTTP probe client (stdlib only).

Calls a tool on a running CFDB MCP server and prints the parsed JSON result,
so issues can be reproduced and fixes verified against a live stack without
wiring up a full MCP client library.

    # list tools
    python3 scripts/mcp_probe.py --url http://127.0.0.1:9528/mcp --list

    # call a tool with JSON args
    python3 scripts/mcp_probe.py --url http://127.0.0.1:9528/mcp \
        total_expenditures '{"committee_id": "1489398", "cycle": 2026}'

    # call with keyword args
    python3 scripts/mcp_probe.py total_expenditures committee_id=1489398 cycle=2026

Exit code is 0 on a successful JSON-RPC response (even if the tool returned
an empty result), 1 on transport/protocol error, 2 on bad usage.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2024-11-05"


def _post(url: str, payload: dict, session_id: str | None = None, timeout: int = 120):
    headers = {
        "Content-Type": "application/json",
        # Streamable HTTP servers may answer with SSE or JSON; accept both.
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
        sid = resp.headers.get("Mcp-Session-Id")
        ctype = (resp.headers.get("Content-Type") or "").lower()
    return body, sid, ctype


def _parse_body(body: str, ctype: str) -> dict:
    """Return the JSON-RPC object from a JSON or SSE response body."""
    body = body.strip()
    if not body:
        return {}
    if "text/event-stream" in ctype or body.startswith("event:") or body.startswith("data:"):
        # SSE: the JSON-RPC message is carried on `data:` lines.
        for line in body.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk and chunk != "[DONE]":
                    return json.loads(chunk)
        raise ValueError(f"no data: payload in SSE body: {body[:200]}")
    return json.loads(body)


def main() -> int:
    ap = argparse.ArgumentParser(description="Call a CFDB MCP tool over Streamable HTTP.")
    ap.add_argument("--url", default="http://127.0.0.1:9528/mcp", help="MCP endpoint")
    ap.add_argument("--list", action="store_true", help="List available tools and exit")
    ap.add_argument("tool", nargs="?", help="Tool name to call")
    ap.add_argument("args", nargs="*", help='JSON object of args, or key=value pairs')
    ap.add_argument("--raw", action="store_true", help="Print the full JSON-RPC response")
    opts = ap.parse_args()

    # 1. initialize (also captures the session id)
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "cfdb-probe", "version": "1.0"},
        },
    }
    try:
        body, sid, ctype = _post(opts.url, init)
    except urllib.error.URLError as e:
        print(f"[probe] connect failed: {e}", file=sys.stderr)
        return 1
    init_resp = _parse_body(body, ctype)
    if "error" in init_resp:
        print(f"[probe] initialize error: {json.dumps(init_resp['error'])}", file=sys.stderr)
        return 1

    # 2. initialized notification (required before tools/call)
    try:
        _post(opts.url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    except urllib.error.HTTPError as e:
        # Some servers answer 202/204 with no body; only surface real errors.
        if e.code >= 400:
            print(f"[probe] initialized notify: HTTP {e.code}", file=sys.stderr)
            return 1

    if opts.list:
        body, _, ctype = _post(
            opts.url, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, sid
        )
        resp = _parse_body(body, ctype)
        tools = resp.get("result", {}).get("tools", [])
        for t in tools:
            print(f"{t['name']}: {(t.get('description') or '')[:90]}")
        print(f"\n[{len(tools)} tools]")
        return 0

    if not opts.tool:
        print("[probe] need a tool name (or --list)", file=sys.stderr)
        return 2

    # 3. assemble args: one JSON object, or key=value pairs
    args: dict = {}
    if opts.args:
        if len(opts.args) == 1 and opts.args[0].lstrip().startswith("{"):
            args = json.loads(opts.args[0])
        else:
            for kv in opts.args:
                if "=" not in kv:
                    print(f"[probe] bad arg (want key=value): {kv}", file=sys.stderr)
                    return 2
                k, v = kv.split("=", 1)
                try:
                    args[k] = json.loads(v)
                except json.JSONDecodeError:
                    args[k] = v

    call = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": opts.tool, "arguments": args},
    }
    try:
        body, _, ctype = _post(opts.url, call, sid)
    except urllib.error.HTTPError as e:
        print(f"[probe] tools/call HTTP {e.code}: {e.read().decode()[:400]}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"[probe] tools/call failed: {e}", file=sys.stderr)
        return 1

    resp = _parse_body(body, ctype)
    if opts.raw:
        print(json.dumps(resp, indent=2))
        return 0
    if "error" in resp:
        print(json.dumps(resp["error"], indent=2), file=sys.stderr)
        return 1

    result = resp.get("result", {})
    # MCP CallToolResult: prefer structuredContent, else parse text content as JSON.
    if result.get("structuredContent") is not None:
        print(json.dumps(result["structuredContent"], indent=2))
        return 0
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    joined = "\n".join(texts)
    try:
        print(json.dumps(json.loads(joined), indent=2))
    except json.JSONDecodeError:
        print(joined)
    if result.get("isError"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
