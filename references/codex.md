# Codex execution and packaging

Pi Delegate can be installed as a standalone skill or as a plugin. Resolve the
directory containing the loaded `SKILL.md` and invoke its helper by absolute
path. A plugin bundles the helper under `skills/pi-delegate/scripts/`; it does
not depend on this source checkout. Avoid installing both forms in one scope.
The standalone invocation is `$pi-delegate`; native plugin discovery exposes
`$pi-delegate:pi-delegate`. Packaged UI metadata uses that qualified name.

## Persistent supervisor

Start `serve` through Codex's tracked execution tool with a short initial
yield. Keep the returned execution-session handle and leave the supervisor
running while using separate calls for `wait`, `status`, and `ui-respond`.
Poll the execution handle for startup errors or supervisor output. Do not
detach with `nohup` or confuse an execution-tool yield with Pi completion.

Use bounded waits with `--return-on-ui`. An exit of 3 requires inspecting the
dialog and answering it according to the task's existing authorization.
Stopping a monitor does not stop the worker; use the helper's `stop` command
when finished and collect the supervisor's final output.

## Herdr context

When `HERDR_ENV=1` reaches execution tools, follow the default monitoring and
cleanup workflow in [live-monitoring.md](live-monitoring.md). A rendered Codex
TUI inside Herdr does not prove its separate command executor inherited those
variables. Codex may attach to a shared app-server started in another
environment. CLI 0.162.1 provides `--no-daemon` to bypass that shared server;
the user can resume or fork the conversation from the originating Herdr shell
and then verify the variables again. Do not fabricate pane context or stop a
shared server serving other sessions.

## Development install versus plugin release

`scripts/install.py --target codex` links the standalone source skill, so
source edits are immediately visible. A native plugin installation copies a
build into the host's plugin cache. Build with `scripts/package_plugin.py`,
install through the generated marketplace, and rebuild/update when releasing
new code. Neither mechanism installs Pi or supplies provider credentials.
