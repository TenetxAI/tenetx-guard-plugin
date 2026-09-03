---
name: tenetx-doctor
description: Check whether TenetX governance and capture are actually working in this session, and explain any gaps
argument-hint: "[--verbose]"
allowed-tools: ["read_code", "run_command"]
triggers: [user, model]
---

# Is TenetX actually guarding this session?

Devin runs plugin hooks best effort and **fails open**: if a secret is missing
or the control plane is unreachable, the session continues silently and
ungoverned. This skill turns that silence into an answer.

Report findings as: **Governed**, **Ungoverned (reason)**, or **Unknown**.

## 1. Is the hook wired at all?

The plugin ships its hook config at both `hooks.json` and `hooks/hooks.json`
(Devin reads whichever its manifest layout declares). Confirm the plugin is
installed and its hooks were loaded:

```bash
devin plugins list
devin plugins info tenetx-governance
```

If the plugin is not listed, nothing else in this report matters — governance
is not wired. Stop and say so.

## 2. Does the hook have credentials?

The bootstrap needs a control-plane origin, an org slug, and a Windsurf VMCP
token. Report which of the three are present **without printing any value**:

```bash
for v in TENETX_URL TENETX_ORG; do
  printf '%s=%s\n' "$v" "$(printenv "$v" 2>/dev/null || echo '(unset)')"
done
printf 'token present: %s\n' \
  "$(python3 -c 'import os;print(bool(os.environ.get("TENETX_VMCP"+"_TOKEN") or os.environ.get("TENETX_VMCP"+"_TOKEN_FILE")))')"
```

`TENETX_URL` and `TENETX_ORG` are configuration and safe to show. The token is
a credential: report only whether it is set, never its value or length.

On a laptop the bootstrap can instead reuse an existing
`tenetx install windsurf` install, so a missing secret is not automatically a
failure there — check step 3 before concluding.

## 3. What do the breadcrumbs say?

Every path that could not enforce leaves one JSON line. This is the primary
evidence:

```bash
tail -n 40 ~/.tenetx/capture_failures.jsonl 2>/dev/null || echo '(no breadcrumbs)'
```

Read the `reason` field on lines whose `hook` is `windsurf-devin-plugin`:

| reason | what it means | fix |
| --- | --- | --- |
| `bootstrap_not_found` | Devin loaded the hook but could not locate the plugin's bootstrap script | reinstall the plugin; if it persists, set `TENETX_DEVIN_PLUGIN_ROOT` to the plugin directory |
| `missing_credentials` | no origin / org / token reachable | add the Devin secrets, then start a **new** session |
| `insecure_control_plane_url` | `TENETX_URL` is plaintext http to a non-loopback host | correct the secret to https |
| `guard_download_failed` | the control plane was unreachable or rejected the token | check the origin and whether the token is still valid |
| `guard_sha256_mismatch` | the served guard did not match its advertised digest — **treat as an incident** | do not work around it; escalate |
| `guard_sha256_header_missing` | the guard ran unverified | report it; the control plane should always advertise a digest |
| `guard_dir_untrusted`, `guard_foreign_owner`, `guard_group_or_world_writable` | the guard cache is somewhere another local user could rewrite | fix the directory's ownership and mode |
| `guard_refresh_failed_using_cached_guard` | enforcing, but on a guard older than an hour | transient; note it if it repeats |
| `guard_unavailable` | **no guard ran — this session is ungoverned** | resolve the preceding reason on the same timestamp |
| `guard_exec_failed` | the guard crashed or timed out | report the path and escalate |

No breadcrumbs at all, plus a session where tool calls are being checked, is
the healthy case.

## 4. Say it plainly

Finish with one line the user can act on. For example:

> **Ungoverned** — `missing_credentials` at 14:02Z: `TENETX_ORG` is not set on
> this machine. Add it under Settings → Resources → Secrets and start a new
> session; work done in this session was not policy-checked.

Never claim a session is governed on the strength of the plugin being installed
alone. Governed means a guard ran, which means no `guard_unavailable` or
`missing_credentials` breadcrumb for this session.
