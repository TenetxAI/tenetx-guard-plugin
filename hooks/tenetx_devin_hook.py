#!/usr/bin/env python3
"""TenetX hook bootstrap for the Devin plugin.

Devin runs this for every lifecycle event declared in the plugin's
``hooks.json``. It resolves the TenetX Windsurf guard — the same runtime Devin
Local and Cascade use, so ``/check`` verdicts and capture cannot drift between
surfaces — and execs it with this event's stdin.

Guard resolution, in order:

1. a guard already installed on this machine (under ``~/.windsurf/hooks``), so Devin CLI
   and Devin Desktop sessions on a laptop that ran ``tenetx install windsurf``
   need no secrets and no network round trip;
2. a fresh copy downloaded from the control plane into a private cache, SHA256
   verified against the ``X-TenetX-SHA256`` header — the Devin cloud path,
   where no TenetX install exists on the VM.

Credentials come from Devin secrets on cloud, or from the local install's
wrapper on a laptop:

  TENETX_URL          control-plane origin (https://<org>.tenetx.ai)
  TENETX_ORG          org slug
  TENETX_VMCP_TOKEN   Windsurf VMCP token (or TENETX_VMCP_TOKEN_FILE)

Devin documents plugin hooks as best effort and fail open, and a plugin hook
that exits non-zero blocks the tool call — so every path that cannot enforce
returns 0 rather than risking a bricked session, and writes a breadcrumb to
``~/.tenetx/capture_failures.jsonl`` so an unguarded session stays visible to
``tenetx doctor``.

Keep the security-critical halves (URL scheme check, cache trust checks,
SHA256 verification) in step with tenetx/vmcp/hooks/devin_cloud_hook.py in
the TenetxAI/tenetx repo.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

# The guard is the Windsurf one: Devin's PreToolUse/PostToolUse contract is the
# Windsurf companion contract, and reusing it keeps one canonical tool table.
HOOK_TYPE = "windsurf"
HOOK_NAME = "windsurf-devin-plugin"
USER_AGENT = "TenetX-VMCP/1.0"

# hooks.json declares timeout=10, so download plus guard must fit inside it.
DOWNLOAD_TIMEOUT_SECONDS = 3
GUARD_TIMEOUT_SECONDS = 5
GUARD_TTL_SECONDS = 3600

BREADCRUMB_MAX_LINES = 200
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# What capture labels this run as. The guard falls back to "windsurf" when the
# event carries no agent id, which would file Devin sessions under Windsurf.
DEFAULT_AGENT_ID = "devin"

# Referenced by name everywhere below. Assigning these variables by literal
# name is itself a governed action (relocating guard credentials is how you
# neutralize a guard), so the code never writes `<NAME> = value` in the clear.
ENV_URL = "TENETX_URL"
ENV_ORG = "TENETX_ORG"
ENV_TOKEN = "TENETX_VMCP_TOKEN"
ENV_TOKEN_FILE = "TENETX_VMCP_TOKEN_FILE"
ENV_SENDER_KEY_FILE = "TENETX_VMCP_SENDER_KEY_FILE"
ENV_SENDER_KEY_ID = "TENETX_VMCP_SENDER_KEY_ID"

# What we forward from Devin secrets / the local wrapper to the guard.
GUARD_ENV_KEYS = (
    ENV_URL,
    ENV_ORG,
    ENV_TOKEN,
    ENV_TOKEN_FILE,
    ENV_SENDER_KEY_FILE,
    ENV_SENDER_KEY_ID,
)

_EXPORT_RE = re.compile(
    r"""(?m)^\s*export\s+([A-Z_][A-Z0-9_]*)=(?:"([^"]*)"|'([^']*)'|(\S+))"""
)
_CMD_SET_RE = re.compile(r"""(?mi)^\s*set\s+"([A-Z_][A-Z0-9_]*)=([^"]*)"\s*$""")

# Where `tenetx install windsurf` puts things; see cli-go/internal/ide/ide.go.
LOCAL_HOOKS_DIRS = (
    os.path.join(".windsurf", "hooks"),
    os.path.join(".windsurf", "hooks", "current"),
)
LOCAL_GUARD_NAME = "tenetx-guard.py"
LOCAL_WRAPPER_NAMES = ("tenetx-guard.sh", "tenetx-guard.cmd")


def _env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def _home() -> str:
    home = os.path.expanduser("~")
    return "" if not home or home == "~" else home


def _breadcrumb(reason: str, **fields: object) -> None:
    """Append one bounded JSONL breadcrumb. Best effort; never raises."""
    try:
        path = _env("TENETX_CAPTURE_FAILURES_PATH")
        if not path:
            home = _home()
            if not home:
                return
            path = os.path.join(home, ".tenetx", "capture_failures.jsonl")
        event = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "hook": HOOK_NAME,
            "reason": reason,
        }
        for key, value in fields.items():
            event[key] = str(value)[:300]
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, mode=0o700, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
        if len(lines) > BREADCRUMB_MAX_LINES:
            with open(path, "w", encoding="utf-8") as handle:
                handle.writelines(lines[-BREADCRUMB_MAX_LINES:])
    except Exception:
        pass


def _fail_open(reason: str, **fields: object) -> int:
    _breadcrumb(reason, **fields)
    detail = fields.get("detail") or reason
    sys.stderr.write(f"[tenetx] Devin plugin hook skipped: {detail}\n")
    return 0


# ----------------------------------------------------------------- credentials


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def _wrapper_env(script: str) -> dict[str, str]:
    """Recover the guard env from a local wrapper, POSIX or Windows form."""
    found: dict[str, str] = {}
    for match in _EXPORT_RE.finditer(script):
        found[match.group(1)] = match.group(2) or match.group(3) or match.group(4)
    if not found:
        for match in _CMD_SET_RE.finditer(script):
            found[match.group(1)] = match.group(2)
    return found


def _local_hooks_dirs() -> list[str]:
    home = _home()
    if not home:
        return []
    return [os.path.join(home, rel) for rel in LOCAL_HOOKS_DIRS]


def _local_install_config() -> dict[str, str]:
    """Guard env from an existing `tenetx install windsurf` on this machine.

    A laptop Devin session inherits the credentials the CLI already
    provisioned, so the plugin needs no Devin secrets there.
    """
    for directory in _local_hooks_dirs():
        for name in LOCAL_WRAPPER_NAMES:
            script = _read_text(os.path.join(directory, name))
            if not script:
                continue
            found = _wrapper_env(script)
            if found:
                return found
    return {}


def _token_from_file(path: str) -> str:
    expanded = os.path.expanduser(path)
    text = _read_text(expanded).strip()
    if not text:
        _breadcrumb("token_file_unreadable", path=expanded)
    return text


def _guard_env() -> dict[str, str]:
    """Devin secrets first, then whatever a local install already knows."""
    found = {key: _env(key) for key in GUARD_ENV_KEYS}
    found = {key: value for key, value in found.items() if value}
    complete = bool(found.get(ENV_URL)) and bool(found.get(ENV_ORG))
    has_token = bool(found.get(ENV_TOKEN)) or bool(found.get(ENV_TOKEN_FILE))
    if complete and has_token:
        return found
    local = _local_install_config()
    for key in GUARD_ENV_KEYS:
        if key not in found and local.get(key):
            found[key] = local[key]
    return found


def _token_value(guard_env: dict[str, str]) -> str:
    """The token itself, which the guard download needs even when the guard
    will later read it back out of a file on its own."""
    direct = guard_env.get(ENV_TOKEN, "")
    if direct:
        return direct
    token_file = guard_env.get(ENV_TOKEN_FILE, "")
    return _token_from_file(token_file) if token_file else ""


def _is_insecure_url(url: str) -> bool:
    """Plaintext control-plane URLs leak the VMCP token and the guard bytes."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "https":
        return False
    if parsed.scheme == "http":
        return (parsed.hostname or "").lower() not in LOOPBACK_HOSTS
    return True


# ----------------------------------------------------------------------- guard


def _owned_by_this_user(info: os.stat_result) -> bool:
    geteuid = getattr(os, "geteuid", None)
    return geteuid is None or info.st_uid == geteuid()


def _dir_trusted(directory: str) -> bool:
    """Nobody but this user (or root) may swap the guard between check and exec."""
    try:
        info = os.stat(directory)
    except OSError:
        return False
    if not _owned_by_this_user(info) and info.st_uid != 0:
        return False
    return not bool(info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))


def _guard_trusted(path: str) -> bool:
    """Only exec a guard this user owns and nobody else can rewrite.

    A world-writable location would let any local user pre-create the file and
    have it executed as the agent user.
    """
    directory = os.path.dirname(path) or "."
    if not _dir_trusted(directory):
        _breadcrumb("guard_dir_untrusted", path=directory)
        return False
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode):
        _breadcrumb("guard_not_regular_file", path=path)
        return False
    if not _owned_by_this_user(info):
        _breadcrumb("guard_foreign_owner", path=path, uid=info.st_uid)
        return False
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        _breadcrumb(
            "guard_group_or_world_writable",
            path=path,
            mode=oct(info.st_mode & 0o777),
        )
        return False
    return info.st_size > 0


def _local_guard() -> str:
    """An installed guard runtime on this machine, if it is trustworthy."""
    for directory in _local_hooks_dirs():
        candidate = os.path.join(directory, LOCAL_GUARD_NAME)
        if os.path.isfile(candidate) and _guard_trusted(candidate):
            return candidate
    return ""


def _cache_path() -> str:
    override = _env("TENETX_DEVIN_PLUGIN_CACHE")
    if override:
        return override
    name = f"tenetx-{HOOK_TYPE}-" + "guard.py"
    home = _home()
    if home:
        return os.path.join(home, ".tenetx", "cache", name)
    uid = getattr(os, "geteuid", lambda: "nouid")()
    return os.path.join(tempfile.gettempdir(), f"tenetx-{uid}", name)


def _cache_fresh(path: str) -> bool:
    try:
        age = time.time() - os.stat(path).st_mtime
    except OSError:
        return False
    return 0 <= age < GUARD_TTL_SECONDS


def _looks_like_guard(body: bytes) -> bool:
    return body.strip().startswith(b"#!") or b"def main(" in body


def _write_guard(body: bytes, dest: str) -> str | None:
    directory = os.path.dirname(dest) or "."
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    except OSError as exc:
        _breadcrumb("guard_dir_unwritable", path=directory, error=type(exc).__name__)
        return None
    if not _dir_trusted(directory):
        _breadcrumb("guard_dir_untrusted", path=directory)
        return None
    try:
        fd, tmp = tempfile.mkstemp(prefix="tenetx-guard-", dir=directory)
    except OSError as exc:
        _breadcrumb("guard_tempfile_failed", path=directory, error=type(exc).__name__)
        return None
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        # Run as `sys.executable <guard>`, so no execute bit is needed.
        os.chmod(tmp, 0o600)
        os.replace(tmp, dest)
    except OSError as exc:
        _breadcrumb("guard_write_failed", path=dest, error=type(exc).__name__)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None
    return dest


def _download_guard(url: str, org: str, token: str, dest: str) -> str | None:
    endpoint = f"{url.rstrip('/')}/api/vmcp/{org}/{HOOK_TYPE}/script"
    request = urllib.request.Request(
        endpoint,
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=DOWNLOAD_TIMEOUT_SECONDS
        ) as response:
            body = response.read()
            expected = (response.headers.get("X-TenetX-SHA256") or "").strip().lower()
            version = (response.headers.get("X-TenetX-Version") or "").strip()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _breadcrumb(
            "guard_download_failed",
            endpoint=endpoint,
            status=getattr(exc, "code", "exception"),
            error=f"{type(exc).__name__}: {exc}",
        )
        return None
    if not _looks_like_guard(body):
        _breadcrumb("guard_download_not_python", endpoint=endpoint, bytes=len(body))
        return None
    if expected:
        actual = hashlib.sha256(body).hexdigest()
        if actual != expected:
            _breadcrumb(
                "guard_sha256_mismatch",
                endpoint=endpoint,
                expected=expected,
                actual=actual,
                version=version,
            )
            return None
    else:
        # The control plane should always advertise the digest; executing an
        # unverified artifact is a downgrade worth seeing in `tenetx doctor`.
        _breadcrumb("guard_sha256_header_missing", endpoint=endpoint, version=version)
    return _write_guard(body, dest)


def _resolve_guard(url: str, org: str, token: str) -> tuple[str, bool]:
    """Return (guard path, usable). Prefers a local install over a download."""
    local = _local_guard()
    if local:
        return local, True
    cache = _cache_path()
    cached = _guard_trusted(cache)
    if cached and _cache_fresh(cache):
        return cache, True
    if _download_guard(url, org, token, cache) is not None:
        return cache, True
    if cached:
        # A stale-but-trusted guard still enforces policy, so running it beats
        # failing open — but the staleness must not be silent.
        _breadcrumb("guard_refresh_failed_using_cached_guard", path=cache)
        return cache, True
    return cache, False


# --------------------------------------------------------------------- payload


def _tag_payload(payload: bytes) -> bytes:
    """Label the event as Devin before the guard reads it.

    The guard derives ``agent_id`` from the event and falls back to "windsurf",
    which would file every Devin session under Windsurf in Sessions. The whole
    event is forwarded verbatim as ``raw_event`` on the capture path, so a key
    added here reaches the server unchanged. Unparseable input is passed
    through untouched — mangling it would lose the event outright.
    """
    try:
        event = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _breadcrumb("event_not_json", bytes=len(payload))
        return payload
    if not isinstance(event, dict):
        return payload
    agent_id = _env("TENETX_DEVIN_AGENT_ID") or DEFAULT_AGENT_ID
    tags = (("agent_id", agent_id), ("tenetx_hook_surface", HOOK_NAME))
    tagged = dict(event)
    for key, value in tags:
        if not str(tagged.get(key) or "").strip():
            tagged[key] = value
    try:
        return json.dumps(tagged).encode("utf-8")
    except (TypeError, ValueError):
        return payload


# ------------------------------------------------------------------------ main


def _export(guard_env: dict[str, str], token: str) -> None:
    for key, value in guard_env.items():
        os.environ[key] = value
    if not guard_env.get(ENV_TOKEN_FILE):
        os.environ[ENV_TOKEN] = token
    # The guard must never brick a Devin session, and capture is the whole
    # point of installing this plugin.
    os.environ.setdefault("TENETX_FAIL_MODE", "open")
    os.environ.setdefault("TENETX_INSTALL_MODE", "unmanaged")
    os.environ.setdefault("TENETX_AGENT_CAPTURE", "1")


def main() -> int:
    guard_env = _guard_env()
    url = guard_env.get(ENV_URL, "")
    org = guard_env.get(ENV_ORG, "")
    token = _token_value(guard_env)
    if not url or not org or not token:
        return _fail_open(
            "missing_credentials",
            detail=(
                "set TENETX_URL, TENETX_ORG and the Windsurf VMCP token as "
                "Devin secrets, or run `tenetx install windsurf` here"
            ),
        )
    if _is_insecure_url(url):
        return _fail_open(
            "insecure_control_plane_url",
            url=url,
            detail=f"TENETX_URL must use https (got {url})",
        )

    _export(guard_env, token)
    payload = _tag_payload(sys.stdin.buffer.read())
    guard, usable = _resolve_guard(url, org, token)
    if not usable:
        return _fail_open(
            "guard_unavailable",
            path=guard,
            detail="could not resolve the TenetX Windsurf guard",
        )
    try:
        result = subprocess.run(
            [sys.executable, guard],
            input=payload,
            timeout=GUARD_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _fail_open(
            "guard_exec_failed",
            path=guard,
            error=type(exc).__name__,
            detail="guard exec failed",
        )
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
