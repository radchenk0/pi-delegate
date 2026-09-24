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
- `--tool-output 0`: hide tool-result bodies;
- `--history 0`: show only events arriving after attachment;
- `--raw`: print unformatted JSON events;
- `--stay`: remain attached across settled runs;
- `--no-follow`: render history and exit.

## Herdr

Use Herdr only when the user asked for it and `HERDR_ENV=1` is present. Follow
the installed Herdr skill rather than guessing pane identifiers or command
syntax:

1. Inspect the current pane layout.
2. Split a background sibling pane in the current tab with the same project
   directory and without changing focus.
3. Run the `watch` command in that pane.
4. Leave the pane open for the user; do not close panes you did not create.

The Herdr pane provides the visible live surface. The renderer remains the
same, so output and filtering are consistent outside Herdr.

## Fallback

When `HERDR_ENV` is missing, do not attempt to control a focused Herdr session
from the outside. Start `watch` using the host's tracked foreground or
persistent process facility. A plain terminal can also run the command.

Never attach `pi --resume` to a session that already has a live RPC worker;
concurrent Pi processes could write the same session JSONL. `watch` is safe
because it reads only the supervisor's event copy.
