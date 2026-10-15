# Live monitoring

`watch` is a read-only renderer over `.pi/workers/<slug>/events.jsonl`. It does
not attach another Pi process, write to the session, or compete with the RPC
supervisor.

```bash
python3 /absolute/path/to/pi-delegate/scripts/pi_rpc_worker.py watch task-name \
  --cwd /absolute/path/to/project
```

By default it replays 200 recent events, follows new events, and exits when the
worker becomes idle or terminal. Useful options:

- `--thinking`: include Pi's streamed thinking;
- `--full`: show complete tool arguments and output, overriding display limits;
- `--tool-args N`: cap argument display at N characters; 0 means unlimited;
- `--tool-output N`: cap text across updates and result per tool call (default
  1200); 0 hides tool output;
- `--no-tool-updates`: show final tool results only;
- `--history 0`: show only events arriving after attachment;
- `--raw`: print unformatted JSON events;
- `--stay`: remain attached across settled runs;
- `--no-follow`: render history and exit.

The formatted view shows assistant text, tool starts, intermediate results,
tool completion/errors, full blocking-dialog fields and options, notifications,
compaction, and RPC/extension errors. Argument display defaults to 800
characters. Truncated output is marked and the stored event log is unaffected.
Cumulative tool-result snapshots print only new text; replacement snapshots
are shown again. Non-text content (for example images) remains available in
the raw event record rather than being rendered as terminal images.

`watch` does not accept answers or change worker state. Answer a displayed
dialog with `ui-respond` through the orchestrator. `--thinking` remains opt-in
even with `--full`. Read `stderr.log` for process diagnostics outside RPC.

## Herdr

When `HERDR_ENV=1`, create a separate monitoring pane for each worker by
default. The user does not need to request monitoring separately. Honor an
explicit request to run without a pane or to keep it open after completion.
Follow the installed Herdr skill (or read `herdr --skill` if unavailable)
rather than guessing pane identifiers or command syntax:

1. Inspect the current pane layout.
2. Split a background sibling pane in the current tab with the same project
   directory and without changing focus.
3. Record the returned pane ID and run `watch` there once the worker's runtime
   exists. Use `--stay` when attaching before an idle follow-up, so the watcher
   does not exit before the new turn begins. Keep `serve` in the host's tracked
   persistent execution facility.
4. When the run settles, read `result --json` and capture the answer or error
   for the report, then close the monitoring pane you created. A pending
   dialog or a wait timeout means the worker still needs attention; keep its
   pane open. Use worker state and run evidence to establish completion,
   rather than treating the watcher's exit alone as completion.
5. Close the created pane on startup failure as well. If the user asked to
   keep it open, leave it for inspection. Never close a pane you did not
   create as part of this monitoring workflow.

Pane cleanup applies only to runs monitored in Herdr, and only when you
created the pane and recorded its ID. Outside Herdr, do not issue pane-close
commands. Use `herdr pane close <recorded-pane-id>` for cleanup. Read the
result before closing so the report does not depend on terminal scrollback. Closing a
monitoring pane does not stop the RPC worker: stop an idle worker when the
task is finished, as described in `SKILL.md`. For a later follow-up in a warm
session, open a new monitoring pane before submitting the follow-up.

The Herdr pane provides the visible live surface through the read-only
watcher. Never start a second interactive Pi process on the same session.
The renderer remains the same, so output and filtering are consistent outside
Herdr.

## Fallback

When `HERDR_ENV` is missing, do not attempt to control a focused Herdr session
from the outside. Start `watch` using the host's tracked foreground or
persistent process facility. A plain terminal can also run the command.

If a Codex TUI is visibly inside Herdr but shell tools lack `HERDR_*`, check
whether it attached to a shared app-server started outside Herdr. Codex CLI
0.162.1 supports `codex --no-daemon resume <session-id>` to resume with a local
server; verify the variables again afterward. Do not stop a shared daemon
that may host other conversations.

Never attach `pi --resume` to a session that already has a live RPC worker;
concurrent Pi processes could write the same session JSONL. `watch` is safe
because it reads only the supervisor's event copy.
