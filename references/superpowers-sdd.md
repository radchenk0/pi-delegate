# Pi as the implementer in superpowers' subagent-driven development

[superpowers](https://github.com/obra/superpowers)' subagent-driven
development (SDD) executes a plan one task at a time. A fresh implementer
works on each task, a reviewer checks it, a fix loop follows, and a final
whole-branch review closes the plan. The implementer role has a clear
contract: it reads a task-brief file, writes a report file, and returns a
short status block. So it can be filled by a Pi worker while the orchestrator
and its reviewers stay on the host's native subagents.

Run SDD exactly as written, with one substitution: every implementer
dispatch is a Pi worker instead of a native subagent. That covers the first
dispatch, the fix rounds, the escalation, and the final fix wave.

## Role mapping

| SDD step | With pi-delegate |
|---|---|
| Dispatch implementer | Write the dispatch (SDD's implementer template, adapted below) to a file next to the task brief; `serve <slug> --prompt-file <dispatch> --model <id> --thinking <level>` |
| Wait for the report | `wait <slug> <seconds> --return-on-ui`, looped. Answer dialogs (exit 3) with `ui-respond` |
| Read the status block | `result <slug>`. The full report is the report file named in the dispatch |
| Answer NEEDS_CONTEXT | `follow-up <slug> "<answers>"`, then wait |
| Fix rounds 1-3 ("resume the implementer") | `follow-up <slug> "<findings verbatim>"` in the same warm session |
| Fix rounds 4-5 ("fresh, more capable implementer") | `stop` the old slug; `serve` a new slug with a stronger model or a higher `--thinking` level, carrying the brief, the report file, and the open findings |
| Final review fix wave | One worker with the complete findings list |
| Task complete | `stop`, then `clean` (keeps the session) |

Use one slug per task, for example `sdd-<plan>-t<N>`. SDD already forbids
parallel implementers. Never run two Pi workers that write to the same
worktree.

## Adapting the implementer prompt

SDD's implementer template assumes the subagent can ask questions mid-task.
A Pi run cannot pause to ask. Replace "ask questions now / while you work"
with: *if something blocks correct completion, stop and reply NEEDS_CONTEXT
or BLOCKED with the specifics; do not guess*. Keep the template's
no-subagents rule. If Pi has planning or subagent packages installed (for
example superpowers itself or pi-subagents), also tell it the plan is fixed
and not to run planning skills or spawn helpers. Keep the four-status reply
block unchanged, so the orchestrator's handling stays exactly as SDD
describes.

## Commits

SDD's implementer commits each task, and its review package diffs
`BASE..HEAD` commits. Decide once per plan, from the project's own rules:

- **The project allows agent commits:** let the worker commit, as SDD
  expects. A permission extension in Pi typically raises a dialog for
  `git commit`. Answer it with the one-time option, never a session-wide
  one.
- **The user commits manually:** tell the worker not to commit, stage, or
  stash. Take per-task diffs from working-tree snapshots instead of commits.
  This writes a tree object through a throwaway index and never touches the
  real index, HEAD, or refs:

  ```bash
  tmp=$(mktemp -d)
  cp "$(git rev-parse --path-format=absolute --git-path index)" "$tmp/index" 2>/dev/null
  GIT_INDEX_FILE="$tmp/index" git add -A -- . ':(exclude).pi' ':(exclude).superpowers'
  GIT_INDEX_FILE="$tmp/index" git write-tree   # snapshot id; use as BASE / HEAD
  rm -rf "$tmp"
  ```

  Snapshot before dispatch and after DONE, then build the review package
  from `git diff -U10 <base-tree> <head-tree>`. Uncommitted changes the user
  had before the task are not in the diff. In this mode a moved HEAD after a
  run is a defect to report, not to reset.

## Review the evidence, not the claim

SDD's reviewers already treat the implementer's report as unverified. With
Pi, also read `result --json`: check that `status` is `completed`, that the
`actualModel` is the one you asked for, and that `gitEvidence` matches the
commit mode. The orchestrator still runs the project's gates itself before
completing the plan.
