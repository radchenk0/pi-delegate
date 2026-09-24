# pi-delegate implementation plan

## Goal

Turn the current personal `pi-rpc-worker` into a source-controlled, installable
`pi-delegate` skill for Codex and Claude Code while preserving its long-lived
Pi RPC workflow.

The project directory is the source of truth. Personal installations point to
this checkout with supported skill-directory symlinks.

## Constraints

- Keep RPC, warm sessions, `steer`, queued `follow-up`, `abort`, and recovery.
- Keep project-local runtime and session data under `.pi/`.
- Never let Pi commit, reset, clean, or silently revert the worktree.
- Treat Pi's report and gate claims as unverified until the orchestrator checks
  the diff and reruns relevant gates.
- Preserve compatibility with runtime state created by the imported helper.
- Use only the Python standard library in the helper and tests.

## Release 1

### 1. Package and installation

- Rename the skill to `pi-delegate`.
- Keep the repository root as the skill root (`SKILL.md`, `scripts/`,
  `references/`, and `agents/`).
- Add a small installer that creates or checks these supported personal links:
  - Codex: `~/.agents/skills/pi-delegate`
  - Claude Code: `~/.claude/skills/pi-delegate`
- Refuse to replace unrelated paths unless the caller explicitly requests it.
- Document development, validation, installation, and uninstallation.

### 2. Delegation and review contracts

- Add a brief-writing reference covering task scope, exclusions, real project
  gates, Git safety, and the final report format.
- Add a review reference covering independent gate execution, changes to tests,
  staged/unstaged/untracked files, scope drift, and common generated-code risks.
- Add queue guidance: unrelated tasks use separate sessions; resume is for
  rework; one worktree per concurrent writer.

### 3. Run boundary

- Model one run as one busy episode:
  - an accepted prompt while idle opens the run;
  - queued follow-ups and steers join that run;
  - the next `agent_settled` closes it;
  - a rejected prompt, Pi death, timeout, or supervisor termination closes it
    through an explicit failure path.
- Persist at most one `openRun` in `state.json`.
- Give each run a monotonic identifier stored in state.
- Keep the worker-wide event stream; record event-line boundaries in each run
  rather than splitting the stream in this release.

### 4. Result contract

- Store `brief.md` and an atomically published `result.json` in
  `.pi/workers/<slug>/runs/<run-id>/`.
- Include status, timing, abort origin, worker health, session identity,
  requested and actual model, aggregate usage, final message, stop reason,
  Git snapshots, stderr tail, event boundaries, and error details.
- Publish terminal results only. Live state remains in `state.json`.
- Refuse stale results and expose the newest terminal run through `result`.

### 5. Input, Git evidence, and lifecycle

- Retain `--prompt-file`; add explicit prompt input from stdin.
- Record Git HEAD, porcelain status, staged/unstaged/untracked paths, and stash
  count before and after a run. Warn on a dirty starting tree without blocking
  or reverting it.
- Detect HEAD movement, stash changes, new staged changes, and vanished dirty
  paths. Mark attribution ambiguous when the run starts dirty.
- Handle `SIGHUP` in addition to `SIGTERM` and `SIGINT`.
- Publish a terminal result when Pi rejects a prompt or exits unexpectedly.
- Sweep the complete Pi process group with grace, recheck, and SIGKILL so a
  descendant that ignores SIGTERM cannot survive cleanup.

### 6. Tests

- Build a fake Pi executable speaking the JSONL RPC protocol.
- Characterize start/run/settle, idle follow-up conversion, queued follow-ups,
  request acknowledgement, stale-result rejection, and orphan detection.
- Cover atomic results, prompt rejection, Pi death, supervisor termination,
  split UTF-8, dirty Git evidence, and a descendant that ignores SIGTERM.
- Keep one small opt-in live Pi smoke test documented but outside the default
  deterministic suite.

## Release 2 candidates

- Per-run watchdog: RPC abort, grace, process-group TERM, then KILL. Disabled by
  default until real workloads establish a useful default.
- Process-level read-only mode using `--tools read,grep,find,ls`.
- Native Pi project-trust behavior by default, with explicit approve/no-approve
  overrides recorded in state and results.
- Bounded `pi --version` preflight and clearer unavailable/startup statuses.
- Run retention controls and optional path-scope enforcement.
- Named Pi profiles after the run/result contract is stable.

## Verification and rollout

1. Run the standard-library unit/integration suite with the fake Pi.
2. Validate `SKILL.md` frontmatter and supporting-resource links.
3. Run a live Pi smoke operation in an isolated temporary Git repository.
4. Ask an independent Pi session to review the diff read-only.
5. Install the checkout through symlinks for Codex and Claude Code.
6. Remove the legacy `~/.codex/skills/pi-rpc-worker` copy only after both new
   installations resolve to this checkout and the current project guidance has
   been updated to `$pi-delegate`.
