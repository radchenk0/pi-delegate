"""Control-plane tests: late follow-up race, abort/steer protocol fidelity,
clean, list, serve refusal, recover --forget, extension UI, git degradation,
and the legacy result.md fallback (stdlib only)."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from base import WorkerTestCase


class WorkerControlTest(WorkerTestCase):
    def test_watch_replays_human_readable_events(self):
        self.start("observe", "STREAM")
        self.wait_state("observe", lambda s: s.get("status") == "idle")
        watched = self.command(
            "watch", "observe", "--history", "100", "--no-follow", "--color", "never",
        )
        self.assertIn("watching observe status=idle", watched.stdout)
        self.assertIn("agent running", watched.stdout)
        self.assertIn("assistant done: STREAM", watched.stdout)
        self.assertEqual(watched.stdout.count("done: STREAM"), 1)
        self.assertIn("agent settled", watched.stdout)
        self.assertNotIn("\x1b[", watched.stdout)
        self.assertIn("\\x1b[31m", watched.stdout)
        self.stop("observe")

    def test_watch_follows_until_settle(self):
        self.start("observe-live", "HOLD")
        self.wait_state("observe-live", lambda s: s.get("status") == "running")
        watcher = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "scripts/pi_rpc_worker.py"),
                "watch",
                "observe-live",
                "--history",
                "0",
                "--color",
                "never",
                "--cwd",
                str(self.project),
            ],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.2)
        self.command("abort", "observe-live")
        stdout, stderr = watcher.communicate(timeout=5)
        self.assertEqual(watcher.returncode, 0, stderr)
        self.assertIn("agent settled", stdout)
        self.assertIn("watch ended: status=idle", stdout)
        self.stop("observe-live")

    def test_late_followup_without_open_run_opens_new_run(self):
        # Simulates the TOCTOU window: a client read "running" state, the run
        # settled, then the follow-up reached the supervisor. The supervisor
        # must open a new run instead of forwarding to an idle Pi.
        self.start("race", "go")
        settled = self.wait_state("race", lambda s: s.get("status") == "idle" and s.get("lastRunId") == "run-000001")
        self.assertEqual(self.run_result(settled)["status"], "completed")

        request_id = uuid.uuid4().hex
        envelope = {"action": "rpc", "requestId": request_id, "command": {"type": "follow_up", "message": "late"}}
        fifo = self.project / ".pi" / "workers" / "race" / "control.fifo"
        fd = os.open(str(fifo), os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, (json.dumps(envelope) + "\n").encode("utf-8"))
        finally:
            os.close(fd)

        second = self.wait_state("race", lambda s: s.get("status") == "idle" and s.get("lastRunId") == "run-000002", timeout=10)
        result = self.run_result(second)
        self.assertEqual(result["runId"], "run-000002")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["finalMessage"], "done: late")
        brief = (self.project / ".pi" / "workers" / "race" / "runs" / "run-000002" / "brief.md").read_text(encoding="utf-8")
        self.assertIn("late", brief)
        self.stop("race")

    def test_abort_settles_first_then_acknowledges(self):
        self.start("ab", "HOLD")
        self.wait_state("ab", lambda s: s.get("status") == "running")
        self.command("abort", "ab")
        # The fake Pi (like real Pi) emits the abort response only after
        # agent_settled, so by the time the CLI returns the run is finalized.
        state = self.wait_state("ab", lambda s: s.get("status") == "idle", timeout=5)
        result = self.run_result(state)
        self.assertEqual(result["status"], "aborted")
        self.assertEqual(result.get("abortOrigin"), "rpc")
        self.assertTrue(result["workerAlive"])
        self.stop("ab")

    def test_steer_counts_and_acknowledges(self):
        self.start("st", "HOLD")
        self.wait_state("st", lambda s: s.get("status") == "running")
        self.command("steer", "st", "focus on tests")
        state = self.wait_state("st", lambda s: s.get("status") == "running" and (s.get("openRun") or {}).get("steers") == 1)
        self.command("abort", "st")
        settled = self.wait_state("st", lambda s: s.get("status") == "idle")
        result = self.run_result(settled)
        self.assertEqual(result["steers"], 1)
        self.assertEqual(result["status"], "aborted")
        # steer is refused for an idle worker
        refused = self.command("steer", "st", "again", check=False)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("steer requires a running worker", refused.stderr)
        self.stop("st")

    def test_clean_archives_evidence_and_discard_removes_runtime(self):
        self.start("arch", "go")
        settled = self.wait_state("arch", lambda s: s.get("status") == "idle")
        self.assertEqual(self.run_result(settled)["status"], "completed")
        self.stop("arch")
        self.command("clean", "arch")

        runtime = self.project / ".pi" / "workers" / "arch"
        self.assertFalse(runtime.exists())
        archives = list((self.project / ".pi" / "logs" / "arch").iterdir())
        self.assertEqual(len(archives), 1)
        for name in ("state.json", "runs", "events.jsonl", "stderr.log"):
            self.assertTrue((archives[0] / name).exists(), f"missing {name} in archive")

        # --discard-logs removes the runtime without archiving
        self.start("junk", "go")
        self.wait_state("junk", lambda s: s.get("status") == "idle")
        self.stop("junk")
        self.command("clean", "junk", "--discard-logs")
        self.assertFalse((self.project / ".pi" / "workers" / "junk").exists())
        self.assertFalse((self.project / ".pi" / "logs" / "junk").exists())

        # a live worker refuses clean
        self.start("live", "HOLD")
        self.wait_state("live", lambda s: s.get("status") == "running")
        refused = self.command("clean", "live", check=False)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("refusing to clean a live", refused.stderr)
        self.stop("live")

    def test_list_shows_workers_and_serve_refuses_live_slug(self):
        self.start("one", "HOLD")
        self.wait_state("one", lambda s: s.get("status") == "running")
        self.start("two", "go")
        self.wait_state("two", lambda s: s.get("status") == "idle")
        self.stop("two")

        listed = self.command("list")
        self.assertIn("one", listed.stdout)
        self.assertIn("two", listed.stdout)
        self.assertIn("running", listed.stdout)
        self.assertIn("stopped", listed.stdout)

        refused = self.command("serve", "one", "HOLD", check=False, timeout=15)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("session is locked by another supervisor", refused.stderr)
        self.stop("one")

    def test_recover_refuses_mismatch_and_forget_clears_state(self):
        proc = self.start("mis", "ORPHAN")
        running = self.wait_state("mis", lambda s: s.get("status") == "running")
        orphan_pi_pid = running["piPid"]
        proc.kill()
        proc.wait(timeout=3)
        # Force the orphaned finalize so a run artifact exists before --forget.
        self.command("status", "mis")

        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], env=self.env)
        time.sleep(0.2)
        state_path = self.project / ".pi" / "workers" / "mis" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["piPid"] = sleeper.pid
        state["piPgid"] = sleeper.pid
        state_path.write_text(json.dumps(state), encoding="utf-8")

        try:
            # Fail-closed by default: no signal may reach the foreign process.
            refused = self.command("recover", "mis", check=False)
            self.assertEqual(refused.returncode, 2, refused.stderr)
            self.assertIn("refusing to signal it", refused.stderr)
            os.kill(sleeper.pid, 0)  # still alive

            self.command("recover", "mis", "--forget")
            forgotten = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(forgotten["status"], "stopped")
            self.assertIsNone(forgotten.get("piPid"))
            self.assertIsNone(forgotten.get("pid"))
            self.assertIsNone(forgotten.get("piPgid"))
            os.kill(sleeper.pid, 0)  # foreign process untouched
            # Run evidence preserved
            artifact = json.loads(Path(forgotten["lastResultPath"]).read_text(encoding="utf-8"))
            self.assertEqual(artifact["status"], "orphaned")

            # The worker is restartable after --forget.
            self.start("mis", "go")
            resumed = self.wait_state("mis", lambda s: s.get("status") == "idle" and s.get("lastRunId") == "run-000002", timeout=15)
            self.assertEqual(self.run_result(resumed)["status"], "completed")
            self.stop("mis")
        finally:
            os.kill(orphan_pi_pid, signal.SIGKILL)
            if sleeper.poll() is None:
                sleeper.kill()
            sleeper.wait()

    def test_recover_clears_orphan_when_pi_is_already_gone(self):
        proc = self.start("gone", "ORPHAN")
        running = self.wait_state("gone", lambda s: s.get("status") == "running")
        proc.kill()
        proc.wait(timeout=3)
        self.command("status", "gone")
        os.kill(running["piPid"], signal.SIGKILL)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(running["piPid"], 0)
            except ProcessLookupError:
                break
            time.sleep(0.03)
        self.command("recover", "gone")
        state = json.loads(
            (self.project / ".pi/workers/gone/state.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["status"], "stopped")
        self.assertIsNone(state.get("piPid"))
        self.assertIn("already gone", state["lastError"])

    def test_ui_respond_answers_blocking_dialog(self):
        self.start("uiw", "UI")
        state = self.wait_state(
            "uiw",
            lambda s: isinstance(s.get("pendingUi"), dict) and s["pendingUi"].get("id") == "ui-1",
            timeout=10,
        )
        self.assertEqual(state["pendingUi"]["method"], "confirm")
        # The dialog text is recorded so an orchestrator can decide without
        # reading events.jsonl.
        self.assertEqual(state["pendingUi"]["title"], "Proceed with fake step?")
        self.assertEqual(state["pendingUi"]["message"], "Nothing real happens.")
        self.assertNotIn("options", state["pendingUi"])

        # Invalid forms are refused by the CLI.
        no_flag = self.command("ui-respond", "uiw", check=False)
        self.assertEqual(no_flag.returncode, 2)
        self.assertIn("provide exactly one", no_flag.stderr)
        both = self.command("ui-respond", "uiw", "--confirmed", "--cancelled", check=False)
        self.assertEqual(both.returncode, 2)

        # Simulate a stale client-side state read: the supervisor still owns
        # ui-1, while the CLI composes a response for a different id. The
        # supervisor-level rejection must reach the CLI instead of printing
        # a false "accepted" message.
        state_path = self.project / ".pi/workers/uiw/state.json"
        stale = json.loads(state_path.read_text(encoding="utf-8"))
        stale["pendingUi"]["id"] = "stale-ui"
        state_path.write_text(json.dumps(stale), encoding="utf-8")
        mismatched = self.command("ui-respond", "uiw", "--confirmed", check=False)
        self.assertEqual(mismatched.returncode, 2)
        self.assertIn("does not match a pending UI request", mismatched.stderr)

        self.command("ui-respond", "uiw", "--confirmed")
        settled = self.wait_state(
            "uiw", lambda s: s.get("status") == "idle" and s.get("pendingUi") is None, timeout=10,
        )
        result = self.run_result(settled)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["finalMessage"], "done: ui confirmed")
        # The fire-and-forget notify request must not be treated as an error.
        self.assertFalse(settled.get("lastError"))

        # Nothing pending afterwards.
        empty = self.command("ui-respond", "uiw", check=False)
        self.assertEqual(empty.returncode, 2)
        self.assertIn("no pending blocking UI request", empty.stderr)
        self.stop("uiw")

    def test_ui_respond_select_requires_an_offered_option(self):
        self.start("sel", "SELECT")
        state = self.wait_state(
            "sel",
            lambda s: isinstance(s.get("pendingUi"), dict) and s["pendingUi"].get("id") == "ui-2",
            timeout=10,
        )
        self.assertEqual(state["pendingUi"]["options"], ["y: allow once", "s: allow session", "n: deny"])

        # A shorthand or paraphrase is refused locally and lists the options,
        # instead of being sent to Pi and leaving the dialog unanswered.
        wrong = self.command("ui-respond", "sel", "--value", "y", check=False)
        self.assertEqual(wrong.returncode, 2)
        self.assertIn("must be one of the offered options", wrong.stderr)
        self.assertIn("'y: allow once'", wrong.stderr)
        self.assertEqual(self.wait_state("sel", lambda s: True)["pendingUi"]["id"], "ui-2")

        self.command("ui-respond", "sel", "--value", "y: allow once")
        settled = self.wait_state(
            "sel", lambda s: s.get("status") == "idle" and s.get("pendingUi") is None, timeout=10,
        )
        self.assertEqual(self.run_result(settled)["finalMessage"], "done: ui selected")
        self.stop("sel")

    def test_wait_return_on_ui_surfaces_a_blocking_dialog(self):
        self.start("wui", "SELECT")
        self.wait_state("wui", lambda s: isinstance(s.get("pendingUi"), dict), timeout=10)

        # Without the flag a blocked worker just runs out the clock.
        plain = self.command("wait", "wui", "0.5", check=False)
        self.assertEqual(plain.returncode, 1)

        blocked = self.command("wait", "wui", "5", "--return-on-ui", check=False)
        self.assertEqual(blocked.returncode, 3)
        self.assertIn("waiting on a select dialog (ui-2)", blocked.stdout)
        self.assertIn("title: dangerous operation requires confirmation: git commit", blocked.stdout)
        self.assertIn("option: y: allow once", blocked.stdout)

        self.command("ui-respond", "wui", "--value", "y: allow once")
        settled = self.command("wait", "wui", "10", "--return-on-ui")
        self.assertIn("settled", settled.stdout)
        self.stop("wui")

    def test_git_snapshot_degradation_is_surfaced_in_result(self):
        real_git = shutil.which("git")
        self.assertTrue(real_git)
        wrapper = self.bin / "git"
        wrapper.write_text(
            "#!/bin/sh\n"
            "for a in \"$@\"; do if [ \"$a\" = status ]; then sleep 12; exit 1; fi; done\n"
            f"exec {real_git} \"$@\"\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        self.start("gitd", "go")
        state = self.wait_state("gitd", lambda s: s.get("status") == "idle", timeout=60)
        result = self.run_result(state)
        self.assertEqual(result["gitEvidence"]["attribution"], "degraded")
        self.assertIn(
            "git status --porcelain=v1 --untracked-files=all failed or timed out",
            result["gitEvidence"]["snapshotDegraded"],
        )
        self.stop("gitd")

    def test_result_falls_back_to_legacy_result_md(self):
        worker = self.project / ".pi" / "workers" / "legacy"
        worker.mkdir(parents=True)
        (worker / "result.md").write_text("legacy cached answer", encoding="utf-8")
        (worker / "state.json").write_text(
            json.dumps({"slug": "legacy", "status": "idle", "settledResultFresh": True}),
            encoding="utf-8",
        )
        completed = self.command("result", "legacy")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("legacy cached answer", completed.stdout)
        self.assertIn("legacy state has no run artifact", completed.stderr)

        # Without the freshness flag the fallback must refuse.
        state_path = worker / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["settledResultFresh"] = False
        state_path.write_text(json.dumps(state), encoding="utf-8")
        refused = self.command("result", "legacy", check=False)
        self.assertEqual(refused.returncode, 2)
        self.assertIn("no completed run artifact", refused.stderr)


if __name__ == "__main__":
    import unittest
    unittest.main()
