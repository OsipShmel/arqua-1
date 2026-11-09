"""Public provenance metadata, without environment dumps or provider secrets."""

from __future__ import annotations

import hashlib
import platform
import subprocess
from importlib.metadata import distributions
from pathlib import Path


def environment_manifest() -> dict:
    packages = {
        d.metadata["Name"]: d.version for d in distributions() if d.metadata["Name"]
    }
    root = next(
        (p for p in Path(__file__).resolve().parents if (p / "uv.lock").is_file()), None
    )
    revision, lock_sha256, dirty = None, None, None
    if root is not None:
        lock_sha256 = hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()
        try:
            result = subprocess.run(
                ["git", "-c", f"safe.directory={root.as_posix()}", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                revision = result.stdout.strip()
                status = subprocess.run(
                    [
                        "git",
                        "-c",
                        f"safe.directory={root.as_posix()}",
                        "status",
                        "--porcelain",
                    ],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": dict(sorted(packages.items())),
        "git_revision": revision,
        "workspace_lock_sha256": lock_sha256,
        "git_dirty": dirty,
        "agent_source_sha256": hashlib.sha256(
            b"".join(
                p.name.encode() + b"\0" + p.read_bytes()
                for p in sorted(Path(__file__).parent.glob("*.py"))
            )
        ).hexdigest(),
    }
