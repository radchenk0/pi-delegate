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
attribution is `ambiguous` when the run starts dirty and `unavailable` outside
Git. `state.json` is mutable live state; `result.json` is terminal evidence.
