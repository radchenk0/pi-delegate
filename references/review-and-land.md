# Review and landing checklist

1. Read `result --json`; check terminal status, model, event range, stderr, and
   Git evidence. A dirty baseline makes attribution ambiguous.
2. Inspect status, staged changes, untracked files, and the complete diff.
3. Review changed tests first. Treat skips, broader tolerances, weakened exact
   assertions, catch-all exceptions, and replaced fixtures as a failed gate
   until the task justifies them.
4. Look for fixture values in production paths, fallback returns, nonexistent
   APIs, swallowed errors, unrelated cleanup, and unwanted generated files.
5. Run relevant gates independently. Do not trust a prose claim that they pass.
6. Surface design choices and scope changes instead of treating them as implied
   authorization.
7. Commit or publish only under the orchestrator's existing authorization.
