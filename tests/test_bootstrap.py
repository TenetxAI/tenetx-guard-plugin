#!/usr/bin/env python3
"""Tests for hooks/tenetx_devin_hook.py.

Run with: python3 -m pytest tests/ -q     (or python3 tests/test_bootstrap.py)

These cover the two things a hook must never get wrong: it must not block a
Devin session when it cannot enforce, and it must not execute an artifact it
has not verified.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOTSTRAP = ROOT / "hooks" / "tenetx_devin_hook.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("tenetx_devin_hook", BOOTSTRAP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hook = _load_module()


def _run(env: dict[str, str], payload: str) -> subprocess.CompletedProcess:
    """Run the bootstrap as Devin would: fresh process, event JSON on stdin."""
    child = {"PATH": os.environ.get("PATH", ""), **env}
    return subprocess.run(
        [sys.executable, str(BOOTSTRAP)],
        input=payload.encode("utf-8"),
        capture_output=True,
        env=child,
        timeout=30,
    )


class FailOpenTests(unittest.TestCase):
    """A plugin hook that exits non-zero blocks the tool call."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.breadcrumbs = self.home / "capture_failures.jsonl"

    def _base_env(self) -> dict[str, str]:
        return {
            "HOME": str(self.home),
            "TENETX_CAPTURE_FAILURES_PATH": str(self.breadcrumbs),
        }

    def _reasons(self) -> list[str]:
        if not self.breadcrumbs.is_file():
            return []
        return [
            json.loads(line)["reason"]
            for line in self.breadcrumbs.read_text().splitlines()
            if line.strip()
        ]

    def test_missing_credentials_allows_and_leaves_a_breadcrumb(self) -> None:
        result = _run(self._base_env(), '{"hook_event_name": "PreToolUse"}')
        self.assertEqual(result.returncode, 0)
        self.assertIn("missing_credentials", self._reasons())

    def test_plaintext_control_plane_is_refused(self) -> None:
        env = self._base_env()
        env.update(
            {
                "TENETX_URL": "http://control.example.com",
                "TENETX_ORG": "acme",
                "TENETX_VMCP_TOKEN": "t0ken",
            }
        )
        result = _run(env, '{"hook_event_name": "PreToolUse"}')
        self.assertEqual(result.returncode, 0)
        self.assertIn("insecure_control_plane_url", self._reasons())

    def test_unreachable_control_plane_allows(self) -> None:
        env = self._base_env()
        env.update(
            {
                # RFC 5737 TEST-NET-1: guaranteed not to route anywhere.
                "TENETX_URL": "https://192.0.2.1",
                "TENETX_ORG": "acme",
                "TENETX_VMCP_TOKEN": "t0ken",
                "TENETX_DEVIN_PLUGIN_CACHE": str(self.home / "cache" / "guard.py"),
            }
        )
        result = _run(env, '{"hook_event_name": "PreToolUse"}')
        self.assertEqual(result.returncode, 0)
        self.assertIn("guard_download_failed", self._reasons())
        self.assertIn("guard_unavailable", self._reasons())


class GuardExecTests(unittest.TestCase):
    """The verdict has to be the guard's, not the bootstrap's."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.cache = self.home / "cache" / "guard.py"
        self.cache.parent.mkdir(parents=True)
        # The bootstrap refuses a guard anyone else could rewrite, and mkdir
        # honours the umask, which is 0 in some CI images.
        self.cache.parent.chmod(0o700)
        self.seen = self.home / "seen.json"

    def _install_guard(self, exit_code: int) -> None:
        self.cache.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "event = json.load(sys.stdin)\n"
            "open(os.environ['SEEN'], 'w').write(json.dumps(event))\n"
            f"sys.exit({exit_code})\n",
            encoding="utf-8",
        )
        self.cache.chmod(0o600)

    def _env(self) -> dict[str, str]:
        return {
            "HOME": str(self.home),
            "TENETX_URL": "https://acme.tenetx.ai",
            "TENETX_ORG": "acme",
            "TENETX_VMCP_TOKEN": "t0ken",
            "TENETX_DEVIN_PLUGIN_CACHE": str(self.cache),
            "TENETX_CAPTURE_FAILURES_PATH": str(self.home / "crumbs.jsonl"),
            "SEEN": str(self.seen),
        }

    def test_guard_block_is_propagated_as_exit_2(self) -> None:
        self._install_guard(2)
        result = _run(self._env(), '{"hook_event_name": "PreToolUse"}')
        self.assertEqual(result.returncode, 2)

    def test_guard_allow_is_propagated_as_exit_0(self) -> None:
        self._install_guard(0)
        result = _run(self._env(), '{"hook_event_name": "PreToolUse"}')
        self.assertEqual(result.returncode, 0)

    def test_event_reaches_the_guard_tagged_as_devin(self) -> None:
        self._install_guard(0)
        _run(self._env(), '{"hook_event_name": "PreToolUse", "tool_name": "exec"}')
        event = json.loads(self.seen.read_text())
        self.assertEqual(event["agent_id"], "devin")
        self.assertEqual(event["tool_name"], "exec")
        self.assertEqual(event["tenetx_hook_surface"], "windsurf-devin-plugin")

    def test_an_agent_id_already_on_the_event_wins(self) -> None:
        self._install_guard(0)
        _run(self._env(), '{"hook_event_name": "PreToolUse", "agent_id": "devin-2"}')
        self.assertEqual(json.loads(self.seen.read_text())["agent_id"], "devin-2")

    def test_group_writable_guard_is_not_executed(self) -> None:
        self._install_guard(2)
        self.cache.chmod(0o660)
        env = self._env()
        # No network: the download will fail, and the untrusted cache must not
        # be used as a fallback.
        env["TENETX_URL"] = "https://192.0.2.1"
        result = _run(env, '{"hook_event_name": "PreToolUse"}')
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.seen.exists())


class PayloadTaggingTests(unittest.TestCase):
    def test_non_json_stdin_is_passed_through_unchanged(self) -> None:
        self.assertEqual(hook._tag_payload(b"not json"), b"not json")

    def test_non_object_json_is_passed_through_unchanged(self) -> None:
        self.assertEqual(hook._tag_payload(b"[1, 2]"), b"[1, 2]")

    def test_the_original_fields_survive_tagging(self) -> None:
        tagged = json.loads(hook._tag_payload(b'{"a": 1}'))
        self.assertEqual(tagged["a"], 1)
        self.assertEqual(tagged["agent_id"], hook.DEFAULT_AGENT_ID)


class WrapperEnvTests(unittest.TestCase):
    def test_posix_wrapper_exports_are_recovered(self) -> None:
        script = "\n".join(
            [
                "#!/bin/sh",
                'export TENETX_ORG="acme"',
                "export TENETX_URL='https://acme.tenetx.ai'",
                "export TENETX_VMCP_TOKEN_FILE=/home/u/.tenetx/vmcp_tokens/a.token",
            ]
        )
        env = hook._wrapper_env(script)
        self.assertEqual(env[hook.ENV_ORG], "acme")
        self.assertEqual(env[hook.ENV_URL], "https://acme.tenetx.ai")
        self.assertTrue(env[hook.ENV_TOKEN_FILE].endswith("a.token"))

    def test_windows_wrapper_sets_are_recovered(self) -> None:
        script = 'set "TENETX_ORG=acme"\r\nset "TENETX_URL=https://acme.tenetx.ai"\r\n'
        env = hook._wrapper_env(script)
        self.assertEqual(env[hook.ENV_ORG], "acme")
        self.assertEqual(env[hook.ENV_URL], "https://acme.tenetx.ai")


class UrlSchemeTests(unittest.TestCase):
    def test_https_is_accepted(self) -> None:
        self.assertFalse(hook._is_insecure_url("https://acme.tenetx.ai"))

    def test_plaintext_remote_is_rejected(self) -> None:
        self.assertTrue(hook._is_insecure_url("http://acme.tenetx.ai"))

    def test_plaintext_loopback_is_allowed_for_local_development(self) -> None:
        self.assertFalse(hook._is_insecure_url("http://localhost:9050"))
        self.assertFalse(hook._is_insecure_url("http://127.0.0.1:9050"))

    def test_a_bare_host_is_rejected(self) -> None:
        self.assertTrue(hook._is_insecure_url("acme.tenetx.ai"))


class TrustTests(unittest.TestCase):
    def test_a_world_writable_directory_is_never_trusted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o777)
            self.assertFalse(hook._dir_trusted(tmp))

    def test_a_private_directory_is_trusted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o700)
            self.assertTrue(hook._dir_trusted(tmp))

    def test_a_symlinked_guard_is_not_trusted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o700)
            real = Path(tmp) / "real.py"
            real.write_text("#!/usr/bin/env python3\n")
            link = Path(tmp) / "link.py"
            link.symlink_to(real)
            self.assertFalse(hook._guard_trusted(str(link)))

    def test_an_empty_guard_is_not_trusted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.chmod(tmp, 0o700)
            empty = Path(tmp) / "empty.py"
            empty.touch()
            empty.chmod(stat.S_IRUSR | stat.S_IWUSR)
            self.assertFalse(hook._guard_trusted(str(empty)))


class HooksConfigTests(unittest.TestCase):
    """Both copies of the hook config must stay in step, and must not declare
    events Devin does not deliver to plugins."""

    UNSUPPORTED = ("SessionStart", "SessionEnd")

    def _configs(self) -> list[dict]:
        return [
            json.loads((ROOT / name).read_text())
            for name in ("hooks.json", "hooks/hooks.json")
        ]

    def test_both_copies_are_identical(self) -> None:
        first, second = self._configs()
        self.assertEqual(first, second)

    def test_no_unsupported_events_are_declared(self) -> None:
        for config in self._configs():
            for event in self.UNSUPPORTED:
                self.assertNotIn(event, config)

    def test_every_command_fails_open_when_the_bootstrap_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for config in self._configs():
                for rows in config.values():
                    for row in rows:
                        for entry in row["hooks"]:
                            result = subprocess.run(
                                entry["command"],
                                shell=True,
                                input=b'{"hook_event_name": "Stop"}',
                                capture_output=True,
                                env={
                                    "PATH": os.environ.get("PATH", ""),
                                    "HOME": tmp,
                                    "DEVIN_PROJECT_DIR": tmp,
                                },
                                timeout=30,
                            )
                            self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
