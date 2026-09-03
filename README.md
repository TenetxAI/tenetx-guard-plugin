# tenetx-governance — TenetX plugin for Devin

A [Devin plugin](https://docs.devin.ai/cli/extensibility/plugins/overview) that
puts TenetX policy enforcement and agent-observability capture into **Devin
cloud sessions, the Devin CLI, and Devin Desktop** — without committing
anything to the repos Devin works on.

Install it once per account and every session is policy-checked and recorded.

```bash
devin plugins install RajuFiyaaLifeStyle/tenetx-devin-plugins
```

## What it does

Devin's lifecycle hooks hand the plugin every tool call before and after it
runs. The plugin's bootstrap resolves the **TenetX Windsurf guard** — the same
runtime Devin Local and Cascade already use — and pipes the event to it. The
guard calls the control plane:

| Endpoint | When | Effect |
| --- | --- | --- |
| `POST /api/vmcp/{org}/windsurf/check` | `PreToolUse`, `PermissionRequest` | allow / ask / block the call |
| `POST /api/vmcp/{org}/windsurf/capture` | every event | the audit + Sessions trail |
| `POST /api/vmcp/{org}/windsurf/check-response` | `PostToolUse` | response-side policy |

Reusing the Windsurf guard is deliberate: Devin's `PreToolUse`/`PostToolUse`
contract *is* the Windsurf companion contract, and the guard is generated
server-side from one canonical tool table, so a Devin verdict can't drift from
a Windsurf one.

## What you have to configure

Three values. Only the third is a secret.

| Name | Value | Secret? |
| --- | --- | --- |
| `TENETX_URL` | control-plane origin, e.g. `https://acme.tenetx.ai` (https enforced) | no |
| `TENETX_ORG` | org slug, e.g. `acme` | no |
| `TENETX_VMCP_TOKEN` | the **Windsurf** VMCP token | **yes** |

Mint the token with the TenetX CLI on your own machine — it is never created by
this plugin:

```bash
tenetx login
tenetx install windsurf     # prints where the token is stored
```

### Cloud sessions

Add all three under **Settings → Resources → Secrets** in the Devin web app,
then **start a new session** — secrets are injected at session start, so
editing them does not affect a running session.

`TENETX_VMCP_TOKEN_FILE` is accepted instead of `TENETX_VMCP_TOKEN` if you
would rather mount the token as a file.

### Local sessions (Devin CLI / Devin Desktop)

Nothing to configure. If the machine has already run
`tenetx install windsurf`, the bootstrap discovers that install's guard and
credentials on its own and skips the network entirely.

### Rolling it out to an org

An org or enterprise admin can require it for everyone at
**Settings → Resources → Plugins**:

```json
{ "requiredPlugins": ["RajuFiyaaLifeStyle/tenetx-devin-plugins"] }
```

An org-level manifest reaches cloud sessions; the enterprise/account manifest
also reaches CLI and Devin Desktop users signed into the account.

## Layout

```
.devin-plugin/plugin.json    manifest (Devin-native layout)
.claude-plugin/plugin.json   identical manifest, for the Claude-compat layout
hooks.json                   lifecycle hooks — Devin-native location
hooks/hooks.json             the same config, Claude-compat location
hooks/tenetx_devin_hook.py   the bootstrap Devin actually executes
AGENTS.md                    always-on rule: how to behave under governance
rules/                       triggered rule: what to do with a blocked call
skills/                      /tenetx-governance:… operator commands
scripts/                     hook-config generator + layout validator
tests/                       unit tests for the bootstrap
```

Both manifests and both hook configs are shipped because Devin picks a layout
by precedence (`.devin-plugin` > `.claude-plugin` > root `plugin.json`) and
reads the hook config from the path *that* layout declares. `scripts/` keeps
the pairs identical and CI fails if they drift.

## Skills

Three, and all three are for the human, not for the agent:

| Command | Use |
| --- | --- |
| `/tenetx-governance:tenetx-setup` | wire it up and verify it took |
| `/tenetx-governance:tenetx-doctor` | is this session *actually* governed? |
| `/tenetx-governance:tenetx-policy-explain` | explain a verdict, and the legitimate next step |

**There is deliberately no skill that posts events to the TenetX API.** Capture
belongs to the hook, not the model:

- a hook fires on every event whether or not the model cooperates; a skill fires
  when the model decides to invoke it, which means duplicate and missing events;
- the guard's payload is generated from the server's canonical tool table, so it
  cannot drift — a hand-written payload in a skill would;
- calling the API from a skill would put the VMCP token in the model's context.

Governance you can only get by asking the agent nicely is not governance. The
skills exist for the parts a human genuinely has to do: configuring secrets,
diagnosing a fail-open, and reading a verdict.

## Limitations worth knowing

- **Plugin hooks are best effort and fail open.** That is Devin's documented
  behaviour, not ours: if the plugin fails to load, the session continues
  unguarded. For a repo that must be governed regardless, also commit the
  in-repo hook — `tenetx install devin-cloud` writes `.devin/hooks.v1.json`
  and its hook script, which Devin clones with the repo. Both can be installed
  at once; the guard de-duplicates by tool-call id.
- **No `SessionStart` / `SessionEnd`.** Devin does not deliver them to plugins.
  Session identity comes from `session_id` on the other events, which the guard
  already prefers over the per-turn `prompt_id`.
- **Devin plugins are in closed beta**, so the manifest and hook contract may
  still change.
- Every fail-open path writes one JSON line to
  `~/.tenetx/capture_failures.jsonl`. That file is the answer to "was this
  session actually guarded?" — see `/tenetx-governance:tenetx-doctor`.

## Security properties of the bootstrap

- The control-plane URL must be `https` (loopback excepted, for local dev).
- A downloaded guard is verified against the `X-TenetX-SHA256` response header
  before it is executed; a mismatch refuses to run it.
- The guard cache is only used if this user owns it and neither the file nor its
  directory is group- or world-writable — otherwise any local user could
  pre-place a file and have it executed as the agent user.
- The token is forwarded as a file path whenever it arrived as one, so it is not
  materialized into the environment of a process the agent can inspect.
- A refused or unavailable guard exits 0. A plugin hook that exits non-zero
  blocks the tool call, so failing closed here would brick the session rather
  than protect it — the breadcrumb is what makes that visible.

## Developing

```bash
devin plugins install .                 # local installs are linked, edits are live
python3 scripts/validate_plugin.py      # layout + hook-config checks
python3 scripts/gen_hooks_json.py       # regenerate both hooks.json copies
python3 -m unittest discover -s tests   # bootstrap unit tests
```

Change the hook command in `scripts/gen_hooks_json.py`, never in the generated
JSON — CI regenerates and diffs.

The bootstrap's security-critical halves mirror
`tenetx/vmcp/hooks/devin_cloud_hook.py` in `TenetxAI/tenetx`. A fix to one
belongs in the other.
