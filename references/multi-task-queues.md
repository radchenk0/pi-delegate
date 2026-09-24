# Multiple tasks and workers

- Use a fresh slug for unrelated work. Reuse a session for rework or a direct
  continuation that benefits from its conversation.
- Run writer tasks sequentially in one worktree. Concurrent writers need
  separate worktrees and disjoint file scopes.
- Start each task from an understood tree. A dirty tree is allowed, but change
  attribution becomes ambiguous.
- Pass forward decisions, names, constraints, and interface changes.
- Review and land one bounded task before dispatching a dependent task.
- A busy follow-up belongs to the current run. Prefer waiting for settle and
  sending the next task from idle.
