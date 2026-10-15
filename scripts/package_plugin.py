#!/usr/bin/env python3
"""Build a self-contained Pi Delegate plugin, ZIP, and local marketplaces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parent.parent


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(output: Path) -> Path:
    output = output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"refusing to overwrite output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "packaging/plugin.json").read_text(encoding="utf-8"))
    name = manifest["name"]
    with tempfile.TemporaryDirectory(prefix=".pi-plugin-build-", dir=output.parent) as temporary:
        stage = Path(temporary) / "package"
        plugin = stage / name
        skill = plugin / "skills" / name
        skill.mkdir(parents=True)
        shutil.copy2(ROOT / "SKILL.md", skill / "SKILL.md")
        shutil.copytree(ROOT / "references", skill / "references", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "agents", skill / "agents")
        # Native plugin discovery namespaces skills, unlike a standalone link.
        agent_metadata = skill / "agents/openai.yaml"
        agent_metadata.write_text(
            agent_metadata.read_text(encoding="utf-8").replace(f"${name}", f"${name}:{name}"),
            encoding="utf-8",
        )
        (skill / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts/pi_rpc_worker.py", skill / "scripts/pi_rpc_worker.py")

        write_json(plugin / "plugin.json", manifest)
        metadata = {key: manifest[key] for key in ("name", "version", "description", "author")}
        write_json(plugin / ".codex-plugin/plugin.json", {
            **metadata, "skills": "./skills/",
            "interface": manifest["extensions"]["com.openai"]["interface"],
        })
        write_json(plugin / ".claude-plugin/plugin.json", {**metadata, "skills": "./skills/"})
        (plugin / "README.md").write_text(
            "# Pi Delegate\n\n"
            "A skills plugin for Codex and Claude Code. Requires Python 3, Git, "
            "and a configured Pi on PATH. Herdr is optional.\n\n"
            "The bundled entrypoint is skills/pi-delegate/SKILL.md; resolve all "
            "helper paths relative to that skill. Workers stay in RPC mode.\n\n"
            "Installing this plugin does not install Pi, configure providers, "
            "or install the separate Herdr skill.\n",
            encoding="utf-8",
        )
        catalog = {"name": "pi-delegate-local", "interface": {"displayName": "Pi Delegate"}, "plugins": [{
            "name": name,
            "source": {"source": "local", "path": f"./{name}"},
        }]}
        write_json(stage / ".agents/plugins/marketplace.json", catalog)
        write_json(stage / ".claude-plugin/marketplace.json", {
            "name": "pi-delegate-local", "owner": {"name": "pi-delegate"},
            "metadata": {"description": "Local Pi Delegate plugin builds for RPC delegation."},
            "plugins": [{"name": name, "source": f"./{name}", "description": manifest["description"]}],
        })
        with zipfile.ZipFile(stage / f"{name}-{manifest['version']}.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(plugin.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(plugin).as_posix())
        # Publish only a complete build; never include checkout/runtime files.
        stage.rename(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    manifest = json.loads((ROOT / "packaging/plugin.json").read_text(encoding="utf-8"))
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / f"{manifest['name']}-{manifest['version']}")
    args = parser.parse_args()
    try:
        print(build(args.output))
    except (OSError, RuntimeError, ValueError) as error:
        print(f"pi-delegate package: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
