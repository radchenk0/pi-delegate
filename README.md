# pi-delegate

An installable agent skill for delegating bounded work to long-lived `pi`
coding-agent sessions over its JSONL RPC protocol.

This repository is the source of truth. The installer creates a personal skill
directory symlink, so edits in this checkout are immediately available.

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
