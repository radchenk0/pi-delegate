#!/usr/bin/env python3
"""Deterministic JSONL RPC stand-in used by the integration tests."""

import json
import os
import subprocess
import sys
import time


def emit(value, split=False):
    data = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
    if split and len(data) > 5:
        os.write(sys.stdout.fileno(), data[:5])
        time.sleep(0.01)
        os.write(sys.stdout.fileno(), data[5:])
    else:
        os.write(sys.stdout.fileno(), data)


emit({"type": "session", "sessionId": "fake-session"})
for raw in sys.stdin.buffer:
    command = json.loads(raw)
    request_id = command.get("id")
    kind = command.get("type")
    message = command.get("message", "")
    if kind == "get_state":
        emit({
            "type": "response",
            "id": request_id,
            "command": "get_state",
            "success": True,
            "data": {
                "sessionId": "fake-session",
                "model": {"provider": "fake-provider", "id": "fake-model"},
            },
        })
        continue
    if kind == "extension_ui_response":
        # Only settle the UI flow when the exact pending dialog id is answered,
        # mirroring how real Pi ignores mismatched response ids.
        if command.get("id") == "ui-1" and command.get("confirmed"):
            emit({"type": "turn_start"})
            emit({
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done: ui confirmed"}],
                    "provider": "fake-provider",
                    "model": "fake-model",
                    "usage": {"input": 3, "output": 2},
                    "stopReason": "stop",
                },
            })
            emit({"type": "agent_settled"})
        continue
    if kind in {"prompt", "follow_up", "steer", "abort"}:
        if "REJECT" in message:
            emit({"type": "response", "id": request_id, "success": False, "error": "fake rejection"})
            continue
        if kind == "abort":
            # Per the Pi RPC docs, the abort response arrives only after the
            # agent has settled; emit in that order.
            emit({"type": "agent_settled"})
        emit({"type": "response", "id": request_id, "success": True, "data": {"sessionId": "fake-session"}})
    if "DIE" in message:
        raise SystemExit(23)
    if "SPAWN" in message:
        child = subprocess.Popen([
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        ])
        with open("descendant.pid", "w", encoding="utf-8") as handle:
            handle.write(str(child.pid))
    if kind in {"prompt", "follow_up"}:
        emit({"type": "agent_start"})
        if "ORPHAN" in message:
            time.sleep(30)
            continue
        if "HOLD" in message:
            continue
        if "UI" in message:
            # One fire-and-forget notification (must not block or error) and one
            # blocking confirm dialog that waits for extension_ui_response.
            emit({"type": "extension_ui_request", "id": "ff-1", "method": "notify"})
            emit({"type": "extension_ui_request", "id": "ui-1", "method": "confirm"})
            continue
        emit({"type": "turn_start"})
        emit({
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": f"done: {message}"}],
                "provider": "fake-provider",
                "model": "fake-model",
                "usage": {"input": 3, "output": 2},
                "stopReason": "stop",
            },
        }, split="UTF8" in message)
        emit({"type": "agent_settled"})
