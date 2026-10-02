# Run and result contract

One run is the busy episode opened by a prompt accepted while idle. Steers and
busy follow-ups join it. Settle, rejection, Pi exit, abort, or supervisor loss
closes it.

Artifacts live at `.pi/workers/<slug>/runs/run-NNNNNN/`:

- `brief.md`: exact opening prompt, mode `0600`;
- `result.json`: atomically published terminal record, mode `0600`.

Schema `pi-delegate.result.v1` records status, timing, abort origin, worker
health, session identity, requested and actual model, usage, stop reason, final
message, Git snapshots, changed status paths, event boundaries, stderr tail,
RPC errors, and process exit details.

Statuses are `completed`, `failed`, `aborted`, and `orphaned`. `touchedFiles`
compares Git porcelain before and after. It does not prove authorship;
attribution is `ambiguous` when the run starts dirty, `degraded` when Git
evidence was incomplete (a snapshot command failed or timed out; the exact
commands are listed in `gitEvidence.snapshotDegraded`), and `unavailable`
outside Git.

`.pi/workers/<slug>/result.md` (the latest settled assistant text) is written
with mode `0600`. `state.json` is mutable live state; `result.json` is
terminal evidence. `supervisor.pid` is a diagnostic duplicate of the
supervisor PID; `state.json` is authoritative for liveness.

Useful `state.json` fields beyond liveness: `openRun` (the active run and its
queued follow-ups/steers), `lastRunId`/`lastRunStatus`, `pendingUi` (a blocking
extension dialog awaiting `ui-respond`: its `id`, `method`, and whichever of
`title`, `message`, `options`, `placeholder`, and `timeout` Pi sent, text
capped at 4000 characters; `null` when none),
`lastControlRequestId`/`lastRpcResponseId` (control-plane correlation), and
`lastError`. State imported from the legacy helper has no `lastRunId`; the
`result` command then falls back to the cached `result.md` when the state was
marked fresh by a settled turn (`settledResultFresh`), printing a warning that
no run evidence exists.
