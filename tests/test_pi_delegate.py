from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "pi_rpc_worker.py"
INSTALLER = ROOT / "scripts" / "install.py"
FAKE_SOURCE = ROOT / "tests" / "fake_pi.py"


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        self.bin = Path(self.temp.name) / "bin"
        self.project.mkdir()
        self.bin.mkdir()
        fake = self.bin / "pi"
        fake.write_bytes(FAKE_SOURCE.read_bytes())
        fake.chmod(0o755)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        self.env = os.environ.copy()
        self.env["PATH"] = f"{self.bin}{os.pathsep}{self.env['PATH']}"
        self.env["PYTHONPYCACHEPREFIX"] = str(Path(self.temp.name) / "pycache")
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
        self.temp.cleanup()

    def command(self, *args, check=True, env=None):
        return subprocess.run(
            [sys.executable, str(HELPER), *args, "--cwd", str(self.project)],
            env=env or self.env,
            capture_output=True,
            text=True,
            timeout=10,
            check=check,
        )

    def start(self, slug, prompt, stdin=False):
        argv = [sys.executable, str(HELPER), "serve", slug, "--cwd", str(self.project)]
        if stdin:
            argv.append("--prompt-stdin")
            proc = subprocess.Popen(argv, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            proc.stdin.write(prompt)
            proc.stdin.close()
        else:
            brief = Path(self.temp.name) / f"{slug}.md"
            brief.write_text(prompt, encoding="utf-8")
            argv.extend(["--prompt-file", str(brief)])
            proc = subprocess.Popen(argv, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(proc)
        return proc

    def wait_state(self, slug, predicate, timeout=5):
        path = self.project / ".pi" / "workers" / slug / "state.json"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.03)
                continue
            if predicate(state):
                return state
            time.sleep(0.03)
        self.fail(f"state condition not reached for {slug}")

    def stop(self, slug):
        self.command("stop", slug)

    def test_completed_run_stdin_utf8_and_idle_followup(self):
        self.start("happy", "UTF8 привет", stdin=True)
        first = self.wait_state("happy", lambda s: s.get("status") == "idle")
        result = json.loads(Path(first["lastResultPath"]).read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["actualModel"], "fake-model")
        self.assertIn("привет", result["finalMessage"])
        self.assertFalse(any(path.startswith(".pi/") for path in result["touchedFiles"]))
        self.assertEqual((Path(first["lastResultPath"]).parent / "brief.md").stat().st_mode & 0o777, 0o600)

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


if __name__ == "__main__":
    unittest.main()
