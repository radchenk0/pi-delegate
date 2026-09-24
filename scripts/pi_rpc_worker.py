#!/usr/bin/env python3
"""Run resumable Pi coding-agent workers over the JSONL RPC protocol."""

from __future__ import annotations

import argparse
from datetime import datetime
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from typing import NoReturn
import uuid


SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
STATE_FLUSH_INTERVAL = 0.5


def die(message: str, code: int = 2) -> NoReturn:
    print(f"pi-rpc-worker: {message}", file=sys.stderr)
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


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    state["updatedAt"] = time.time()
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


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
    if not supervisor_live and pi_live:
        state["status"] = "orphaned"
        state["lastError"] = "supervisor is dead while its Pi process is still alive"
    elif not supervisor_live and previous not in {"stopped", "dead"}:
        state["status"] = "dead"
        state["lastError"] = state.get("lastError") or "supervisor process is not alive"
    if persist and state.get("status") != previous:
        save_state(p["state"], state)
    return state


def acquire_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
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


def write_rpc(child: subprocess.Popen, command: dict) -> None:
    if child.stdin is None:
        raise RuntimeError("Pi stdin is unavailable")
    child.stdin.write((json.dumps(command, ensure_ascii=False) + "\n").encode("utf-8"))
    child.stdin.flush()


def process_pi_event(line: str, events, state: dict, p: dict[str, Path], slug: str) -> bool:
    events.write(line + "\n")
    events.flush()
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return False

    force_save = False
    event_type = event.get("type")
    state["lastEvent"] = event_type
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
        state["settledResultFresh"] = bool(state["resultFresh"])
        if not state["settledResultFresh"]:
            state["lastError"] = "worker settled without a fresh assistant result"
        force_save = True
        print(f"worker '{slug}' settled", flush=True)
    elif event_type == "extension_ui_request":
        method = event.get("method")
        if method not in {"setStatus", "notify"}:
            state["lastError"] = f"possible blocking UI request: {method}"
            force_save = True
    elif event_type == "response":
        state["lastRpcResponseId"] = event.get("id")
        state["lastRpcSuccess"] = bool(event.get("success"))
        if not event.get("success", False):
            state["lastError"] = event.get("error") or "RPC command rejected"
        force_save = True

    text = assistant_text(event)
    if text:
        p["result"].write_text(text + "\n", encoding="utf-8")
        state["resultFresh"] = True
        state["lastResultTurn"] = state["turnSequence"]
        force_save = True
    return force_save


def prepare_runtime(p: dict[str, Path]) -> None:
    p["runtime"].mkdir(parents=True, exist_ok=True)
    old = effective_state(p)
    if pid_alive(old.get("pid")):
        die(f"worker is already live with supervisor pid {old['pid']}")
    if old.get("status") == "orphaned":
        die(f"orphan Pi pid {old.get('piPid')} still owns this session; run recover first")
    if p["fifo"].exists() or p["fifo"].is_symlink():
        p["fifo"].unlink()
    os.mkfifo(p["fifo"], 0o600)


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        try:
            prompt = Path(args.prompt_file).expanduser().read_text(encoding="utf-8")
        except OSError as error:
            die(f"cannot read prompt file: {error}")
    else:
        prompt = args.prompt or ""
    if not prompt.strip():
        die("provide a non-empty prompt or --prompt-file")
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

    def request_stop(_signum=None, _frame=None) -> None:
        nonlocal stop_requested
        stop_requested = True

    try:
        prepare_runtime(p)
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
            "settleGeneration": 0,
            "expectedSettleGeneration": 1,
            "queuedFollowUps": 0,
            "turnSequence": 0,
            "resultFresh": False,
            "settledResultFresh": False,
            "lastResultTurn": None,
            "lastEvent": None,
            "lastError": None,
            "lastControlRequestId": None,
            "lastRpcResponseId": None,
            "lastRpcSuccess": None,
        }
        p["pid"].write_text(f"{os.getpid()}\n", encoding="utf-8")
        save_state(p["state"], state)
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)

        fifo_fd = os.open(p["fifo"], os.O_RDWR | os.O_NONBLOCK)
        fifo_buffer = ""
        stdout_buffer = b""
        selector = selectors.DefaultSelector()
        if child.stdout is None:
            raise RuntimeError("Pi stdout is unavailable")
        selector.register(child.stdout, selectors.EVENT_READ, "pi")
        selector.register(fifo_fd, selectors.EVENT_READ, "control")

        initial_id = f"initial-{uuid.uuid4().hex}"
        write_rpc(child, {"id": initial_id, "type": "prompt", "message": prompt})
        print(f"worker '{slug}' started; session {p['session']}", flush=True)
        last_state_flush = time.monotonic()
        state_dirty = False

        with p["events"].open("a", encoding="utf-8") as events:
            while not stop_requested:
                if child.poll() is not None:
                    state["status"] = "dead"
                    state["lastError"] = f"Pi exited with code {child.returncode}"
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
                            chunk = os.read(fifo_fd, 65536).decode("utf-8")
                        except BlockingIOError:
                            chunk = ""
                        fifo_buffer += chunk
                        while "\n" in fifo_buffer:
                            line, fifo_buffer = fifo_buffer.split("\n", 1)
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
                                save_state(p["state"], state)
                                stop_requested = True
                                break
                            rpc = envelope.get("command")
                            if not isinstance(rpc, dict):
                                state["lastError"] = "control request has no RPC command"
                                state["lastControlRequestId"] = request_id
                                force_save = True
                                continue
                            rpc["id"] = request_id
                            rpc_type = rpc.get("type")
                            if rpc_type in {"prompt", "follow_up"}:
                                state["status"] = "queued"
                                state["expectedSettleGeneration"] = state["settleGeneration"] + 1
                                state["settledResultFresh"] = False
                                if rpc_type == "follow_up":
                                    state["queuedFollowUps"] += 1
                            write_rpc(child, rpc)
                            state["lastControlRequestId"] = request_id
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
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)

    field = "lastRpcResponseId" if rpc_ack else "lastControlRequestId"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = effective_state(p)
        if current.get(field) == request_id:
            if rpc_ack and not current.get("lastRpcSuccess"):
                die(current.get("lastError") or "Pi rejected the RPC command")
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
            print(f"worker '{args.slug}' settled")
            return 0
        if state.get("status") in {"dead", "orphaned", "stopped"}:
            print(f"worker '{args.slug}' is {state['status']}", file=sys.stderr)
            return 2
        time.sleep(0.25)
    print(f"timed out after {args.timeout:g}s", file=sys.stderr)
    return 1


def result(args: argparse.Namespace) -> int:
    p = worker_paths(args)
    state = require_state(p)
    if state.get("status") != "idle":
        die(f"worker is {state.get('status')}; no settled result is available")
    if not state.get("settledResultFresh"):
        die(state.get("lastError") or "worker settled without a fresh assistant result")
    try:
        print(p["result"].read_text(encoding="utf-8").rstrip())
    except FileNotFoundError:
        die("state reports a fresh result, but result.md is missing")
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
        if not pid_alive(state.get("piPid")):
            print(f"worker '{args.slug}' has no live orphan Pi process")
            return 0
        if not pi_process_matches(state):
            die("stored Pi PID belongs to a different process; refusing to signal it")
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
        for name in ("events.jsonl", "stderr.log", "result.md", "state.json"):
            source = p["runtime"] / name
            if source.exists():
                shutil.move(str(source), archive / name)
        shutil.rmtree(p["runtime"])
        print(f"archived diagnostic logs to {archive}")
    if p["session"].exists():
        print(f"preserved session {p['session']}")
    return 0


def add_cwd(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cwd", default=os.getcwd())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="start or resume a foreground RPC worker")
    serve_p.add_argument("slug")
    serve_p.add_argument("prompt", nargs="?")
    serve_p.add_argument("--prompt-file")
    add_cwd(serve_p)
    serve_p.add_argument("--model")
    serve_p.add_argument("--thinking", choices=["off", "minimal", "low", "medium", "high", "xhigh", "max"])
    serve_p.add_argument("--no-tools", action="store_true")
    serve_p.set_defaults(func=serve)

    list_p = sub.add_parser("list")
    add_cwd(list_p)
    list_p.set_defaults(func=list_workers)

    for name in ("status", "result"):
        item = sub.add_parser(name)
        item.add_argument("slug")
        add_cwd(item)
        item.set_defaults(func=globals()[name])

    wait_p = sub.add_parser("wait")
    wait_p.add_argument("slug")
    wait_p.add_argument("timeout", nargs="?", type=float, default=600)
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

    for name in ("stop", "recover"):
        item = sub.add_parser(name)
        item.add_argument("slug")
        item.add_argument("--timeout", type=float, default=10)
        add_cwd(item)
        item.set_defaults(func=globals()[name])

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
