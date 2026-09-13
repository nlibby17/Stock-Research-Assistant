"""Check wheel availability for supported target profiles without installing them."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    targets = [
        (version, target, "12.0", "standard.lock")
        for version in ("3.11", "3.12", "3.13", "3.14")
        for target in (
            "x86_64-pc-windows-msvc",
            "x86_64-unknown-linux-gnu",
            "x86_64-apple-darwin",
            "aarch64-apple-darwin",
        )
    ]
    targets += [
        ("3.12", target, "11.0", "macos-11-py312.lock")
        for target in ("x86_64-apple-darwin", "aarch64-apple-darwin")
    ]
    for version, target, macos, lock in targets:
        print(f"Checking {target}, Python {version}, macOS minimum {macos}: {lock}", flush=True)
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--dry-run",
                "--reinstall",
                "--python",
                sys.executable,
                "--python-version",
                version,
                "--python-platform",
                target,
                "--no-build",
                "--require-hashes",
                "-r",
                str(ROOT / "requirements" / lock),
                "--quiet",
            ],
            env={**os.environ, "MACOSX_DEPLOYMENT_TARGET": macos},
            check=True,
        )
    print(
        f"All {len(targets)} wheel target checks passed. This does not execute foreign native code."
    )


if __name__ == "__main__":
    main()
