#!/usr/bin/env python3
"""Run Skillbox in temporary folders without reading the user's manifest."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

launcher = Path(__file__).resolve().parents[1] / "bin/skillbox.py"
if sys.version_info < (3, 11):
    sys.exit("This demo requires Python 3.11 or later.")

with tempfile.TemporaryDirectory(prefix="skillbox-demo-", dir="/tmp") as directory:
    root = Path(directory)
    roots = {name: root / name for name in ("claude", "codex")}
    for folder in roots.values():
        folder.mkdir()
    manifest = root / "skills.toml"
    manifest.write_text(
        "[roots]\n"
        + "\n".join(f"{name} = {json.dumps(str(folder))}" for name, folder in roots.items())
        + "\n[sources.demo]\n"
        + f"path = {json.dumps(str(root / 'source'))}\npriority = 1\n"
    )
    # This scratch run uses system utilities, not another installed Skillbox.
    env = {**os.environ, "SKILLBOX_MANIFEST": str(manifest), "SKILLBOX_STATE_DIR": str(root), "PATH": "/usr/bin:/bin"}

    def run(*args):
        print("\n$ skillbox " + " ".join(args), flush=True)
        subprocess.run([sys.executable, str(launcher), *args], env=env, check=True)

    print("One source skill. Two temporary tool folders.", flush=True)
    run("new", "hello", "--repo", "demo")
    run("list")
    source = root / "source/hello/SKILL.md"
    for name, folder in roots.items():
        assert (folder / "hello").is_symlink(), f"{name} did not receive a symlink"
        assert (folder / "hello/SKILL.md").samefile(source), f"{name} reads a different file"
    print("\nBoth tools point to source/hello/SKILL.md.", flush=True)

    source.write_text(source.read_text() + "\nAlways explain the next step in one sentence.\n")
    for folder in roots.values():
        assert "Always explain" in (folder / "hello/SKILL.md").read_text()
    print("Edited the source once. Both tools see the new instruction.", flush=True)
    run("doctor")
    print("\nDemo passed. Temporary folders are removed on exit.", flush=True)
