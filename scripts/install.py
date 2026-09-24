#!/usr/bin/env python3
"""Install this checkout as a personal Codex or Claude Code skill."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parent.parent
TARGETS = {
    "codex": Path.home() / ".agents" / "skills" / "pi-delegate",
    "claude": Path.home() / ".claude" / "skills" / "pi-delegate",
}


def same_link(path: Path) -> bool:
    return path.is_symlink() and path.resolve() == ROOT


def install(path: Path, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if same_link(path):
        print(f"ok: {path} -> {ROOT}")
        return
    if path.exists() or path.is_symlink():
        if not replace:
            raise RuntimeError(f"refusing to replace existing path: {path}")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.backup-{stamp}")
        if backup.exists() or backup.is_symlink():
            raise RuntimeError(f"backup path already exists: {backup}")
        shutil.move(str(path), str(backup))
        print(f"backup: {path} -> {backup}")
    path.symlink_to(ROOT, target_is_directory=True)
    print(f"installed: {path} -> {ROOT}")


def uninstall(path: Path) -> None:
    if same_link(path):
        path.unlink()
        print(f"removed: {path}")
    elif path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to remove path not linked to this checkout: {path}")
    else:
        print(f"absent: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(TARGETS), default="codex")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    try:
        path = TARGETS[args.target]
        uninstall(path) if args.uninstall else install(path, args.replace)
    except (OSError, RuntimeError) as error:
        print(f"pi-delegate installer: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
