#!/usr/bin/env python3
"""Run resumable Pi coding-agent workers over the JSONL RPC protocol."""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import select
import selectors
import shutil
import signal
import subprocess
import sys
import time
from typing import NoReturn
import uuid


SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
RUN_ID_RE = re.compile(r"^run-[0-9]{6}$")
STATE_FLUSH_INTERVAL = 0.5
PROCESS_GRACE_SECONDS = 2.0
RESULT_SCHEMA = "pi-delegate.result.v1"
# Extension UI methods per the Pi RPC docs: dialog methods block the agent
# until an extension_ui_response arrives; the rest are fire-and-forget.
UI_DIALOG_METHODS = {"select", "confirm", "input", "editor"}
UI_FIRE_AND_FORGET_METHODS = {"notify", "setStatus", "setWidget", "setTitle", "set_editor_text"}
# Dialog fields an orchestrator needs to answer without reading events.jsonl.
UI_DIALOG_FIELDS = ("title", "message", "options", "placeholder", "timeout")


def die(message: str, code: int = 2) -> NoReturn:
    print(f"pi-delegate: {message}", file=sys.stderr)
    raise SystemExit(code)


def validate_slug(slug: str) -> str:
    if not SLUG_RE.fullmatch(slug):
        die("slug must be 1-64 safe characters: letters, digits, dot, underscore, hyphen")
    return slug


def paths(cwd: Path, slug: str) -> dict[str, Path]:
    runtime = cwd / ".pi" / "workers" / slug
    return {
        "cwd": cwd,
        "runtime": runtime,
        "runs": runtime / "runs",
        "session": cwd / ".pi" / "sessions" / f"{slug}.jsonl",
        "lock": cwd / ".pi" / "sessions" / f"{slug}.lock",
        "logs": cwd / ".pi" / "logs" / slug,
        "events": runtime / "events.jsonl",
        "stderr": runtime / "stderr.log",
        "state": runtime / "state.json",
        "result": runtime / "result.md",
        "fifo": runtime / "control.fifo",
        "pid": runtime / "supervisor.pid",
    }


def pending_ui_record(event: dict, request_id: str, method: str) -> dict:
    record = {"id": request_id, "method": method}
    for field in UI_DIALOG_FIELDS:
        value = event.get(field)
        if isinstance(value, str):
            record[field] = value
        elif field == "options" and isinstance(value, list):
            record[field] = [str(option) for option in value]
        elif field == "timeout" and isinstance(value, (int, float)) and not isinstance(value, bool):
            record[field] = value
    return record


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    state["updatedAt"] = time.time()
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    write_private_text(temp, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    os.replace(temp, path)


def atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    write_private_text(temp, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temp, path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_private_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value)


def command_output(argv: list[str], cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.rstrip("\n") if completed.returncode == 0 else None


def porcelain_path(line: str) -> str:
    value = line[3:] if len(line) >= 4 else line
    if " -> " in value:
        value = value.split(" -> ", 1)[1]
    return value.strip('"')


def git_snapshot(cwd: Path) -> dict | None:
    root = command_output(["git", "rev-parse", "--show-toplevel"], cwd)
    if root is None:
        return None
    degraded: list[str] = []

    def git_output(label: str, argv: list[str]) -> str:
        text = command_output(argv, cwd)
        if text is None:
            degraded.append(f"{label} failed or timed out")
            return ""
        return text

    porcelain_text = git_output(
        "git status --porcelain=v1 --untracked-files=all",
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
    )
    try:
        relative_cwd = cwd.resolve().relative_to(Path(root).resolve())
    except ValueError:
        relative_cwd = Path()
    internal_root = (relative_cwd / ".pi").as_posix()

    def external(path: str) -> bool:
        return path != internal_root and not path.startswith(internal_root + "/")

    porcelain = [
        line for line in porcelain_text.splitlines()
        if line and external(porcelain_path(line))
    ]
    staged_text = git_output("git diff --cached --name-only", ["git", "diff", "--cached", "--name-only"])
    unstaged_text = git_output("git diff --name-only", ["git", "diff", "--name-only"])
    stash_text = command_output(["git", "stash", "list", "--format=%gd %H"], cwd)
    if stash_text is None:
        degraded.append("git stash list failed or timed out")
        stash_text = ""
    return {
        "root": root,
        "head": git_output("git rev-parse HEAD", ["git", "rev-parse", "HEAD"]),
        "porcelain": porcelain,
        "paths": sorted({porcelain_path(line) for line in porcelain}),
        "staged": sorted(path for path in staged_text.splitlines() if path and external(path)),
        "unstaged": sorted(path for path in unstaged_text.splitlines() if path and external(path)),
        "untracked": sorted(porcelain_path(line) for line in porcelain if line.startswith("?? ")),
        "stash": [line for line in stash_text.splitlines() if line],
        "degraded": degraded,
    }


def merge_numeric(target: dict, source: dict) -> None:
    for key, value in source.items():
        if isinstance(value, dict):
            child = target.setdefault(key, {})
            if isinstance(child, dict):
                merge_numeric(child, value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            target[key] = target.get(key, 0) + value


def stderr_tail(path: Path, limit: int = 20) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []
    return [line for line in lines if line.strip()][-limit:]


def pid_alive(pid: object) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True


def group_alive(pgid: object) -> bool:
    try:
        os.killpg(int(pgid), 0)
        return True
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True


def process_command(pid: object) -> str:
    if not pid_alive(pid):
        return ""
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except PermissionError:
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def pi_process_matches(state: dict) -> bool:
    command = process_command(state.get("piPid"))
    session = state.get("sessionFile")
    return bool(command and session and "--mode rpc" in command and str(session) in command)


def effective_state(p: dict[str, Path], persist: bool = True) -> dict:
    state = load_state(p["state"])
    if not state:
        return {}
    supervisor_live = pid_alive(state.get("pid"))
    # Keep ordinary status/result/list usable inside a restricted sandbox.
    # Process identity inspection is reserved for the privileged recover path.
    pi_live = not supervisor_live and pid_alive(state.get("piPid"))
    previous = state.get("status")
    finalized = False
    if not supervisor_live and pi_live:
        state["status"] = "orphaned"
        state["lastError"] = "supervisor is dead while its Pi process is still alive"
        if persist and state.get("openRun"):
            finalized = finalize_run(
                state,
                p,
                "orphaned",
                error=state["lastError"],
                abort_origin="supervisor-loss",
                worker_alive=True,
            ) is not None
    elif not supervisor_live and previous not in {"stopped", "dead"}:
        state["status"] = "dead"
        state["lastError"] = state.get("lastError") or "supervisor process is not alive"
        if persist and state.get("openRun"):
            finalized = finalize_run(
                state,
                p,
                "aborted",
                error=state["lastError"],
                abort_origin="supervisor-loss",
                worker_alive=False,
            ) is not None
    if persist and (state.get("status") != previous or finalized):
        save_state(p["state"], state)
    return state


def acquire_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(lock_path.parent, 0o700)
    handle = lock_path.open("a+", encoding="utf-8")
    os.fchmod(handle.fileno(), 0o600)
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        die(f"session is locked by another supervisor: {lock_path}")
    return handle


def assistant_text(event: dict) -> str | None:
    if event.get("type") != "message_end":
        return None
    message = event.get("message") or {}
    if message.get("role") != "assistant":
        return None
    parts = [
        item.get("text", "")
        for item in message.get("content") or []
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    text = "\n".join(part for part in parts if part).strip()
    return text or None


def count_event_lines(path: Path) -> int:
    try:
        with path.open("rb") as handle:
            return sum(1 for _line in handle)
    except FileNotFoundError:
        return 0


def open_run(state: dict, p: dict[str, Path], prompt: str, request_id: str) -> None:
    if state.get("openRun"):
        raise RuntimeError("cannot open a second run while one is active")
    sequence = int(state.get("runSequence", 0)) + 1
    run_id = f"run-{sequence:06d}"
    before = git_snapshot(p["cwd"])
    run_dir = p["runs"] / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    write_private_text(run_dir / "brief.md", prompt.rstrip() + "\n")
    if before and before.get("porcelain"):
        print(
            f"warning: worker '{state['slug']}' started with a dirty Git tree; change attribution will be ambiguous",
            file=sys.stderr,
            flush=True,
        )
    state["runSequence"] = sequence
    state["openRun"] = {
        "runId": run_id,
        "openedAt": utc_now(),
        "openedAtEpoch": time.time(),
        "settleTarget": int(state.get("settleGeneration", 0)) + 1,
        "initialRequestId": request_id,
        "eventStartLine": int(state.get("eventLineCount", 0)) + 1,
        "queuedFollowUps": 0,
        "steers": 0,
        "abortRequested": False,
        "finalMessage": "",
        "usage": {},
        "stopReason": None,
        "actualProvider": None,
        "actualModel": None,
        "gitBefore": before,
        "rpcErrors": [],
    }
    state["expectedSettleGeneration"] = state["openRun"]["settleTarget"]
    state["settledResultFresh"] = False


def changed_paths(before: dict | None, after: dict | None) -> list[str] | None:
    if before is None or after is None:
        return None
    before_lines = {porcelain_path(line): line[:2] for line in before.get("porcelain", [])}
    after_lines = {porcelain_path(line): line[:2] for line in after.get("porcelain", [])}
    return sorted(
        path for path in set(before_lines) | set(after_lines)
        if before_lines.get(path) != after_lines.get(path)
    )


def finalize_run(
    state: dict,
    p: dict[str, Path],
    status: str,
    *,
    error: str | None = None,
    abort_origin: str | None = None,
    worker_alive: bool = True,
    exit_code: int | None = None,
    signal_name: str | None = None,
) -> dict | None:
    run = state.get("openRun")
    if not isinstance(run, dict):
        return None
    run_id = run.get("runId")
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        state["lastError"] = "refusing unsafe run identifier in state"
        state["openRun"] = None
        return None
    after = git_snapshot(p["cwd"])
    before = run.get("gitBefore")
    before_paths = set((before or {}).get("paths", []))
    after_paths = set((after or {}).get("paths", []))
    before_staged = set((before or {}).get("staged", []))
    after_staged = set((after or {}).get("staged", []))
    finished_at = utc_now()
    result = {
        "schema": RESULT_SCHEMA,
        "slug": state.get("slug"),
        "runId": run_id,
        "status": status,
        "abortOrigin": abort_origin,
        "workerAlive": worker_alive,
        "startedAt": run.get("openedAt"),
        "finishedAt": finished_at,
        "durationMs": max(0, int((time.time() - float(run.get("openedAtEpoch", time.time()))) * 1000)),
        "sessionFile": state.get("sessionFile"),
        "sessionId": state.get("sessionId"),
        "requestedModel": state.get("requestedModel"),
        "actualProvider": run.get("actualProvider") or state.get("actualProvider"),
        "actualModel": run.get("actualModel") or state.get("actualModel"),
        "usage": run.get("usage") or None,
        "stopReason": run.get("stopReason"),
        "finalMessage": run.get("finalMessage") or "",
        "queuedFollowUps": run.get("queuedFollowUps", 0),
        "steers": run.get("steers", 0),
        "gitBefore": before,
        "gitAfter": after,
        "touchedFiles": changed_paths(before, after),
        "gitEvidence": {
            "attribution": (
                "unavailable"
                if before is None or after is None
                else "degraded"
                if (before.get("degraded") or after.get("degraded"))
                else "ambiguous"
                if before.get("porcelain")
                else "clean-baseline"
            ),
            "snapshotDegraded": sorted(set((before or {}).get("degraded", [])) | set((after or {}).get("degraded", []))),
            "headMoved": bool(before and after and before.get("head") != after.get("head")),
            "stashChanged": bool(before and after and before.get("stash") != after.get("stash")),
            "stagedChangesAppeared": sorted(after_staged - before_staged),
            "dirtyPathsVanished": sorted(before_paths - after_paths),
        },
        "eventStartLine": run.get("eventStartLine"),
        "eventEndLine": state.get("eventLineCount"),
        "eventsPath": state.get("eventsFile"),
        "stderrPath": str(p["stderr"]),
        "stderrTail": stderr_tail(p["stderr"]),
        "rpcErrors": run.get("rpcErrors", []),
        "exitCode": exit_code,
        "signal": signal_name,
        "error": error,
    }
    result_path = p["runs"] / run_id / "result.json"
    atomic_write_json(result_path, result)
    state["lastRunId"] = run_id
    state["lastRunStatus"] = status
    state["lastResultPath"] = str(result_path)
    state["lastError"] = error
    state["settledResultFresh"] = status == "completed" and bool(result["finalMessage"])
    if status != "completed":
        state["expectedSettleGeneration"] = state.get("settleGeneration", 0)
    state["openRun"] = None
    return result


def write_rpc(child: subprocess.Popen, command: dict) -> None:
    if child.stdin is None:
        raise RuntimeError("Pi stdin is unavailable")
    child.stdin.write((json.dumps(command, ensure_ascii=False) + "\n").encode("utf-8"))
    child.stdin.flush()


def process_pi_event(line: str, events, state: dict, p: dict[str, Path], slug: str) -> bool:
    events.write(line + "\n")
    events.flush()
    state["eventLineCount"] = int(state.get("eventLineCount", 0)) + 1
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return False

    force_save = False
    event_type = event.get("type")
    state["lastEvent"] = event_type
    run = state.get("openRun")
    if event_type == "session":
        state["sessionId"] = event.get("sessionId") or event.get("id") or state.get("sessionId")
        force_save = True
    if event_type == "agent_start":
        state["status"] = "running"
        force_save = True
        print(f"worker '{slug}' running", flush=True)
    elif event_type == "turn_start":
        state["turnSequence"] += 1
        state["resultFresh"] = False
        state["settledResultFresh"] = False
        force_save = True
    elif event_type == "agent_settled":
        state["settleGeneration"] += 1
        state["status"] = "idle"
        state["queuedFollowUps"] = 0
        state["pendingUi"] = None
        if isinstance(run, dict) and state["settleGeneration"] >= int(run.get("settleTarget", 0)):
            final_status = "aborted" if run.get("abortRequested") else "completed"
            final_error = None
            if final_status == "completed" and not run.get("finalMessage"):
                final_status = "failed"
                final_error = "worker settled without a fresh assistant result"
            finalize_run(
                state,
                p,
                final_status,
                error=final_error,
                abort_origin="rpc" if run.get("abortRequested") else None,
            )
        force_save = True
        print(f"worker '{slug}' settled", flush=True)
    elif event_type == "extension_ui_request":
        method = event.get("method")
        if method in UI_DIALOG_METHODS:
            request_id = event.get("id")
            if not isinstance(request_id, str) or not request_id:
                state["lastError"] = f"blocking extension UI request has no valid id: {method}"
            else:
                state["pendingUi"] = pending_ui_record(event, request_id, method)
            force_save = True
        elif method not in UI_FIRE_AND_FORGET_METHODS:
            state["lastError"] = f"unrecognized extension UI request: {method}"
            force_save = True
    elif event_type == "response":
        state["lastRpcResponseId"] = event.get("id")
        state["lastRpcSuccess"] = bool(event.get("success"))
        data = event.get("data")
        if isinstance(data, dict):
            state["sessionId"] = data.get("sessionId") or state.get("sessionId")
            model = data.get("model")
            if isinstance(model, dict):
                state["actualProvider"] = model.get("provider") or state.get("actualProvider")
                state["actualModel"] = model.get("id") or model.get("model") or state.get("actualModel")
                if isinstance(run, dict):
                    run["actualProvider"] = state.get("actualProvider") or run.get("actualProvider")
                    run["actualModel"] = state.get("actualModel") or run.get("actualModel")
        if not event.get("success", False):
            state["lastError"] = event.get("error") or "RPC command rejected"
            if isinstance(run, dict):
                run.setdefault("rpcErrors", []).append(
                    {"requestId": event.get("id"), "error": state["lastError"]}
                )
                if event.get("id") == run.get("initialRequestId"):
                    finalize_run(state, p, "failed", error=state["lastError"])
                    state["status"] = "idle"
        force_save = True

    text = assistant_text(event)
    if text:
        write_private_text(p["result"], text + "\n")
        state["resultFresh"] = True
        state["lastResultTurn"] = state["turnSequence"]
        run = state.get("openRun")
        if isinstance(run, dict):
            message = event.get("message") or {}
            run["finalMessage"] = text
            run["actualProvider"] = message.get("provider") or event.get("provider") or run.get("actualProvider")
            run["actualModel"] = message.get("model") or event.get("model") or run.get("actualModel")
            run["stopReason"] = message.get("stopReason") or event.get("stopReason") or run.get("stopReason")
            usage = message.get("usage") or event.get("usage")
            if isinstance(usage, dict):
                merge_numeric(run.setdefault("usage", {}), usage)
        force_save = True
    return force_save


def prepare_runtime(p: dict[str, Path]) -> None:
    p["runtime"].mkdir(parents=True, exist_ok=True)
    p["runs"].mkdir(parents=True, exist_ok=True)
    os.chmod(p["runtime"], 0o700)
    os.chmod(p["runs"], 0o700)
    old = effective_state(p)
    if pid_alive(old.get("pid")):
        die(f"worker is already live with supervisor pid {old['pid']}")
    if old.get("status") == "orphaned":
        die(f"orphan Pi pid {old.get('piPid')} still owns this session; run recover first")
    if p["fifo"].exists() or p["fifo"].is_symlink():
        p["fifo"].unlink()
    os.mkfifo(p["fifo"], 0o600)


def read_prompt(args: argparse.Namespace) -> str:
    sources = int(bool(args.prompt)) + int(bool(args.prompt_file)) + int(bool(args.prompt_stdin))
    if sources > 1:
        die("use only one prompt source: positional prompt, --prompt-file, or --prompt-stdin")
    if args.prompt_file:
        try:
            prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
        except OSError as error:
            die(f"cannot read prompt file: {error}")
    elif args.prompt_stdin:
        prompt = sys.stdin.read()
    else:
        prompt = args.prompt or ""
    if not prompt.strip():
        die("provide a non-empty prompt, --prompt-file, or --prompt-stdin")
    return prompt


def terminate_group(child: subprocess.Popen, pgid: int, timeout: float = 5) -> None:
    if child.stdin and not child.stdin.closed:
        child.stdin.close()
    try:
        child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if group_alive(pgid):
            os.killpg(pgid, signal.SIGTERM)
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if group_alive(pgid):
                os.killpg(pgid, signal.SIGKILL)
            child.wait()
    if group_alive(pgid):
        os.killpg(pgid, signal.SIGTERM)
        deadline = time.monotonic() + PROCESS_GRACE_SECONDS
        while group_alive(pgid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if group_alive(pgid):
            os.killpg(pgid, signal.SIGKILL)
            deadline = time.monotonic() + PROCESS_GRACE_SECONDS
            while group_alive(pgid) and time.monotonic() < deadline:
                time.sleep(0.05)


def serve(args: argparse.Namespace) -> int:
    slug = validate_slug(args.slug)
    cwd = Path(args.cwd).expanduser().resolve()
    if not cwd.is_dir():
        die(f"working directory does not exist: {cwd}")
    pi = shutil.which("pi")
    if not pi:
        die("pi executable is not on PATH")
    prompt = read_prompt(args)
    p = paths(cwd, slug)
    session_lock = acquire_lock(p["lock"])
    child = None
    stderr_handle = None
    fifo_fd = None
    selector = None
    state: dict = {}
    stop_requested = False
    stop_signal: int | None = None

    def request_stop(signum=None, _frame=None) -> None:
        nonlocal stop_requested, stop_signal
        stop_requested = True
        stop_signal = signum

    try:
        prepare_runtime(p)
        previous_state = load_state(p["state"])
        existing_sequences = [
            int(path.name[4:])
            for path in p["runs"].glob("run-[0-9][0-9][0-9][0-9][0-9][0-9]")
            if path.name[4:].isdigit()
        ]
        previous_run_sequence = max(
            [int(previous_state.get("runSequence", 0))] + existing_sequences
        )
        command = [pi, "--mode", "rpc", "--session", str(p["session"]), "-a", "--name", slug]
        if args.model:
            command.extend(["--model", args.model])
        if args.thinking:
            command.extend(["--thinking", args.thinking])
        if args.no_tools:
            command.append("--no-tools")

        stderr_handle = p["stderr"].open("a", encoding="utf-8")
        child = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_handle,
            bufsize=0,
            start_new_session=True,
        )
        pgid = child.pid
        state = {
            "slug": slug,
            "cwd": str(cwd),
            "pid": os.getpid(),
            "piPid": child.pid,
            "piPgid": pgid,
            "status": "starting",
            "sessionFile": str(p["session"]),
            "eventsFile": str(p["events"]),
            "settleGeneration": int(previous_state.get("settleGeneration", 0)),
            "expectedSettleGeneration": int(previous_state.get("settleGeneration", 0)) + 1,
            "runSequence": previous_run_sequence,
            "openRun": None,
            "lastRunId": previous_state.get("lastRunId"),
            "lastRunStatus": previous_state.get("lastRunStatus"),
            "lastResultPath": previous_state.get("lastResultPath"),
            "queuedFollowUps": 0,
            "turnSequence": 0,
            "resultFresh": False,
            "settledResultFresh": False,
            "lastResultTurn": None,
            "lastEvent": None,
            "lastError": None,
            "lastControlRequestId": None,
            "lastControlSuccess": None,
            "lastRpcResponseId": None,
            "lastRpcSuccess": None,
            "pendingUi": None,
            "eventLineCount": count_event_lines(p["events"]),
            "sessionId": previous_state.get("sessionId"),
            "requestedModel": args.model,
            "actualProvider": previous_state.get("actualProvider"),
            "actualModel": previous_state.get("actualModel"),
        }
        p["pid"].write_text(f"{os.getpid()}\n", encoding="utf-8")
        save_state(p["state"], state)
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGHUP, request_stop)

        fifo_fd = os.open(p["fifo"], os.O_RDWR | os.O_NONBLOCK)
        fifo_buffer = b""
        stdout_buffer = b""
        selector = selectors.DefaultSelector()
        if child.stdout is None:
            raise RuntimeError("Pi stdout is unavailable")
        selector.register(child.stdout, selectors.EVENT_READ, "pi")
        selector.register(fifo_fd, selectors.EVENT_READ, "control")

        initial_id = f"initial-{uuid.uuid4().hex}"
        open_run(state, p, prompt, initial_id)
        save_state(p["state"], state)
        write_rpc(child, {"id": f"state-{uuid.uuid4().hex}", "type": "get_state"})
        write_rpc(child, {"id": initial_id, "type": "prompt", "message": prompt})
        print(f"worker '{slug}' started; session {p['session']}", flush=True)
        last_state_flush = time.monotonic()
        state_dirty = False

        with p["events"].open("a", encoding="utf-8") as events:
            while not stop_requested:
                if child.poll() is not None:
                    state["status"] = "dead"
                    state["lastError"] = f"Pi exited with code {child.returncode}"
                    finalize_run(
                        state,
                        p,
                        "failed",
                        error=state["lastError"],
                        worker_alive=False,
                        exit_code=child.returncode,
                    )
                    save_state(p["state"], state)
                    return 2

                for key, _mask in selector.select(timeout=0.25):
                    force_save = False
                    if key.data == "pi":
                        try:
                            chunk = os.read(child.stdout.fileno(), 65536)
                        except BlockingIOError:
                            chunk = b""
                        if not chunk:
                            continue
                        stdout_buffer += chunk
                        while b"\n" in stdout_buffer:
                            raw_line, stdout_buffer = stdout_buffer.split(b"\n", 1)
                            line = raw_line.decode("utf-8").rstrip("\r")
                            if not line:
                                continue
                            force_save = process_pi_event(line, events, state, p, slug) or force_save
                            state_dirty = True
                    else:
                        try:
                            chunk = os.read(fifo_fd, 65536)
                        except BlockingIOError:
                            chunk = b""
                        fifo_buffer += chunk
                        while b"\n" in fifo_buffer:
                            raw_line, fifo_buffer = fifo_buffer.split(b"\n", 1)
                            line = raw_line.decode("utf-8")
                            if not line.strip():
                                continue
                            try:
                                envelope = json.loads(line)
                            except json.JSONDecodeError:
                                state["lastError"] = "invalid control JSON"
                                force_save = True
                                continue
                            request_id = envelope.get("requestId")
                            if envelope.get("action") == "stop":
                                state["lastControlRequestId"] = request_id
                                state["lastControlSuccess"] = True
                                save_state(p["state"], state)
                                stop_requested = True
                                break
                            rpc = envelope.get("command")
                            if not isinstance(rpc, dict):
                                state["lastError"] = "control request has no RPC command"
                                state["lastControlRequestId"] = request_id
                                state["lastControlSuccess"] = False
                                force_save = True
                                continue
                            if rpc.get("type") == "extension_ui_response":
                                # The id must be the pending dialog's id, not the
                                # control request id; validate before forwarding.
                                pending = state.get("pendingUi")
                                if not isinstance(pending, dict) or rpc.get("id") != pending.get("id"):
                                    state["lastError"] = "extension_ui_response does not match a pending UI request"
                                    state["lastControlRequestId"] = request_id
                                    state["lastControlSuccess"] = False
                                    force_save = True
                                    continue
                                response = {"type": "extension_ui_response", "id": pending["id"]}
                                for key in ("value", "confirmed", "cancelled"):
                                    if key in rpc:
                                        response[key] = rpc[key]
                                write_rpc(child, response)
                                state["pendingUi"] = None
                                state["lastControlRequestId"] = request_id
                                state["lastControlSuccess"] = True
                                force_save = True
                                continue
                            rpc["id"] = request_id
                            rpc_type = rpc.get("type")
                            if rpc_type == "prompt":
                                if state.get("openRun"):
                                    state["lastError"] = "cannot start a new run while the current run is active"
                                    state["lastControlRequestId"] = request_id
                                    force_save = True
                                    continue
                                open_run(state, p, str(rpc.get("message") or ""), request_id)
                                state["status"] = "queued"
                                state["settledResultFresh"] = False
                            elif rpc_type == "follow_up":
                                if isinstance(state.get("openRun"), dict):
                                    state["openRun"]["queuedFollowUps"] += 1
                                    state["queuedFollowUps"] += 1
                                    state["status"] = "queued"
                                else:
                                    # Race: the client read state before the previous run settled.
                                    # Open a new run so this work still gets run boundaries
                                    # and a result artifact.
                                    rpc["type"] = "prompt"
                                    open_run(state, p, str(rpc.get("message") or ""), request_id)
                                    state["status"] = "queued"
                            elif rpc_type == "steer" and isinstance(state.get("openRun"), dict):
                                state["openRun"]["steers"] += 1
                            elif rpc_type == "abort" and isinstance(state.get("openRun"), dict):
                                state["openRun"]["abortRequested"] = True
                            write_rpc(child, rpc)
                            state["lastControlRequestId"] = request_id
                            state["lastControlSuccess"] = True
                            force_save = True

                    now = time.monotonic()
                    if force_save or (state_dirty and now - last_state_flush >= STATE_FLUSH_INTERVAL):
                        save_state(p["state"], state)
                        last_state_flush = now
                        state_dirty = False

                if state_dirty and time.monotonic() - last_state_flush >= STATE_FLUSH_INTERVAL:
                    save_state(p["state"], state)
                    last_state_flush = time.monotonic()
                    state_dirty = False
    except Exception as error:
        if state:
            state["status"] = "dead"
            state["lastError"] = str(error)
            finalize_run(state, p, "failed", error=str(error), worker_alive=False)
            save_state(p["state"], state)
        raise
    finally:
        if selector:
            selector.close()
        if fifo_fd is not None:
            os.close(fifo_fd)
        if child:
            terminate_group(child, child.pid)
        if stderr_handle:
            stderr_handle.close()
        if state:
            if state.get("openRun"):
                signal_name = None
                if stop_signal is not None:
                    try:
                        signal_name = signal.Signals(stop_signal).name
                    except ValueError:
                        signal_name = str(stop_signal)
                finalize_run(
                    state,
                    p,
                    "aborted",
                    error="supervisor stopped before the run settled",
                    abort_origin="signal" if stop_signal is not None else "supervisor",
                    worker_alive=False,
                    signal_name=signal_name,
                )
            state["status"] = "stopped" if stop_requested else state.get("status", "dead")
            state["pid"] = None
            state["piPid"] = None
            state["piPgid"] = None
            save_state(p["state"], state)
            print(f"worker '{slug}' stopped; session preserved", flush=True)
        fcntl.flock(session_lock.fileno(), fcntl.LOCK_UN)
        session_lock.close()
    return 0


def worker_paths(args: argparse.Namespace) -> dict[str, Path]:
    return paths(Path(args.cwd).expanduser().resolve(), validate_slug(args.slug))


def require_state(p: dict[str, Path]) -> dict:
    state = effective_state(p)
    if not state:
        die(f"no worker state for '{p['runtime'].name}' in {p['cwd']}")
    return state


def send_control(p: dict[str, Path], envelope: dict, timeout: float = 15, rpc_ack: bool = True) -> dict:
    state = require_state(p)
    if not pid_alive(state.get("pid")):
        die("worker is not live; resume it with serve using the same slug")
    request_id = uuid.uuid4().hex
    envelope["requestId"] = request_id
    payload = (json.dumps(envelope, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        fd = os.open(p["fifo"], os.O_WRONLY | os.O_NONBLOCK)
    except OSError as error:
        if error.errno in {errno.ENXIO, errno.ENOENT}:
            die("worker control channel is unavailable")
        raise
    deadline = time.monotonic() + timeout
    try:
        offset = 0
        while offset < len(payload):
            try:
                written = os.write(fd, payload[offset:])
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    die("timed out while writing the command to the control pipe")
                select.select([], [fd], [], min(0.1, remaining))
                continue
            if written <= 0:
                die("control pipe closed while writing the command")
            offset += written
    finally:
        os.close(fd)

    field = "lastRpcResponseId" if rpc_ack else "lastControlRequestId"
    while time.monotonic() < deadline:
        current = effective_state(p)
        if current.get(field) == request_id:
            if rpc_ack and not current.get("lastRpcSuccess"):
                die(current.get("lastError") or "Pi rejected the RPC command")
            if not rpc_ack and current.get("lastControlSuccess") is False:
                die(current.get("lastError") or "supervisor rejected the control command")
            return current
        if current.get("status") in {"dead", "orphaned", "stopped"}:
            die(f"worker became {current['status']} before acknowledging the command")
        time.sleep(0.05)
    die(f"command was written to the control pipe, but no Pi acknowledgement arrived within {timeout:g}s")


def status(args: argparse.Namespace) -> int:
    print(json.dumps(require_state(worker_paths(args)), ensure_ascii=False, indent=2))
    return 0


def list_workers(args: argparse.Namespace) -> int:
    cwd = Path(args.cwd).expanduser().resolve()
    root = cwd / ".pi" / "workers"
    rows = []
    if root.is_dir():
        for state_path in sorted(root.glob("*/state.json")):
            p = paths(cwd, state_path.parent.name)
            state = effective_state(p)
            if state:
                rows.append(state)
    if not rows:
        print("(no workers)")
        return 0
    print(f"{'SLUG':24} {'STATUS':10} {'SUPERVISOR':10} {'PI':8} LAST EVENT")
    for state in rows:
        print(
            f"{state.get('slug', '?')[:24]:24} {state.get('status', '?')[:10]:10} "
            f"{str(state.get('pid') or '-')[:10]:10} {str(state.get('piPid') or '-')[:8]:8} "
            f"{state.get('lastEvent') or '-'}"
        )
    return 0


def wait_worker(args: argparse.Namespace) -> int:
    p = worker_paths(args)
    initial = require_state(p)
    target = int(initial.get("expectedSettleGeneration", initial.get("settleGeneration", 0)))
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        state = require_state(p)
        if state.get("status") == "idle" and int(state.get("settleGeneration", 0)) >= target:
            if state.get("lastRunStatus") not in {None, "completed"}:
                print(
                    f"worker '{args.slug}' ended run {state.get('lastRunId')} with status {state.get('lastRunStatus')}",
                    file=sys.stderr,
                )
                return 2
            print(f"worker '{args.slug}' settled")
            return 0
        if state.get("status") in {"dead", "orphaned", "stopped"}:
            print(f"worker '{args.slug}' is {state['status']}", file=sys.stderr)
            return 2
        pending = state.get("pendingUi")
        if args.return_on_ui and isinstance(pending, dict):
            print(f"worker '{args.slug}' is waiting on a {pending.get('method')} dialog ({pending.get('id')})")
            for field in ("title", "message"):
                if pending.get(field):
                    print(f"{field}: {pending[field]}")
            for option in pending.get("options") or []:
                print(f"option: {option}")
            return 3
        time.sleep(0.25)
    print(f"timed out after {args.timeout:g}s", file=sys.stderr)
    return 1


def result(args: argparse.Namespace) -> int:
    p = worker_paths(args)
    state = require_state(p)
    run_id = state.get("lastRunId")
    if isinstance(run_id, str) and RUN_ID_RE.fullmatch(run_id):
        result_path = p["runs"] / run_id / "result.json"
        try:
            run_result = json.loads(Path(result_path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            die(f"state points to a missing run artifact: {result_path}")
        except json.JSONDecodeError as error:
            die(f"run artifact is invalid JSON: {error}")
        if args.json:
            print(json.dumps(run_result, ensure_ascii=False, indent=2))
        elif run_result.get("finalMessage"):
            print(str(run_result["finalMessage"]).rstrip())
        else:
            die(run_result.get("error") or f"run ended with status {run_result.get('status')}")
        return 0
    # Read-only fallback for state imported from the legacy helper: it cached
    # the settled assistant text in result.md without any run artifact. Only
    # reachable when no run artifact exists and the cache was marked fresh by
    # a settled turn (effective_state may already have transitioned the
    # status, so the freshness flag, not the status, is the gate).
    if state.get("settledResultFresh") and p["result"].exists():
        print(
            "warning: legacy state has no run artifact; showing the cached result.md "
            "(no run evidence; verify before relying on it)",
            file=sys.stderr,
        )
        print(p["result"].read_text(encoding="utf-8").rstrip())
        return 0
    die("worker has no completed run artifact")
    return 0


def rpc_message(args: argparse.Namespace, rpc_type: str) -> int:
    p = worker_paths(args)
    state = require_state(p)
    actual_type = rpc_type
    if rpc_type == "follow_up" and state.get("status") == "idle":
        actual_type = "prompt"
    if rpc_type == "steer" and state.get("status") != "running":
        die("steer requires a running worker; use follow-up for an idle worker")
    if rpc_type == "follow_up" and state.get("queuedFollowUps", 0):
        print(
            f"warning: {state['queuedFollowUps']} follow-up message(s) already queued; Pi will process them in order",
            file=sys.stderr,
        )
    current = send_control(
        p,
        {"action": "rpc", "command": {"type": actual_type, "message": args.message}},
        timeout=args.timeout,
    )
    print(f"{actual_type} accepted for '{args.slug}' (next settle {current.get('expectedSettleGeneration')})")
    return 0


def abort(args: argparse.Namespace) -> int:
    p = worker_paths(args)
    send_control(
        p,
        {"action": "rpc", "command": {"type": "abort"}},
        timeout=args.timeout,
    )
    print(f"abort accepted for '{args.slug}'")
    return 0


def ui_respond(args: argparse.Namespace) -> int:
    p = worker_paths(args)
    state = require_state(p)
    pending = state.get("pendingUi")
    if not isinstance(pending, dict):
        die("worker has no pending blocking UI request; nothing to answer")
    method = pending.get("method")
    if method == "confirm" and args.value is not None:
        die("confirm dialogs require --confirmed or --cancelled")
    if method != "confirm" and args.confirmed:
        die(f"{method} dialogs require --value or --cancelled")
    # Pi expects a select answer to be one of the offered option strings
    # verbatim; anything else would leave the dialog unanswered or misread.
    options = pending.get("options")
    if method == "select" and args.value is not None and isinstance(options, list) and args.value not in options:
        die("select value must be one of the offered options: " + ", ".join(repr(option) for option in options))
    if args.value is not None:
        fields = {"value": args.value}
    elif args.confirmed:
        fields = {"confirmed": True}
    elif args.cancelled:
        fields = {"cancelled": True}
    else:
        die("provide exactly one of --value, --confirmed, or --cancelled")
    send_control(
        p,
        {
            "action": "rpc",
            "command": {"type": "extension_ui_response", "id": pending.get("id"), **fields},
        },
        timeout=args.timeout,
        rpc_ack=False,
    )
    print(
        f"UI response accepted by supervisor for '{args.slug}' "
        f"({pending.get('method')} dialog {pending.get('id')})"
    )
    return 0


def stop(args: argparse.Namespace) -> int:
    p = worker_paths(args)
    state = require_state(p)
    if state.get("status") == "orphaned":
        die("worker has an orphan Pi process; use recover")
    if not pid_alive(state.get("pid")):
        print(f"worker '{args.slug}' is already stopped")
        return 0
    send_control(p, {"action": "stop"}, timeout=min(args.timeout, 15), rpc_ack=False)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        current = effective_state(p)
        if current.get("status") == "stopped" and not pid_alive(current.get("pid")):
            print(f"worker '{args.slug}' stopped; session preserved at {p['session']}")
            return 0
        time.sleep(0.1)
    print(f"worker '{args.slug}' did not stop within {args.timeout:g}s", file=sys.stderr)
    return 1


def recover(args: argparse.Namespace) -> int:
    p = worker_paths(args)
    lock = acquire_lock(p["lock"])
    try:
        state = require_state(p)
        if pid_alive(state.get("pid")):
            die("supervisor is still alive; use stop instead")
        if pid_alive(state.get("piPid")):
            if not pi_process_matches(state):
                if not args.forget:
                    die(
                        "stored Pi PID belongs to a different process; refusing to signal it; "
                        "rerun with --forget to clear the state without touching that process"
                    )
                # Explicit escape: never signal the mismatched process; keep all
                # run/result/session evidence and just stop tracking the PIDs.
                state["status"] = "stopped"
                state["pid"] = None
                state["piPid"] = None
                state["piPgid"] = None
                state["lastError"] = (
                    "orphan state cleared with recover --forget; the stored PID no longer "
                    "matched the expected Pi session and was left untouched"
                )
                save_state(p["state"], state)
                print(f"forgot orphan state for '{args.slug}'; the external process was left untouched")
                return 0
            pgid = int(state.get("piPgid") or state["piPid"])
            os.killpg(pgid, signal.SIGTERM)
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline and group_alive(pgid):
                time.sleep(0.1)
            if group_alive(pgid):
                os.killpg(pgid, signal.SIGKILL)
            state["status"] = "stopped"
            state["pid"] = None
            state["piPid"] = None
            state["piPgid"] = None
            state["lastError"] = "orphan process group recovered"
            save_state(p["state"], state)
            print(f"recovered orphan worker '{args.slug}'; session preserved")
            return 0
        state["status"] = "stopped"
        state["pid"] = None
        state["piPid"] = None
        state["piPgid"] = None
        state["lastError"] = "orphan process was already gone; stale process state cleared"
        save_state(p["state"], state)
        print(f"cleared stale orphan state for '{args.slug}'; no live Pi process was signaled")
        return 0
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def clean(args: argparse.Namespace) -> int:
    p = worker_paths(args)
    state = effective_state(p)
    if state and (pid_alive(state.get("pid")) or state.get("status") == "orphaned"):
        die("refusing to clean a live or orphaned worker; stop or recover it first")
    if not p["runtime"].exists():
        print(f"no transient runtime for '{args.slug}'")
        return 0
    if args.discard_logs:
        shutil.rmtree(p["runtime"])
        print(f"removed transient runtime and logs for '{args.slug}'")
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{uuid.uuid4().hex[:6]}"
        archive = p["logs"] / stamp
        archive.mkdir(parents=True, exist_ok=False)
        os.chmod(archive, 0o700)
        for name in ("events.jsonl", "stderr.log", "result.md", "state.json", "runs"):
            source = p["runtime"] / name
            if source.exists():
                shutil.move(str(source), archive / name)
        shutil.rmtree(p["runtime"])
        print(f"archived diagnostic logs to {archive}")
    if p["session"].exists():
        print(f"preserved session {p['session']}")
    return 0


def compact_json(value: object, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if limit > 0 and len(text) > limit:
        text = text[: max(0, limit - 1)] + "…"
    return safe_terminal(text)


def safe_terminal(value: str) -> str:
    return "".join(
        character
        if character in {"\n", "\t"} or (ord(character) >= 32 and ord(character) != 127)
        else f"\\x{ord(character):02x}"
        for character in value
    )


def result_text(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    parts = []
    for item in value.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
            parts.append(str(item["text"]))
    return "\n".join(parts)


class WatchRenderer:
    def __init__(self, *, thinking: bool, tool_output: int, color: bool) -> None:
        self.thinking = thinking
        self.tool_output = tool_output
        self.color = color
        self.stream: str | None = None
        self.turn_had_text = False

    def paint(self, code: str, value: str) -> str:
        return f"\033[{code}m{value}\033[0m" if self.color else value

    def end_stream(self) -> None:
        if self.stream:
            print(flush=True)
            self.stream = None

    def label(self, value: str, code: str = "36") -> None:
        self.end_stream()
        print(self.paint(code, value), flush=True)

    def delta(self, kind: str, value: str) -> None:
        if not value:
            return
        if self.stream != kind:
            self.end_stream()
            prefix = "thinking  " if kind == "thinking" else "assistant "
            print(self.paint("2;35" if kind == "thinking" else "32", prefix), end="", flush=True)
            self.stream = kind
        print(safe_terminal(value), end="", flush=True)

    def render(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "message_update":
            update = event.get("assistantMessageEvent") or {}
            update_type = update.get("type")
            if update_type == "thinking_delta" and self.thinking:
                self.delta("thinking", str(update.get("delta") or ""))
            elif update_type == "text_delta":
                self.turn_had_text = True
                self.delta("assistant", str(update.get("delta") or ""))
            elif update_type in {"thinking_end", "text_end"}:
                self.end_stream()
            return
        if event_type == "agent_start":
            self.label("▶ agent running", "1;36")
        elif event_type == "agent_settled":
            self.label("✓ agent settled", "1;32")
        elif event_type == "turn_start":
            self.turn_had_text = False
            self.label("↻ turn", "36")
        elif event_type == "message_end":
            if not self.turn_had_text:
                text = assistant_text(event)
                if text:
                    self.delta("assistant", text)
                    self.end_stream()
        elif event_type == "tool_execution_start":
            name = str(event.get("toolName") or "tool")
            args = compact_json(event.get("args") or {}, 800)
            self.label(f"→ {name} {args}", "33")
        elif event_type == "tool_execution_end":
            name = str(event.get("toolName") or "tool")
            result = event.get("result")
            failed = bool(
                event.get("isError") or (isinstance(result, dict) and result.get("isError"))
            )
            self.label(f"← {name} {'error' if failed else 'ok'}", "31" if failed else "33")
            output = result_text(result)
            if output and self.tool_output:
                if len(output) > self.tool_output:
                    output = output[: self.tool_output - 1] + "…"
                print(safe_terminal(output), flush=True)
        elif event_type == "extension_ui_request":
            method = event.get("method")
            if method in UI_DIALOG_METHODS:
                self.label(f"⚠ UI blocked: {method} ({event.get('id')})", "1;31")
        elif event_type == "compaction_start":
            self.label("◇ compaction started", "35")
        elif event_type == "compaction_end":
            self.label("◇ compaction finished", "35")
        elif event_type == "response" and not event.get("success", False):
            self.label(f"✗ RPC rejected: {event.get('error') or 'unknown error'}", "1;31")


def watch(args: argparse.Namespace) -> int:
    p = worker_paths(args)
    if args.history < 0 or args.tool_output < 0 or args.poll <= 0:
        die("--history and --tool-output must be non-negative; --poll must be positive")
    state = load_state(p["state"])
    if not state:
        die(f"no worker state for '{args.slug}' in {p['cwd']}")
    try:
        handle = p["events"].open("rb")
    except FileNotFoundError:
        die(f"worker has no event stream: {p['events']}")

    color = args.color == "always" or (args.color == "auto" and sys.stdout.isatty())
    renderer = WatchRenderer(thinking=args.thinking, tool_output=args.tool_output, color=color)
    print(
        f"watching {args.slug} status={state.get('status')} "
        f"model={state.get('actualProvider') or '?'}:{state.get('actualModel') or '?'}",
        flush=True,
    )
    try:
        if args.history:
            history = deque(handle, maxlen=args.history)
            for raw in history:
                try:
                    event = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if args.raw:
                    print(json.dumps(event, ensure_ascii=False), flush=True)
                else:
                    renderer.render(event)
        else:
            handle.seek(0, os.SEEK_END)

        if args.no_follow:
            renderer.end_stream()
            return 0

        while True:
            position = handle.tell()
            raw = handle.readline()
            if raw:
                if not raw.endswith(b"\n"):
                    handle.seek(position)
                    time.sleep(args.poll)
                    continue
                try:
                    event = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if args.raw:
                    print(json.dumps(event, ensure_ascii=False), flush=True)
                else:
                    renderer.render(event)
                continue

            current = load_state(p["state"])
            if not current:
                renderer.end_stream()
                print("watch ended: worker runtime disappeared", file=sys.stderr, flush=True)
                return 2
            if not args.stay and current.get("status") in {"idle", "stopped", "dead", "orphaned"}:
                renderer.end_stream()
                print(f"watch ended: status={current.get('status')}", flush=True)
                return 0 if current.get("status") in {"idle", "stopped"} else 2
            time.sleep(args.poll)
    except KeyboardInterrupt:
        renderer.end_stream()
        return 130
    finally:
        handle.close()


def add_cwd(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cwd", default=os.getcwd())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="start or resume a foreground RPC worker")
    serve_p.add_argument("slug")
    serve_p.add_argument("prompt", nargs="?")
    serve_p.add_argument("--prompt-file")
    serve_p.add_argument("--prompt-stdin", action="store_true")
    add_cwd(serve_p)
    serve_p.add_argument("--model")
    serve_p.add_argument("--thinking", choices=["off", "minimal", "low", "medium", "high", "xhigh", "max"])
    serve_p.add_argument("--no-tools", action="store_true")
    serve_p.set_defaults(func=serve)

    list_p = sub.add_parser("list")
    add_cwd(list_p)
    list_p.set_defaults(func=list_workers)

    watch_p = sub.add_parser("watch", help="render the worker event stream as a live read-only view")
    watch_p.add_argument("slug")
    watch_p.add_argument("--history", type=int, default=200, help="replay this many recent events first; 0 starts at EOF")
    watch_p.add_argument("--thinking", action="store_true", help="include streamed model thinking")
    watch_p.add_argument("--tool-output", type=int, default=1200, help="maximum tool-result characters to print; 0 hides output")
    watch_p.add_argument("--raw", action="store_true", help="print raw JSON events")
    watch_p.add_argument("--no-follow", action="store_true", help="replay history and exit")
    watch_p.add_argument("--stay", action="store_true", help="keep watching across settled runs")
    watch_p.add_argument("--poll", type=float, default=0.2)
    watch_p.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    add_cwd(watch_p)
    watch_p.set_defaults(func=watch)

    for name in ("status", "result"):
        item = sub.add_parser(name)
        item.add_argument("slug")
        add_cwd(item)
        if name == "result":
            item.add_argument("--json", action="store_true", help="print the full result artifact")
        item.set_defaults(func=globals()[name])

    wait_p = sub.add_parser(
        "wait",
        help="block until the open run settles (exit 0), times out (1), or fails (2)",
    )
    wait_p.add_argument("slug")
    wait_p.add_argument("timeout", nargs="?", type=float, default=600)
    wait_p.add_argument(
        "--return-on-ui",
        action="store_true",
        help="exit 3 and print the dialog when the worker is blocked on an extension dialog",
    )
    add_cwd(wait_p)
    wait_p.set_defaults(func=wait_worker)

    for name, rpc_type in (("steer", "steer"), ("follow-up", "follow_up")):
        item = sub.add_parser(name)
        item.add_argument("slug")
        item.add_argument("message")
        item.add_argument("--timeout", type=float, default=15)
        add_cwd(item)
        item.set_defaults(func=lambda args, kind=rpc_type: rpc_message(args, kind))

    abort_p = sub.add_parser("abort")
    abort_p.add_argument("slug")
    abort_p.add_argument("--timeout", type=float, default=60)
    add_cwd(abort_p)
    abort_p.set_defaults(func=abort)

    stop_p = sub.add_parser("stop", help="stop supervisor and Pi, preserve session and result evidence")
    stop_p.add_argument("slug")
    stop_p.add_argument("--timeout", type=float, default=30)
    add_cwd(stop_p)
    stop_p.set_defaults(func=stop)

    recover_p = sub.add_parser("recover", help="kill an orphaned Pi process after supervisor death")
    recover_p.add_argument("slug")
    recover_p.add_argument("--timeout", type=float, default=15)
    recover_p.add_argument(
        "--forget",
        action="store_true",
        help="clear the orphan state without signaling the stored PID when it no longer "
        "matches the expected Pi session (evidence is preserved, the external process is untouched)",
    )
    add_cwd(recover_p)
    recover_p.set_defaults(func=recover)

    ui_p = sub.add_parser("ui-respond", help="answer a pending blocking extension UI request")
    ui_p.add_argument("slug")
    ui_choice = ui_p.add_mutually_exclusive_group()
    ui_choice.add_argument("--value", help="answer for select/input/editor dialogs")
    ui_choice.add_argument("--confirmed", action="store_true", help="confirm a confirm dialog")
    ui_choice.add_argument("--cancelled", action="store_true", help="dismiss any dialog")
    ui_p.add_argument("--timeout", type=float, default=15)
    add_cwd(ui_p)
    ui_p.set_defaults(func=ui_respond)

    clean_p = sub.add_parser("clean")
    clean_p.add_argument("slug")
    clean_p.add_argument("--discard-logs", action="store_true")
    add_cwd(clean_p)
    clean_p.set_defaults(func=clean)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if getattr(args, "prompt", None) and getattr(args, "prompt_file", None):
        die("use either positional prompt or --prompt-file, not both")
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
