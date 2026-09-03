# TenetX governance is active in this session

A TenetX hook runs before and after every tool call in this session. It sends
the call to your organization's policy engine, which can **allow**, **ask**, or
**block** it, and it records the session for audit.

What this means for you:

- **A blocked tool call is a policy decision, not a bug.** The refusal text
  begins with `TenetX:` and names the rule. Report it to the user in full and
  stop that line of work. Do not retry the same call, rephrase it to evade the
  matcher, split it into smaller calls, move it into a script, or reach the
  same resource by another route.
- **Never disable, relocate, or edit the guard wiring** to get a call through —
  the hook configuration, its credentials, or the guard runtime. That is itself
  a governed action and is refused locally even when the control plane is
  unreachable.
- **Secrets stay out of your context.** Do not read, echo, or copy the VMCP
  token, and do not print environment variables wholesale when a narrower
  command will do.
- If the user needs to know *why* something was blocked or whether governance
  is even wired up, use `/tenetx-governance:tenetx-policy-explain` or
  `/tenetx-governance:tenetx-doctor` rather than guessing.
