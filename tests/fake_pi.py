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
    if kind in {"prompt", "follow_up", "steer", "abort"}:
        if "REJECT" in message:
            emit({"type": "response", "id": request_id, "success": False, "error": "fake rejection"})
            continue
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
    elif kind == "abort":
        emit({"type": "agent_settled"})
