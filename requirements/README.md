# Dependency locks and Python support

Guided setup, updates and the Python CI matrix consume these locks. End users do
not need uv. The frontend continues to use its existing `package-lock.json`.

| Platform | Standard CPython | Dependency lock |
|---|---|---|
| Windows 10/11, x64 Intel/AMD | 3.11, 3.12, 3.13, 3.14 | `standard.lock` |
| macOS 12+, Intel or Apple Silicon | 3.11, 3.12, 3.13, 3.14 | `standard.lock` |
| macOS 11, Intel or Apple Silicon | 3.12 only | `macos-11-py312.lock` |
| Linux x64 (CI and manual installation) | 3.11, 3.12, 3.13, 3.14 | `standard.lock` |

Python 3.12 is the common recommended version. Python 3.15+, PyPy, 32-bit Python,
Windows ARM and free-threaded Python builds are outside this support policy.
Supported Python minor versions are bounded in `pyproject.toml` and covered by CI.
Python patch releases and OS/native-library differences still matter; the locks
make package selection repeatable, not the entire operating system identical.

The macOS 11 lock preserves PyArrow 15.0.2 from the existing compatibility constraint
and resolves compatible transitive dependencies (including NumPy 1.26.4). It must
not be used with Python 3.13/3.14. Wheel checks cover the macOS 11 deployment target
for both architectures. CI also executes this dependency profile on current macOS;
that does **not** replace a native-import smoke test on an actual macOS 11 machine.

## Installation and updates

Use the normal `scripts/setup.ps1` / `scripts/setup.sh` and update scripts. They call
`scripts/install_dependencies.py` with the local virtual-environment interpreter.
The helper checks platform support and `lock-inputs.json` before making changes,
installs exact dependencies with `--require-hashes --only-binary=:all:`, installs
the local project editable with `--no-deps --no-build-isolation`, and runs `pip check`.
Pip, setuptools and wheel are locked too, so editable builds do not download an
uncontrolled build environment. Any failed command prevents subsequent validation
or success reporting. No fallback to compiling third-party native packages is used.

Updates restore the locked versions of managed packages, including downgrades when
necessary. Unrelated packages already present in `.venv` are retained; `pip check`
reports conflicts. For an exact package inventory, use a fresh separate environment.
Do not delete the working environment, private configuration or runtime data to test.
Normal dependency updates require network access to retrieve the pinned artifacts.

## Deliberate lock regeneration

Maintainer tool: **uv 0.12.10**, installed separately from the application environment.
For example, create a tools environment and install `uv==0.12.10` into it, then put
its executable directory on PATH. End-user installers do not install this tool.

From the repository root:

```text
python scripts/lock_dependencies.py
python scripts/lock_dependencies.py --check
```

The first command resolves both profiles from `pyproject.toml`,
`requirements/toolchain.in` and the Mac constraint. Existing pins remain preferences;
changing an input may require different pins. Both resolutions must succeed before
either lock is replaced. The second command is offline: it verifies recorded input
and lock hashes, normalizing Windows/Unix line endings. It does not contact an index
or prove compatibility by itself. Never repair a mismatch by hand-editing the hash
manifest; regenerate and review the locks.

To intentionally upgrade all dependencies:

```text
python scripts/lock_dependencies.py --upgrade
python scripts/check_lock_wheels.py
```

Review the version/hash diff and any new transitive dependencies. Test clean installs
and reruns with both locks, then run `pip check`, native-library import checks and the
full test suite. Validate the app's live workflow before releasing provider upgrades.
The wheel check uses uv dry runs for 18 Python/platform/deployment-target combinations;
it cannot execute foreign native code. CI runs the full standard-profile suite on
Windows, macOS and Linux for all four Python versions, plus the Mac compatibility
profile and wheel checks. CI results must be checked after pushing a change.

Changing supported Python versions, compatibility constraints or the uv version is
an intentional maintenance task: update the installer policy, CI, this guide and
locks together. Lock generation is never performed during normal setup or updates.

Background: [uv lock compilation](https://docs.astral.sh/uv/pip/compile/) and
[pip hash-checking mode](https://pip.pypa.io/en/stable/topics/secure-installs/).
