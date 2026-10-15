# Claude Code compatibility

Claude Code discovers personal skills at `~/.claude/skills/<name>/SKILL.md`.
This package uses the same `SKILL.md` format as Codex and keeps all commands
host neutral by resolving the directory that contains the loaded skill.

`CLAUDE.md` is project guidance, not a replacement skill entrypoint. A project
may mention `$pi-delegate` in its own `CLAUDE.md`, but installation does not
edit that file.

Install explicitly with `python3 scripts/install.py --target claude`. The
default target is Codex, so Claude files are not created accidentally.

## Running workers from Claude Code

The examples below use the standalone Claude installation at
`~/.claude/skills/pi-delegate/scripts/pi_rpc_worker.py`. In a plugin installation,
resolve the loaded skill directory (`skills/pi-delegate` inside the plugin)
and use its helper by absolute path instead.

**`serve` runs as a background Bash command.** This is the host's
persistent-session facility the skill asks for. Use `run_in_background: true`
in the orchestrating conversation, not a short-lived foreground subagent.
Keep the returned task handle. Do not detach with `&` or `nohup`.

Background lifetime depends on the installed Claude Code version and session
mode. In v2.1.288 and later, interactive local sessions have no background
time limit. Unattended sessions have a separate time budget: normally 30
minutes, with a two-hour ceiling unless configuration raises it. Plain-text
`-p` runs default to ten minutes. The limits introduced in v2.1.285 applied
to interactive sessions too before v2.1.288. A foreground subagent's commands
stop when that subagent ends, regardless of these budgets.

For unattended runs, request a supported `timeout` long enough for the task.
If the host stops the supervisor, inspect `status`; use `recover` for an
orphaned worker, then `serve` the same slug to resume its durable session.
Do not assume every installation accepts an arbitrary two-hour timeout.
See the official [Bash background lifetime reference](https://code.claude.com/docs/en/tools-reference#time-limit-for-background-commands).

**`wait` runs in the foreground in bounded calls.** The usual foreground
ceiling is ten minutes, configurable through `BASH_DEFAULT_TIMEOUT_MS` and
`BASH_MAX_TIMEOUT_MS`. Use short waits to keep the orchestrator responsive:

```bash
python3 ~/.claude/skills/pi-delegate/scripts/pi_rpc_worker.py wait task-name 30 --return-on-ui --cwd /absolute/path/to/project
```

Exit 1 means call again. Exit 3 means a dialog is blocking the worker: read
the printed title and options, answer with `ui-respond`, and wait again.
Without `--return-on-ui`, a dialog consumes the wait window. If Claude moves
the wait command into the background, collect that task's result before
starting another wait; a Bash-tool return alone is not worker completion.

**Pass briefs as files.** Write the brief with the Write tool and pass
`--prompt-file`. Positional prompts are visible in the process list, and long
heredocs make permission rules harder to match.

## Permissions

Claude Code matches allow rules against the literal command text. Invoke the
helper by one stable literal path, not through a shell variable such as
`$W`, and allow that prefix once in `~/.claude/settings.json`:

```json
{
  "permissions": {
    "allow": [
      "Bash(python3 ~/.claude/skills/pi-delegate/scripts/pi_rpc_worker.py:*)"
    ]
  }
}
```

In auto mode, an unlisted call into a freshly cloned checkout may be refused
as running external code. The allow rule is the intended way to approve it.

Pi's own permission extensions are separate from Claude Code's. They reach
the worker as blocking `select` or `confirm` dialogs and must be answered
through `ui-respond`. Claude Code's allowlist does not cover them.

## Repository hygiene

Workers write sessions, events, and run evidence under `<project>/.pi/`. Add
`.pi/` to the global gitignore (`git config --global core.excludesfile`) so
that every project ignores it without editing tracked files.

## Native subagents

Claude Code's `Agent` tool and pi workers are different mechanisms. Use this
skill when the work should run on Pi's models and tools. For running Pi as
the implementer inside superpowers' subagent-driven development, see
[superpowers-sdd.md](superpowers-sdd.md).
