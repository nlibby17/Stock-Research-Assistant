"""Install the selected, hash-verified dependency lock using the current venv."""

import argparse
import platform
import subprocess
import sys
import sysconfig
from pathlib import Path

from lock_dependencies import check_locks

ROOT = Path(__file__).resolve().parents[1]


def select_lock(version, system, machine, mac_version="", implementation="CPython"):
    if implementation != "CPython" or not (3, 11) <= version < (3, 15):
        raise ValueError("Use standard CPython 3.11–3.14 (Python 3.12 on macOS 11).")
    supported = {"Windows": {"AMD64", "x86_64"}, "Darwin": {"x86_64", "arm64"}, "Linux": {"x86_64"}}
    if machine not in supported.get(system, set()):
        raise ValueError("Supported platforms: Windows x64, Mac Intel/Apple Silicon, Linux x64.")
    if system == "Darwin":
        major = int(mac_version.split(".")[0])
        if major < 11:
            raise ValueError("macOS 11 or newer is required.")
        if major == 11:
            if version != (3, 12):
                raise ValueError(
                    "macOS 11 requires Python 3.12. Rerun setup with Python 3.12 installed."
                )
            return "macos-11-py312.lock"
    return "standard.lock"


def install(root=ROOT, check_only=False):
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        raise ValueError("Free-threaded Python is not supported. Use a standard CPython build.")
    lock = select_lock(
        sys.version_info[:2],
        platform.system(),
        platform.machine(),
        platform.mac_ver()[0],
        platform.python_implementation(),
    )
    check_locks(root)
    print(
        f"Dependency profile: {lock} (Python {sys.version_info.major}.{sys.version_info.minor})",
        flush=True,
    )
    if check_only:
        return
    if sys.prefix == sys.base_prefix:
        raise ValueError(
            "Run this helper with the project's virtual-environment Python, not global Python."
        )
    # Dependencies (including pip and build tools) are pinned and hash checked first.
    # The local editable application is then built without resolving anything else.
    commands = [
        [
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--require-hashes",
            "--only-binary=:all:",
            "-r",
            str(root / "requirements" / lock),
        ],
        [
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--no-build-isolation",
            "-e",
            str(root),
        ],
        ["-m", "pip", "check"],
    ]
    for command in commands:
        subprocess.run([sys.executable, *command], cwd=root, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Check support and lock integrity only."
    )
    args = parser.parse_args()
    try:
        install(check_only=args.check)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Dependency setup failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
