---
name: tenetx-policy-explain
description: Explain a TenetX policy verdict that blocked or questioned a tool call, and what the legitimate next step is
argument-hint: "[the verdict text]"
allowed-tools: ["read_code", "run_command"]
triggers: [user, model]
---

# Explain a TenetX verdict

Use this when a tool call came back refused with a message beginning `TenetX:`,
or when the user asks why something was blocked.

## 1. Read the verdict as written

Quote it to the user in full, unedited. It names the rule that fired, and that
name is what they will need to request an exception. Do not paraphrase it into
something vaguer, and do not soften it.

## 2. Name what was attempted

State the tool, the target, and the intent in one line — "reading
`config/prod.env` to find the database host". The user is deciding whether the
rule is correct *for this case*, and cannot do that without knowing what you
were doing.

## 3. Classify it

| Verdict shape | Meaning | Next step |
| --- | --- | --- |
| Block naming a data-classification rule (PII, secrets, DLP) | the input or target carries protected data | the data itself must change — redact, use a fixture, or use a scoped credential |
| Block naming a resource or path rule | the target is out of scope for this agent | the user requests access, or points you at an in-scope equivalent |
| Block naming guard integrity (`guard_integrity`) | the call would alter TenetX's own wiring, credentials, or guard runtime | never work around this; if it fired on ordinary work, the command probably just *mentions* a protected path — narrow the command |
| Ask / approval required | a human must confirm | present exactly what needs approving and wait |

## 4. Offer the legitimate route, or stop

Say plainly which of these applies:

- **There is a compliant way** — describe it and offer to do it.
- **The rule looks wrong here** — say why, and tell the user this is an
  exception request for a policy owner. Do not attempt the call again.
- **There is no way to finish this task under policy** — say so, and list what
  else you completed.

## What never counts as an explanation

Retrying, rewording, re-encoding, splitting a command, moving it into a script,
or reaching the same resource by a different route. Any of those is evasion,
and the verdict already told you the answer.

If the block was unexpected because you did not think governance was even
active, run `/tenetx-governance:tenetx-doctor` first — the wiring may be
reporting something more useful than the verdict itself.
