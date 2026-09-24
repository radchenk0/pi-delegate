from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from base import FAKE_SOURCE, HELPER, INSTALLER, ROOT, WorkerTestCase


class WorkerTest(WorkerTestCase):
    def test_completed_run_stdin_utf8_and_idle_followup(self):
        legacy_result = self.project / ".pi/workers/happy/result.md"
        legacy_result.parent.mkdir(parents=True)
        legacy_result.write_text("old", encoding="utf-8")
        legacy_result.chmod(0o644)
        self.start("happy", "UTF8 привет", stdin=True)
        first = self.wait_state("happy", lambda s: s.get("status") == "idle")
        result = json.loads(Path(first["lastResultPath"]).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["actualModel"], "fake-model")
        self.assertIn("привет", result["finalMessage"])
        self.assertFalse(any(path.startswith(".pi/") for path in result["touchedFiles"]))
        self.assertEqual((Path(first["lastResultPath"]).parent / "brief.md").stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(first["lastResultPath"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(legacy_result.stat().st_mode & 0o777, 0o600)

        self.command("follow-up", "happy", "second")
        second = self.wait_state("happy", lambda s: s.get("status") == "idle" and s.get("lastRunId") == "run-000002")
        self.assertIn("second", json.loads(Path(second["lastResultPath"]).read_text())["finalMessage"])
        self.stop("happy")

    def test_busy_followup_joins_open_run(self):
        self.start("queue", "HOLD")
        self.wait_state("queue", lambda s: s.get("status") == "running")
        self.command("follow-up", "queue", "finish")
        state = self.wait_state("queue", lambda s: s.get("status") == "idle")
        result = json.loads(Path(state["lastResultPath"]).read_text())
        self.assertEqual(result["runId"], "run-000001")
        self.assertEqual(result["queuedFollowUps"], 1)
        self.stop("queue")

    def test_large_unicode_control_message_is_not_truncated(self):
        self.start("large", "HOLD")
        self.wait_state("large", lambda s: s.get("status") == "running")
        message = "ёж" * 40000
        self.command("follow-up", "large", message)
        state = self.wait_state("large", lambda s: s.get("status") == "idle")
        result = json.loads(Path(state["lastResultPath"]).read_text())
        self.assertEqual(result["finalMessage"], "done: " + message)
        self.stop("large")

    def test_rejected_prompt_and_pi_death_publish_results(self):
        self.start("reject", "REJECT")
        rejected = self.wait_state("reject", lambda s: s.get("lastRunStatus") == "failed")
        self.assertEqual(json.loads(Path(rejected["lastResultPath"]).read_text())["error"], "fake rejection")
        self.assertEqual(self.command("wait", "reject", check=False).returncode, 2)
        self.stop("reject")

        dead_proc = self.start("death", "DIE")
        dead = self.wait_state("death", lambda s: s.get("status") == "dead")
        artifact = json.loads(Path(dead["lastResultPath"]).read_text())
        self.assertEqual(artifact["exitCode"], 23)
        dead_proc.wait(timeout=3)

    def test_signal_aborts_open_run(self):
        # A fake Pi process with no responses keeps the run open.
        fake = self.bin / "pi"
        fake.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n", encoding="utf-8")
        fake.chmod(0o755)
        proc = self.start("signal", "wait")
        self.wait_state("signal", lambda s: bool(s.get("openRun")))
        proc.send_signal(signal.SIGHUP)
        proc.wait(timeout=6)
        state = json.loads((self.project / ".pi/workers/signal/state.json").read_text())
        result = json.loads(Path(state["lastResultPath"]).read_text())
        self.assertEqual(result["status"], "aborted")
        self.assertEqual(result["signal"], "SIGHUP")

    def test_sigkill_is_detected_as_orphan_and_recovered(self):
        proc = self.start("orphan", "ORPHAN")
        state = self.wait_state("orphan", lambda s: s.get("status") == "running")
        proc.kill()
        proc.wait(timeout=3)
        status = self.command("status", "orphan")
        observed = json.loads(status.stdout)
        self.assertEqual(observed["status"], "orphaned")
        artifact = json.loads(Path(observed["lastResultPath"]).read_text())
        self.assertEqual(artifact["status"], "orphaned")
        self.assertTrue(artifact["workerAlive"])
        self.command("recover", "orphan")

    def test_stop_kills_descendant_that_holds_stdio(self):
        self.start("descendant", "SPAWN")
        self.wait_state("descendant", lambda s: s.get("status") == "idle")
        descendant_pid = int((self.project / "descendant.pid").read_text())
        self.stop("descendant")
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            try:
                os.kill(descendant_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("descendant survived process-group cleanup")


class InstallerTest(unittest.TestCase):
    def test_codex_default_and_optional_claude_target(self):
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            subprocess.run([sys.executable, str(INSTALLER)], env=env, check=True, capture_output=True, text=True)
            self.assertEqual((Path(home) / ".agents/skills/pi-delegate").resolve(), ROOT)
            self.assertFalse((Path(home) / ".claude/skills/pi-delegate").exists())
            subprocess.run([sys.executable, str(INSTALLER), "--target", "claude"], env=env, check=True, capture_output=True, text=True)
            self.assertEqual((Path(home) / ".claude/skills/pi-delegate").resolve(), ROOT)

    def test_refuses_unrelated_path_and_replace_backs_up(self):
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            target = Path(home) / ".agents" / "skills" / "pi-delegate"
            target.mkdir(parents=True)
            (target / "README.md").write_text("someone else's skill", encoding="utf-8")
            refused = subprocess.run(
                [sys.executable, str(INSTALLER)], env=env, capture_output=True, text=True,
            )
            self.assertEqual(refused.returncode, 2, refused.stderr)
            self.assertIn("refusing to replace existing path", refused.stderr)
            self.assertEqual((target / "README.md").read_text(encoding="utf-8"), "someone else's skill")

            replaced = subprocess.run(
                [sys.executable, str(INSTALLER), "--replace"], env=env, capture_output=True, text=True,
            )
            self.assertEqual(replaced.returncode, 0, replaced.stderr)
            self.assertEqual(target.resolve(), ROOT)
            backups = sorted(
                path for path in target.parent.iterdir()
                if path.name.startswith("pi-delegate.backup-")
            )
            self.assertEqual(len(backups), 1)
            self.assertEqual(
                (backups[0] / "README.md").read_text(encoding="utf-8"), "someone else's skill",
            )

    def test_uninstall_removes_symlink_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            subprocess.run([sys.executable, str(INSTALLER)], env=env, check=True, capture_output=True, text=True)
            target = Path(home) / ".agents" / "skills" / "pi-delegate"
            self.assertTrue(target.is_symlink())
            result = subprocess.run(
                [sys.executable, str(INSTALLER), "--uninstall"], env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(target.exists())
            self.assertFalse(target.is_symlink())
            second = subprocess.run(
                [sys.executable, str(INSTALLER), "--uninstall"], env=env, capture_output=True, text=True,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("absent:", second.stdout)


if __name__ == "__main__":
    unittest.main()
