---
description: What to do when a TenetX policy decision blocks or questions a tool call
trigger: model_decision
---

# Handling a TenetX policy decision

A TenetX verdict arrives as a non-zero hook exit with a message starting
`TenetX:`. Treat it as an instruction from the organization, not an obstacle.

## Do

1. **Quote the verdict verbatim** to the user, including the rule name or
   reason code. They may need it to request an exception.
2. **Say what you were trying to do** and which resource was involved, so the
   user can judge whether the rule is right for this case.
3. **Stop that branch of work** and continue with anything unaffected. Report
   at the end exactly what you could not complete and why.
4. **Offer the legitimate route** if there is one — an approval request, a
   narrower scope, a redacted input, an owner to ask.

## Do not

- Retry the identical call hoping for a different verdict.
- Reword, re-encode, or split the call to get past the matcher.
- Wrap it in a script, a heredoc, an alias, or another tool to change how it
  looks to the hook.
- Fetch the same data from a different path, mirror, or credential.
- Touch hook configuration, guard credentials, or the guard runtime.

## When governance looks absent

If a session seems ungoverned — no verdicts at all on calls you would expect to
be checked — that is worth surfacing rather than enjoying. Devin documents
plugin hooks as best effort and fail open, so a misconfigured secret shows up as
silence. `/tenetx-governance:tenetx-doctor` reads the local breadcrumb log and
says which it is.
