"""Lock integrity, platform selection and install failure boundaries (offline)."""

import importlib
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def installer(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("install_dependencies")


@pytest.fixture
def locked_project(tmp_path, installer):
    root = tmp_path / "Project With Spaces"
    locks = importlib.import_module("lock_dependencies")
    for name in (*locks.INPUTS, *locks.LOCKS, locks.MANIFEST):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return root


@pytest.mark.parametrize(
    "system,machine,mac,version,expected",
    [
        ("Windows", "AMD64", "", (3, 11), "standard.lock"),
        ("Windows", "AMD64", "", (3, 14), "standard.lock"),
        ("Linux", "x86_64", "", (3, 13), "standard.lock"),
        ("Darwin", "arm64", "12.7", (3, 12), "standard.lock"),
        ("Darwin", "x86_64", "15.0", (3, 14), "standard.lock"),
        ("Darwin", "arm64", "11.7.10", (3, 12), "macos-11-py312.lock"),
        ("Darwin", "x86_64", "11.7", (3, 12), "macos-11-py312.lock"),
    ],
)
def test_supported_profile_selection(installer, system, machine, mac, version, expected):
    assert installer.select_lock(version, system, machine, mac) == expected


@pytest.mark.parametrize(
    "version,system,machine,mac,implementation",
    [
        ((3, 10), "Windows", "AMD64", "", "CPython"),
        ((3, 15), "Windows", "AMD64", "", "CPython"),
        ((4, 0), "Windows", "AMD64", "", "CPython"),
        ((3, 12), "Windows", "x86", "", "CPython"),
        ((3, 12), "Windows", "ARM64", "", "CPython"),
        ((3, 12), "Linux", "aarch64", "", "CPython"),
        ((3, 12), "Darwin", "x86_64", "10.15", "CPython"),
        ((3, 13), "Darwin", "arm64", "11.7", "CPython"),
        ((3, 11), "Darwin", "x86_64", "11.7", "CPython"),
        ((3, 12), "Linux", "x86_64", "", "PyPy"),
    ],
)
def test_unsupported_targets_stop(installer, version, system, machine, mac, implementation):
    with pytest.raises(ValueError):
        installer.select_lock(version, system, machine, mac, implementation)


@pytest.mark.parametrize(
    "name",
    [
        "pyproject.toml",
        "requirements/toolchain.in",
        "constraints/macos-11-py312.txt",
        "requirements/standard.lock",
        "requirements/macos-11-py312.lock",
    ],
)
def test_changed_input_or_lock_is_rejected(installer, locked_project, name):
    path = locked_project / name
    path.write_bytes(path.read_bytes() + b"\n# changed\n")
    with pytest.raises(ValueError, match="stale or modified"):
        installer.check_locks(locked_project)


def test_integrity_check_accepts_windows_and_unix_line_endings(installer, locked_project):
    for path in locked_project.rglob("*"):
        if path.is_file():
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    installer.check_locks(locked_project)


@pytest.fixture
def commands(installer, monkeypatch):
    run = Mock()
    monkeypatch.setattr(installer, "subprocess", SimpleNamespace(run=run))
    # Override only the helper's sys reference, not the running test interpreter.
    version = (3, 14)

    class Version(tuple):
        major = 3
        minor = 14

    monkeypatch.setattr(
        installer,
        "sys",
        SimpleNamespace(
            prefix="venv",
            base_prefix="base",
            executable="venv-python",
            version_info=Version(version),
        ),
    )
    monkeypatch.setattr(
        installer,
        "platform",
        SimpleNamespace(
            system=lambda: "Windows",
            machine=lambda: "AMD64",
            mac_ver=lambda: ("", "", ""),
            python_implementation=lambda: "CPython",
        ),
    )
    return run


def test_install_uses_hashes_then_local_build_then_consistency_check(
    installer, locked_project, commands
):
    installer.install(locked_project)
    actual = [call.args[0] for call in commands.call_args_list]
    assert len(actual) == 3
    assert actual[0] == [
        "venv-python",
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--require-hashes",
        "--only-binary=:all:",
        "-r",
        str(locked_project / "requirements/standard.lock"),
    ]
    assert actual[1] == [
        "venv-python",
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-deps",
        "--no-build-isolation",
        "-e",
        str(locked_project),
    ]
    assert actual[2] == ["venv-python", "-m", "pip", "check"]
    assert all(
        call.kwargs == {"cwd": locked_project, "check": True} for call in commands.call_args_list
    )


@pytest.mark.parametrize("failed_step", [0, 1, 2])
def test_install_stops_at_failed_command(installer, locked_project, commands, failed_step):
    commands.side_effect = [None] * failed_step + [subprocess.CalledProcessError(23, "pip")]
    with pytest.raises(subprocess.CalledProcessError):
        installer.install(locked_project)
    assert commands.call_count == failed_step + 1


def test_check_mode_does_not_install(installer, locked_project, commands):
    installer.install(locked_project, check_only=True)
    commands.assert_not_called()


def test_global_python_is_not_modified(installer, locked_project, commands):
    installer.sys.prefix = installer.sys.base_prefix
    with pytest.raises(ValueError, match="virtual-environment"):
        installer.install(locked_project)
    commands.assert_not_called()


def test_stale_lock_stops_before_install(installer, locked_project, commands):
    (locked_project / "pyproject.toml").write_text("changed")
    with pytest.raises(ValueError, match="stale"):
        installer.install(locked_project)
    commands.assert_not_called()


def test_free_threaded_python_stops_before_install(
    installer, locked_project, commands, monkeypatch
):
    monkeypatch.setattr(installer, "sysconfig", SimpleNamespace(get_config_var=lambda _: 1))
    with pytest.raises(ValueError, match="Free-threaded"):
        installer.install(locked_project)
    commands.assert_not_called()
