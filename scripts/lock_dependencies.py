"""Regenerate reviewed dependency locks, or check their recorded input/file hashes."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UV_VERSION = "0.12.10"
INPUTS = ("pyproject.toml", "requirements/toolchain.in", "constraints/macos-11-py312.txt")
LOCKS = ("requirements/standard.lock", "requirements/macos-11-py312.lock")
MANIFEST = "requirements/lock-inputs.json"


def digest(path):
    # Normalize Git's Windows checkout line endings, while hashing all other content.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def manifest(root):
    return {
        "uv_version": UV_VERSION,
        "sha256": {name: digest(root / name) for name in (*INPUTS, *LOCKS)},
    }


def check_locks(root=ROOT):
    if json.loads((root / MANIFEST).read_text(encoding="utf-8")) != manifest(root):
        raise ValueError(
            "Dependency locks are stale or modified. Regenerate with python scripts/lock_dependencies.py and review the changes."
        )


def regenerate(root=ROOT, upgrade=False):
    uv = shutil.which("uv")
    if not uv:
        raise ValueError(
            f"Install the maintainer tool uv=={UV_VERSION} in a separate tools environment."
        )
    result = subprocess.run([uv, "--version"], capture_output=True, text=True, check=True)
    if result.stdout.split()[1] != UV_VERSION:
        raise ValueError(f"Lock generation requires uv {UV_VERSION}.")
    with tempfile.TemporaryDirectory() as temporary:
        outputs = []
        for lock, version in zip(LOCKS, ("3.11", "3.12"), strict=True):
            output = Path(temporary) / Path(lock).name
            if (root / lock).exists():
                shutil.copy2(root / lock, output)
            command = [
                uv,
                "pip",
                "compile",
                "pyproject.toml",
                "requirements/toolchain.in",
                "--extra",
                "dev",
                "--universal",
                "--python-version",
                version,
                "--generate-hashes",
                "--no-build",
                "--default-index",
                "https://pypi.org/simple",
                "--custom-compile-command",
                "python scripts/lock_dependencies.py",
                "--output-file",
                str(output),
                "--quiet",
            ]
            if "macos" in lock:
                command.extend(["--constraint", "constraints/macos-11-py312.txt"])
            if upgrade:
                command.append("--upgrade")
            subprocess.run(command, cwd=root, check=True)
            outputs.append((root / lock, output.read_bytes()))
        # Do not replace either lock if one profile fails resolution.
        for destination, content in outputs:
            destination.write_bytes(content)
        (root / MANIFEST).write_text(json.dumps(manifest(root), indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Offline integrity/freshness check.")
    parser.add_argument(
        "--upgrade", action="store_true", help="Intentionally refresh all package pins."
    )
    args = parser.parse_args()
    if args.check and args.upgrade:
        parser.error("--check and --upgrade cannot be combined")
    try:
        if args.check:
            check_locks()
        else:
            regenerate(upgrade=args.upgrade)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Dependency lock check/generation failed: {error}\n")
    print("Dependency locks are current.")


if __name__ == "__main__":
    main()
