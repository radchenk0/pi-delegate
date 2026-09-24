"""Shared scaffolding for pi-delegate integration tests (stdlib only)."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "pi_rpc_worker.py"
INSTALLER = ROOT / "scripts" / "install.py"
FAKE_SOURCE = ROOT / "tests" / "fake_pi.py"


class WorkerTestCase(unittest.TestCase):
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

    def command(self, *args, check=True, env=None, timeout=10):
        return subprocess.run(
            [sys.executable, str(HELPER), *args, "--cwd", str(self.project)],
            env=env or self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
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

    def stop(self, slug, timeout=10):
        self.command("stop", slug, timeout=timeout)

    def run_result(self, state):
        return json.loads(Path(state["lastResultPath"]).read_text(encoding="utf-8"))
