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

The commands below assume the Claude target is installed, so the helper lives at
`~/.claude/skills/pi-delegate/scripts/pi_rpc_worker.py`.

**`serve` runs as a background Bash command.** This is the host's
persistent-session facility the skill asks for. Call the Bash tool with
`run_in_background: true` and the largest `timeout` (`7200000`, two hours).
The harness notifies the session when the command exits. Do not detach it with
`&` or `nohup` instead. A background command is stopped at its timeout, so a
worker that must outlive two hours needs `stop` and a later `serve` with the
same slug, which resumes the session. If the supervisor was killed without
cleanup, `status` reports `orphaned`: run `recover` before serving again.

**`wait` runs in the foreground in bounded calls.** A foreground Bash call is
capped at ten minutes, so keep each wait under it and loop:

```bash
python3 ~/.claude/skills/pi-delegate/scripts/pi_rpc_worker.py wait task-name 590 --return-on-ui --cwd /absolute/path/to/project
```

Exit 1 means call again. Exit 3 means a dialog is blocking the worker: read
the printed title and options, answer with `ui-respond`, and wait again.
Without `--return-on-ui`, a dialog consumes the whole ten-minute window.

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
