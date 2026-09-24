# Writing a worker brief

Use a file or stdin so the brief does not appear in the process list.

```xml
<task>
State the concrete outcome, relevant inputs, exact file scope, and exclusions.
</task>
<verification_loop>
List real project commands. Require inspection, fixes within scope, and reruns.
</verification_loop>
<action_safety>
Do not commit, reset, clean, stash, publish, deploy, or contact external systems.
Stop and report when completion requires broader scope or new authorization.
</action_safety>
<structured_output_contract>
Report changes and rationale; files touched; gates and outcomes; remaining risk.
</structured_output_contract>
```

Carry decisions from earlier workers into each new brief. A different session
does not know them.
