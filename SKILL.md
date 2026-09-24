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
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py list --cwd /absolute/path/to/project
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py status task-name --cwd /absolute/path/to/project
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py wait task-name 600 --cwd /absolute/path/to/project
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py result task-name --json --cwd /absolute/path/to/project
```

`list` shows every worker slug under the project with its live status, PIDs,
and last event; `status` shows one worker's full state JSON.

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
`events.jsonl`. A blocking extension dialog shows up in `status` as
`pendingUi` (methods `select`, `confirm`, `input`, `editor`); the worker
stays alive but waits for an answer. Fire-and-forget UI requests
(`notify`, `setStatus`, `setWidget`, `setTitle`, `set_editor_text`) never
block and need no answer. Answer a pending dialog explicitly:

```bash
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py ui-respond task-name --confirmed --cwd /absolute/path/to/project
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py ui-respond task-name --value "Allow" --cwd /absolute/path/to/project
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py ui-respond task-name --cancelled --cwd /absolute/path/to/project
```

Use `--confirmed` for `confirm` dialogs, `--value ANSWER` for
`select`/`input`/`editor` dialogs, and `--cancelled` to dismiss any dialog.
Exactly one form is accepted per call; the pending id is taken from state,
so the response always matches the open dialog.

## Verify and finish

Treat the final message and gate claims as evidence to check. Inspect the run
JSON, the complete Git diff, staged and untracked files, and rerun relevant
gates yourself. Follow [review-and-land.md](references/review-and-land.md),
especially when tests changed. For several tasks, use
[multi-task-queues.md](references/multi-task-queues.md).

Stop an idle worker when finished. If `status` reports `orphaned`, run
`recover` before serving the slug again.

```bash
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py stop task-name --cwd /absolute/path/to/project
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py recover task-name --cwd /absolute/path/to/project
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py clean task-name --cwd /absolute/path/to/project
```

If `recover` reports that the stored Pi PID belongs to a different process
(the PID was reused), it refuses to signal it. Confirm the process is not
yours, then rerun with `recover task-name --forget --cwd ...`: it clears the
orphan state without touching that process, keeps every run/result/session
artifact, and leaves the worker restartable.

`clean` archives events, stderr, state, and run artifacts under `.pi/logs/`;
`--discard-logs` deletes them. Both preserve the durable session. Keep `.pi/`
out of version control unless the project explicitly tracks agent state.

## Environment notes

- If Pi cannot reach the user's provider settings or credentials inside a
  restricted sandbox, rerun the supervisor with the narrowest escalation Pi
  actually needs. Never put credentials in prompts, briefs, or logs.
- `supervisor.pid` in the runtime directory is diagnostic; `state.json` is
  the source of truth for liveness.
- Git snapshot evidence: if Git commands fail or time out, the run result's
  `gitEvidence.attribution` becomes `degraded` and `snapshotDegraded` lists
  what was unavailable. Treat such runs' touched-file evidence as partial.
- Live Pi smoke (opt-in, only when `pi` is configured and real model calls
  are acceptable): in a disposable git repo, `serve` a trivial task such as
  "list the files and say done", then `wait`, `result --json`, `stop`,
  `clean`. The default test suite uses a deterministic fake `pi` and never
  calls a model.

Read [result-schema.md](references/result-schema.md) for state and result
fields, [rpc-protocol.md](references/rpc-protocol.md) for protocol details, and
[claude-code.md](references/claude-code.md) for Claude Code packaging notes.
