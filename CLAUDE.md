# Claude Code project guidance

This repository is both the source and installable root of the `pi-delegate`
skill. Claude Code discovers the skill through `SKILL.md`; this `CLAUDE.md`
applies when Claude works on the repository itself.

- Keep `SKILL.md` host neutral. Do not add environment-specific home variables
  or fixed host installation paths to command examples.
- Preserve standard-library-only Python compatibility.
- Run `python3 -m unittest discover -s tests -v` after helper or installer
  changes.
- Do not install the Claude target unless the user explicitly asks for it.
