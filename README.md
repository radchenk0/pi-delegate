# pi-delegate

An installable agent skill for delegating bounded work to long-lived `pi`
coding-agent sessions over its JSONL RPC protocol.

This repository is the source of truth. The installer creates a personal skill
directory symlink, so edits in this checkout are immediately available.

## Requirements

- macOS or Linux (the helper uses POSIX FIFOs, `fcntl` locks and process
  groups);
- Python 3, standard library only;
- `git` (run evidence records Git snapshots);
- [Pi](https://github.com/earendil-works/pi-mono) on `PATH` with at least one
  working model:

  ```bash
  npm install -g @earendil-works/pi-coding-agent
  pi                  # run /login for a subscription provider, or configure an
                      # API-key or custom provider in ~/.pi/agent/models.json
  pi --list-models    # note the provider/id you want workers to use
  ```

## Quick start

1. Clone and link the skill for your agent (details in [Install](#install)):

   ```bash
   git clone https://github.com/radchenk0/pi-delegate ~/pi-delegate
   cd ~/pi-delegate
   python3 scripts/install.py --target claude   # or: --target codex
   ```

2. Keep worker runtime files out of every project's `git status` by adding
   `.pi/` to your global gitignore (the file named by
   `git config --global core.excludesfile`; set one if it is empty).

3. Smoke-test a worker by hand in a throwaway repository. Start `serve` in the
   foreground in one terminal:

   ```bash
   mkdir -p /tmp/pi-smoke && cd /tmp/pi-smoke && git init -q
   echo "List the files here and reply 'done'. Do not modify anything." | \
     python3 ~/pi-delegate/scripts/pi_rpc_worker.py serve smoke \
       --cwd /tmp/pi-smoke --model <provider/id> --prompt-stdin
   ```

   Then, in a second terminal:

   ```bash
   python3 ~/pi-delegate/scripts/pi_rpc_worker.py wait smoke 300 --return-on-ui --cwd /tmp/pi-smoke
   python3 ~/pi-delegate/scripts/pi_rpc_worker.py result smoke --cwd /tmp/pi-smoke
   python3 ~/pi-delegate/scripts/pi_rpc_worker.py stop smoke --cwd /tmp/pi-smoke
   ```

   If `serve` exits at once, its error is printed in the first terminal; the
   usual causes are `pi` missing from `PATH` or a model id that
   `pi --list-models` does not show. If `wait` exits 3, a Pi extension is
   asking for confirmation; answer it with `ui-respond` (see
   [SKILL.md](SKILL.md)).

4. Ask your agent to delegate, naming the model:

   ```text
   Use pi-delegate to have a Pi worker on <provider/id> implement <task>
   in this repo, then review the diff and run the tests yourself.
   ```

   The agent writes the brief, starts the worker, waits, and reviews the
   result. Pi has no sandbox: the brief tells it not to commit or touch files
   outside the task, and the agent checks the diff and Git evidence afterwards.

Claude Code users should also read
[references/claude-code.md](references/claude-code.md) (background `serve`,
bounded `wait`, the permission allow rule). To use Pi as the implementer in
superpowers' subagent-driven development, see
[references/superpowers-sdd.md](references/superpowers-sdd.md).

## Install

Codex uses `~/.agents/skills/pi-delegate`:

```bash
python3 scripts/install.py --target codex
```

Claude Code support is optional and uses `~/.claude/skills/pi-delegate`:

```bash
python3 scripts/install.py --target claude
```

Existing unrelated paths are refused. `--replace` moves one to a timestamped
sibling backup before linking. `--uninstall` removes only a symlink resolving
to this checkout.

## Develop and validate

The helper uses only the Python standard library and requires `pi` on `PATH`.

```bash
python3 -m unittest discover -s tests -v
python3 scripts/pi_rpc_worker.py --help
```

Runtime artifacts belong to the delegated project under `.pi/`. See
[PLAN.md](PLAN.md) for the release contract and [SKILL.md](SKILL.md) for
agent-facing usage. `CLAUDE.md` contains repository guidance for Claude Code;
skill discovery still uses `SKILL.md`.

`watch` renders the live Pi event stream in a terminal. When an orchestrator
runs inside Herdr (`HERDR_ENV=1`), it opens a separate background monitoring
pane for each worker by default and closes it after reading the completed
run's result. The user can request no pane or ask to keep it open. Outside
Herdr, workers use the host's normal persistent process facility. See
[live monitoring](references/live-monitoring.md).
