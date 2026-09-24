# Claude Code compatibility

Claude Code discovers personal skills at `~/.claude/skills/<name>/SKILL.md`.
This package uses the same `SKILL.md` format as Codex and keeps all commands
host neutral by resolving the directory that contains the loaded skill.

`CLAUDE.md` is project guidance, not a replacement skill entrypoint. A project
may mention `$pi-delegate` in its own `CLAUDE.md`, but installation does not
edit that file.

Install explicitly with `python3 scripts/install.py --target claude`. The
default target is Codex, so Claude files are not created accidentally.
