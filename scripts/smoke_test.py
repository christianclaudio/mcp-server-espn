#!/usr/bin/env python3
"""Execute end-to-end JSON-RPC initialization handshake over stdio."""

import json
import subprocess
import sys


def run_smoke_test() -> int:
    print("[*] Executing stdio JSON-RPC initialization handshake...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "template_mcp.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke-test-client", "version": "1.0.0"},
        },
    }

    try:
        stdout_data, stderr_data = proc.communicate(
            input=json.dumps(init_payload) + "\n", timeout=10
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        print("[x] Error: Smoke test timed out!")
        return 1

    lines = [line.strip() for line in stdout_data.split("\n") if line.strip()]
    if not lines:
        print(f"[x] Error: No stdio response received. Stderr: {stderr_data}")
        return 1

    try:
        response = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        print(f"[x] Error: Invalid JSON response: {lines[0]} ({exc})")
        return 1

    if "result" not in response or "protocolVersion" not in response.get("result", {}):
        print(f"[x] Error: Invalid handshake response structure: {response}")
        return 1

    print(f"[✓] Stdio handshake successful: protocol {response['result']['protocolVersion']}")
    return 0


if __name__ == "__main__":
    sys.exit(run_smoke_test())
