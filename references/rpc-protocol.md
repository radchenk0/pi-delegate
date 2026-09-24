# Pi RPC notes

Read this reference only when debugging, extending, or bypassing the bundled helper.

## Process and framing

- Start Pi with `pi --mode rpc --session <path> -a` in the target working directory.
- Commands and responses are JSON objects separated by LF (`\n`).
- Keep stdin open for the RPC process lifetime. Closing the supervisor side normally gives Pi EOF.
- A PTY can alter input handling; use ordinary pipes for JSONL.
- Never combine `--no-session` with `--session`.
- Hold an exclusive lock associated with the session for the complete supervisor lifetime.
- Put Pi in its own process group. A new supervisor must refuse the session while the previous supervisor, verified Pi child, or lock remains live.

## Commands and acknowledgements

```json
{"id":"request-id","type":"prompt","message":"Start or continue work"}
{"id":"request-id","type":"steer","message":"Change direction during the running turn"}
{"id":"request-id","type":"follow_up","message":"Process after the running turn"}
{"id":"request-id","type":"abort"}
{"id":"request-id","type":"get_state"}
```

Correlate acceptance with the Pi `response` carrying the same `id`. A successful FIFO write confirms transport to the supervisor only.

When idle, send a new `prompt`. While streaming, `follow_up` queues later work. The default follow-up mode processes one queued message per completion; `agent_settled` is emitted only after retry, compaction, and queued continuations are exhausted. Do not equate one `agent_settled` with one queued message.

## Events to watch

- `response`: command acceptance or rejection;
- `agent_start`: session-level processing began;
- `turn_start` and `turn_end`: individual model/tool turns;
- `tool_execution_start` and `tool_execution_end`: tool activity;
- `message_end`: completed assistant message;
- `agent_settled`: no automatic retry or queued continuation remains;
- `extension_ui_request`: an extension requested UI interaction and may be blocking headless execution.

Persist the raw stream for diagnosis. A cached result is current only when a fresh assistant `message_end` occurred during the operation that reached the latest `agent_settled`.

## Session lifecycle

The Pi process and Pi session have different lifetimes. Stopping the process is safe when a session file is enabled. Resume with a new RPC process in the original working directory and the same exact `--session` path. Use `--fork` only when a separate history branch is intended.

If the supervisor dies, ordinary pipe EOF should cause Pi to exit, but recovery must still detect a surviving verified child before allowing another writer to open the same session. Never kill a stored PID without confirming that its current command belongs to the expected Pi RPC session.
