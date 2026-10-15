"""Check the distributable plugin without touching the user's installation."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from base import ROOT


class PackagingTest(unittest.TestCase):
    def test_bundle_is_self_contained_and_excludes_checkout_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bundle"
            built = subprocess.run(
                [sys.executable, str(ROOT / "scripts/package_plugin.py"), "--output", str(output)],
                capture_output=True, text=True,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            plugin = output / "pi-delegate"
            skill = plugin / "skills/pi-delegate"
            for folder in ("references", "agents", "scripts"):
                self.assertTrue((skill / folder).is_dir())
            self.assertEqual((skill / "SKILL.md").read_bytes(), (ROOT / "SKILL.md").read_bytes())
            self.assertIn("$pi-delegate:pi-delegate", (skill / "agents/openai.yaml").read_text())
            for host in (".codex-plugin", ".claude-plugin"):
                manifest = json.loads((plugin / host / "plugin.json").read_text())
                self.assertTrue((plugin / manifest["skills"]).is_dir())
            with zipfile.ZipFile(next(output.glob("*.zip"))) as archive:
                entries = set(archive.namelist())
                self.assertIn("plugin.json", entries)
                self.assertIn("skills/pi-delegate/scripts/pi_rpc_worker.py", entries)
                self.assertFalse(any(part in name.split("/") for name in entries for part in (".git", ".pi", ".agents", "__pycache__", "tests")))
                extracted = Path(temporary) / "extracted"
                archive.extractall(extracted)
            helper = extracted / "skills/pi-delegate/scripts/pi_rpc_worker.py"
            helped = subprocess.run([sys.executable, str(helper), "watch", "--help"], capture_output=True, text=True)
            self.assertEqual(helped.returncode, 0, helped.stderr)
            self.assertIn("--full", helped.stdout)
            # Installation is managed by the plugin host, not a nested
            # standalone installer with a different skill namespace.
            self.assertFalse(helper.with_name("install.py").exists())
            refused = subprocess.run(
                [sys.executable, str(ROOT / "scripts/package_plugin.py"), "--output", str(output)],
                capture_output=True, text=True,
            )
            self.assertEqual(refused.returncode, 2)
            self.assertIn("refusing to overwrite", refused.stderr)
