---
name: tenetx-setup
description: Wire TenetX governance into Devin cloud sessions, the Devin CLI, or Devin Desktop, and verify it took effect
argument-hint: "[cloud|local]"
allowed-tools: ["read_code", "run_command"]
triggers: [user, model]
---

# Wire TenetX into Devin

Work out which surface the user is on, then follow only that section. Finish by
running `/tenetx-governance:tenetx-doctor` — an install is not done until a
guard has actually run.

The token below is a credential. Print it nowhere: not in a command you run,
not in a summary, not in a commit. Tell the user where to copy it from and let
them paste it into Devin's secrets UI themselves.

## Which surface?

| Signal | Surface |
| --- | --- |
| a Cognition VM, no TenetX install on disk | **cloud** |
| the user's own laptop, `devin` CLI or Devin Desktop | **local** |

## Cloud sessions

Cloud VMs do not carry the user's TenetX install, so the credentials come from
Devin secrets. Ask the user to add all three at
**Settings → Resources → Secrets**:

| Secret | Value |
| --- | --- |
| `TENETX_URL` | the control-plane origin, e.g. `https://acme.tenetx.ai` — must be https |
| `TENETX_ORG` | the org slug, e.g. `acme` |
| `TENETX_VMCP_TOKEN` | the **Windsurf** VMCP token |

The token comes from the TenetX CLI on their own machine:

```bash
tenetx login
tenetx install windsurf      # prints where the token is stored
```

Devin injects secrets at session start, so the user must **start a new cloud
session** afterwards. Editing secrets does not affect a running one.

Governance then applies to every cloud session in the account, in every repo —
the plugin is installed at the user level, not committed to a project.

## Local sessions (Devin CLI, Devin Desktop)

If the laptop already ran `tenetx install windsurf`, there is nothing to
configure: the bootstrap finds that install's guard and credentials on its own.
Confirm and stop:

```bash
tenetx doctor
```

If it has not, run the two commands above once. Secrets are not needed on a
laptop.

## Rolling it out to everyone

An org or enterprise admin can require the plugin for every session instead of
asking each person to install it, at
**Settings → Resources → Plugins**:

```json
{ "requiredPlugins": ["RajuFiyaaLifeStyle/tenetx-devin-plugins"] }
```

An org-level manifest reaches cloud sessions; the enterprise/account manifest
also reaches CLI and Devin Desktop users signed into the account. The secrets
above are still required for cloud, and are still per-account.

## Enforcement that cannot fail open

Devin documents plugin hooks as **best effort and fail open**, so this plugin
should be treated as capture and defence-in-depth rather than a hard gate. For
a repo that must be governed even if plugin loading fails, also commit the
in-repo hook:

```bash
tenetx install devin-cloud      # writes .devin/hooks.v1.json + the hook script
```

Commit both files. Devin clones them with the repo, so they load from the
project rather than from plugin machinery. The two can coexist — the guard
de-duplicates by tool-call id.
