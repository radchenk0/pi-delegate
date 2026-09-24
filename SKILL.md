---
name: pi-delegate
description: Delegate self-contained coding, research, review, or file tasks to long-lived Pi coding-agent workers over RPC. Use when the user requests Pi workers or when an independent Pi agent materially helps, including starting, monitoring, steering, following up, aborting, recovering, or resuming workers. Do not use for native subagents or ordinary shell commands.
---

# Pi Delegate

Resolve the skill root as the absolute directory containing this `SKILL.md`.
Invoke its `scripts/pi_rpc_worker.py` by absolute path
on every call. Do not assume a Codex or Claude specific home directory.

## Prepare the brief

Give the worker one bounded task with its goal, inputs, allowed file scope,
explicit exclusions, definition of done, real verification commands, and final
report format. Read [writing-the-brief.md](references/writing-the-brief.md) for
the template. Never give concurrent writers the same worktree or file scope.
Delegation does not extend authorization for destructive actions, external
writes, commits, or scope changes.

## Start and monitor

Run `serve` as a foreground long-running command. Use the execution
environment's persistent-session facility; do not detach it with `nohup`.
Always pass the target project explicitly:

```bash
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py serve task-name --cwd /absolute/path/to/project --prompt-stdin
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py status task-name --cwd /absolute/path/to/project
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py wait task-name 600 --cwd /absolute/path/to/project
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py result task-name --json --cwd /absolute/path/to/project
```

Substitute the resolved skill and project paths. Prefer
`--prompt-stdin` or `--prompt-file`; positional prompts are visible in the
process list. Durable conversations live in `.pi/sessions/<slug>.jsonl` and
runtime evidence in `.pi/workers/<slug>/`. Each busy episode creates
`runs/<run-id>/brief.md` and an atomically published `result.json`.

## Control a live worker

```bash
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py steer task-name "correction" --cwd /absolute/path/to/project
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py follow-up task-name "next task" --cwd /absolute/path/to/project
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py abort task-name --cwd /absolute/path/to/project
```

Use `steer` during an active turn. Use `follow-up` after settle to open a new
run in the warm session. A follow-up sent while busy joins the open run; avoid
stacking another until its acknowledgement and eventual settle are visible.

If a worker appears stuck, inspect `status`, `stderr.log`, and the tail of
`events.jsonl`. An `extension_ui_request` may be a blocking headless dialog.

## Verify and finish

Treat the final message and gate claims as evidence to check. Inspect the run
JSON, the complete Git diff, staged and untracked files, and rerun relevant
gates yourself. Follow [review-and-land.md](references/review-and-land.md),
especially when tests changed. For several tasks, use
[multi-task-queues.md](references/multi-task-queues.md).

Stop an idle worker when finished. If `status` reports `orphaned`, run
`recover` before serving the slug again.

```bash
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py stop task-name --timeout 15 --cwd /absolute/path/to/project
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py recover task-name --cwd /absolute/path/to/project
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py clean task-name --cwd /absolute/path/to/project
```

`clean` archives events, stderr, state, and run artifacts under `.pi/logs/`;
`--discard-logs` deletes them. Both preserve the durable session. Keep `.pi/`
out of version control unless the project explicitly tracks agent state.

Read [result-schema.md](references/result-schema.md) for state and result
fields, [rpc-protocol.md](references/rpc-protocol.md) for protocol details, and
[claude-code.md](references/claude-code.md) for Claude Code packaging notes.
