---
name: pi-rpc-worker
description: Delegate self-contained coding, research, or file tasks to Pi coding-agent workers over RPC when the user requests Pi workers or when independent delegated work would materially help. Use for starting, monitoring, steering, following up with, stopping, recovering, or resuming Pi workers across any project. Do not use for Codex native subagents or ordinary shell commands that do not need a Pi agent.
---

# Pi RPC Worker

Use the bundled `scripts/pi_rpc_worker.py`. It drives `pi --mode rpc` through JSONL, keeps live workers steerable, and stores each Pi session separately so a stopped process can resume later.

Use the full helper path in every command because shell variables may not persist between execution calls:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/pi-rpc-worker/scripts/pi_rpc_worker.py" <command>
```

## Delegate

Give each worker a self-contained task containing the goal, relevant inputs, absolute working directory, permitted file scope, artifact locations, definition of done, and required verification. Assign only one live worker to a file scope. Delegation does not expand permission for destructive actions or external writes.

Start the supervisor as a foreground long-running command from the target project:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/pi-rpc-worker/scripts/pi_rpc_worker.py" serve <slug> "<task>"
```

Use the execution tool's early-yield or persistent-session facility so the foreground supervisor remains alive while other work continues. Do not detach it with `nohup`; some execution environments reap detached children after the launching command exits.

For prompts containing secrets or other sensitive values, keep the prompt out of the process list:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/pi-rpc-worker/scripts/pi_rpc_worker.py" serve <slug> --prompt-file <path>
```

The helper creates:

- `.pi/sessions/<slug>.jsonl` — durable conversation used for resume;
- `.pi/workers/<slug>/` — live state and diagnostic files;
- `.pi/logs/<slug>/` — archived diagnostics created by `clean`.

If Pi cannot access user settings or credentials inside the sandbox, rerun the supervisor with the narrow escalation required for Pi. Never expose credentials in prompts or logs.

## Monitor and control

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/pi-rpc-worker/scripts/pi_rpc_worker.py" list
python3 "${CODEX_HOME:-$HOME/.codex}/skills/pi-rpc-worker/scripts/pi_rpc_worker.py" status <slug>
python3 "${CODEX_HOME:-$HOME/.codex}/skills/pi-rpc-worker/scripts/pi_rpc_worker.py" wait <slug> 600
python3 "${CODEX_HOME:-$HOME/.codex}/skills/pi-rpc-worker/scripts/pi_rpc_worker.py" result <slug>
python3 "${CODEX_HOME:-$HOME/.codex}/skills/pi-rpc-worker/scripts/pi_rpc_worker.py" steer <slug> "<correction>"
python3 "${CODEX_HOME:-$HOME/.codex}/skills/pi-rpc-worker/scripts/pi_rpc_worker.py" follow-up <slug> "<next task>"
python3 "${CODEX_HOME:-$HOME/.codex}/skills/pi-rpc-worker/scripts/pi_rpc_worker.py" abort <slug>
```

Use `steer` only during a running turn. Use `follow-up` for subsequent work; the helper sends `prompt` to an idle worker and queues `follow_up` for a running worker. Pi processes multiple queued follow-ups according to its configured follow-up mode, and `wait` completes after the next `agent_settled` confirms that queued continuations are exhausted.

Treat `result` as a convenience, not proof. It refuses to return a previous turn's stale text, but still inspect artifacts and run proportionate verification. If a worker appears stuck, inspect `status`, `stderr.log`, and the tail of `events.jsonl`; an `extension_ui_request` can indicate a blocking dialog.

## Stop, recover, resume, and clean

Stop a live process after the current interaction:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/pi-rpc-worker/scripts/pi_rpc_worker.py" stop <slug> --timeout 15
```

If a supervisor was killed ungracefully and `status` reports `orphaned`, run `recover <slug>` before starting that slug again. The helper verifies that the stored PID still belongs to the expected Pi session before signaling its process group.

Resume by running `serve` again with the same slug and a new task. The helper locks the session and passes the existing file through `pi --session`; never add `--no-session`.

After verifying the result, clean transient runtime files:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/pi-rpc-worker/scripts/pi_rpc_worker.py" clean <slug>
```

By default, `clean` archives `events.jsonl`, `stderr.log`, `result.md`, and final state under `.pi/logs/`. Use `clean <slug> --discard-logs` only when diagnostic retention is unnecessary. Both forms preserve `.pi/sessions/<slug>.jsonl`. Delete a durable session only when the user requests it or retention is clearly no longer needed. Keep `.pi/` out of version control unless the project explicitly tracks agent state.

For protocol details or helper troubleshooting, read [references/rpc-protocol.md](references/rpc-protocol.md).
