"""Exercise the actual Bash setup/update wrappers with offline command stubs."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def shell_project(tmp_path):
    bash = shutil.which("bash")
    if not bash and os.name == "nt":
        candidate = Path("C:/Program Files/Git/bin/bash.exe")
        if candidate.exists():
            bash = str(candidate)
    if not bash:
        pytest.skip("Bash is unavailable")
    project = tmp_path / "Project With Spaces"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    for name in ("setup.sh", "update.sh"):
        shutil.copy2(ROOT / "scripts" / name, scripts / name)
    (project / ".git").mkdir()
    (project / ".env.example").write_text("SEC_USER_AGENT=example\n")
    (project / ".env").write_text("SEC_USER_AGENT=private\n")
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    stub = bin_dir / "python3.12"
    stub.write_text(
        r"""#!/usr/bin/env bash
set -eu
if [[ "$1" == "-c" ]]; then
    case "$2" in
        *"print(sys.version_info.minor)"*) echo 12 ;;
        *"print((3, 11)"*|*"== (3, 12)"*) echo True ;;
        *"micro"*) echo 3.12.10 ;;
        *) echo 3.12 ;;
    esac
elif [[ "$1" == "-m" && "$2" == "venv" ]]; then
    echo venv >> "$TEST_LOG"
    mkdir -p .venv/bin
    cp "$0" .venv/bin/python
    cp "$0" .venv/bin/stockrank
elif [[ "$1" == *"install_dependencies.py" ]]; then
    echo dependencies >> "$TEST_LOG"
    if [[ "$TEST_FAILURE" == "dependencies" ]]; then exit 23; fi
else
    echo "$1" >> "$TEST_LOG"
fi
""",
        encoding="utf-8",
        newline="\n",
    )
    helpers = {
        "uname": "echo Darwin\n",
        "sw_vers": "echo 11.7.10\n",
        "git": 'case "$1" in\n status) ;;\n symbolic-ref) echo main ;;\n pull) echo pull >> "$TEST_LOG" ;;\n esac\n',
        "shasum": "echo stable-personal-hash\n",
    }
    for name, body in helpers.items():
        (bin_dir / name).write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
    for path in bin_dir.iterdir():
        path.chmod(0o755)
    log = tmp_path / "calls.txt"

    def run(script, failure=""):
        log.unlink(missing_ok=True)
        # Let Bash prepend its native path, including on Git Bash for Windows.
        command = 'export PATH="$TEST_BIN:$PATH"; bash "$TEST_SCRIPT" "$TEST_OPTION"'
        if os.name == "nt":
            command = 'TEST_BIN=$(cygpath -u "$TEST_BIN"); ' + command
        result = subprocess.run(
            [bash, "-c", command],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "TEST_BIN": bin_dir.as_posix(),
                "TEST_SCRIPT": (scripts / script).as_posix(),
                "TEST_OPTION": "--no-desktop-launcher" if script == "setup.sh" else "--skip-tests",
                "TEST_FAILURE": failure,
                "TEST_LOG": log.as_posix(),
                "CI": "1",
            },
        )
        return result, log.read_text().splitlines() if log.exists() else []

    return project, run


def test_setup_success_rerun_and_update_use_same_dependency_helper(shell_project):
    project, run = shell_project
    original = (project / ".env").read_bytes()
    result, calls = run("setup.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["venv", "dependencies"]
    result, calls = run("setup.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["dependencies"]
    result, calls = run("update.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["pull", "dependencies", "setup-check", "config-check"]
    assert (project / ".env").read_bytes() == original


@pytest.mark.parametrize("script", ["setup.sh", "update.sh"])
def test_dependency_failure_blocks_success_and_validation(shell_project, script):
    project, run = shell_project
    if script == "update.sh":
        result, _ = run("setup.sh")
        assert result.returncode == 0, result.stderr
    original = (project / ".env").read_bytes()
    result, calls = run(script, "dependencies")
    assert result.returncode != 0
    assert calls[-1] == "dependencies"
    assert "setup-check" not in calls
    assert "complete." not in result.stdout
    assert (project / ".env").read_bytes() == original
