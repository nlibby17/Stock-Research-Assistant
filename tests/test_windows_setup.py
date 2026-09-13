"""Run the real installer against offline native-command stubs in temporary projects."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows installer behavior")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def native_python(tmp_path_factory):
    folder = tmp_path_factory.mktemp("installer-native-stub")
    source = folder / "Stub.cs"
    executable = folder / "py.exe"
    source.write_text(
        r"""
using System;
using System.IO;
using System.Linq;
public class Stub {
    public static int Main(string[] args) {
        string stage = args.Contains("-c") ? "probe" :
            args.Contains("venv") ? "venv" : (args.Contains("pip") || args.Any(a => a.EndsWith("install_dependencies.py"))) ? "pip" : "validate";
        File.AppendAllText(Environment.GetEnvironmentVariable("SETUP_TEST_LOG"), stage + "\n");
        string failure = Environment.GetEnvironmentVariable("SETUP_TEST_FAILURE");
        if (failure == stage) {
            Console.Error.WriteLine("Simulated " + stage + " failure");
            return 23;
        }
        if (stage == "probe") {
            Console.WriteLine(failure == "bad-version" ? "invalid" : "3.12");
        } else if (stage == "venv" && failure != "incomplete-venv") {
            Directory.CreateDirectory(".venv/Scripts");
            File.Copy(Environment.GetCommandLineArgs()[0], ".venv/Scripts/python.exe", true);
        } else if (stage == "pip") {
            File.Copy(Environment.GetCommandLineArgs()[0], ".venv/Scripts/stockrank.exe", true);
        }
        return 0;
    }
}
""",
        encoding="utf-8",
    )
    compile_script = folder / "compile.ps1"
    compile_script.write_text(
        "param($Source, $Output)\n$ErrorActionPreference = 'Stop'\n"
        "Add-Type -Path $Source -OutputAssembly $Output -OutputType ConsoleApplication\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(compile_script),
            str(source),
            str(executable),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return executable


@pytest.fixture(params=["powershell", "pwsh"])
def installer(request, tmp_path, native_python):
    shell = shutil.which(request.param)
    if not shell:
        pytest.skip(f"{request.param} is not installed")
    project = tmp_path / "Project With Spaces"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/setup.ps1", scripts / "setup.ps1")
    (project / ".env.example").write_text("SEC_USER_AGENT=placeholder\n", encoding="utf-8")
    (project / ".env").write_text("SEC_USER_AGENT=existing\nPERSONAL=keep\n", encoding="utf-8")
    (scripts / "install-launcher.ps1").write_text(
        "if ($env:SETUP_TEST_FAILURE -eq 'shortcut') { throw 'Simulated shortcut failure' }\n"
        "Set-Content -LiteralPath (Join-Path $PSScriptRoot '../shortcut-created') -Value yes\n",
        encoding="utf-8",
    )
    log = tmp_path / "commands.log"
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": str(native_python.parent) + os.pathsep + os.environ["PATH"],
            "SETUP_TEST_LOG": str(log),
            "CI": "1",
        }
    )

    def run(failure="", *extra):
        log.unlink(missing_ok=True)
        result = subprocess.run(
            [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(scripts / "setup.ps1"),
                *extra,
            ],
            cwd=tmp_path,
            env={**environment, "SETUP_TEST_FAILURE": failure},
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result, log.read_text().splitlines() if log.exists() else []

    return project, run


@pytest.mark.parametrize(
    "failure,message,expected",
    [
        ("probe", "Python version check failed", ["probe"]),
        ("bad-version", "Python returned an invalid version", ["probe"]),
        ("venv", "environment creation failed", ["probe", "venv"]),
        ("incomplete-venv", "environment is incomplete", ["probe", "venv"]),
        ("pip", "Dependency installation failed", ["probe", "venv", "pip"]),
        ("validate", "Setup validation failed", ["probe", "venv", "pip", "validate"]),
        ("shortcut", "Simulated shortcut failure", ["probe", "venv", "pip", "validate"]),
    ],
)
def test_setup_stops_at_failed_step(installer, failure, message, expected):
    project, run = installer
    original_env = (project / ".env").read_bytes()
    result, calls = run(failure, "-CreateDesktopShortcut", "-SecUserAgent", "Test test@example.com")
    assert result.returncode != 0
    assert calls == expected
    assert message in result.stdout + result.stderr
    assert "Installation complete." not in result.stdout
    assert not (project / "shortcut-created").exists()
    if failure not in {"validate", "shortcut"}:
        assert (project / ".env").read_bytes() == original_env


def test_success_and_rerun_preserve_existing_environment(installer):
    project, run = installer
    result, calls = run("", "-CreateDesktopShortcut", "-SecUserAgent", "Test test@example.com")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["probe", "venv", "pip", "validate"]
    assert "Installation complete. Setup validation passed." in result.stdout
    assert (project / "shortcut-created").exists()
    env_before = (project / ".env").read_bytes()
    marker = project / ".venv" / "keep-installed-data"
    marker.write_text("keep")
    result, calls = run("", "-SkipDesktopShortcut")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["probe", "pip"]
    assert "Installation complete." in result.stdout
    assert marker.read_text() == "keep"
    assert (project / ".env").read_bytes() == env_before


def test_failed_install_can_be_retried(installer):
    project, run = installer
    result, _ = run("pip", "-CreateDesktopShortcut")
    assert result.returncode != 0
    result, calls = run("", "-CreateDesktopShortcut")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["probe", "pip"]
    assert (project / "shortcut-created").exists()
    assert "Installation complete." in result.stdout


def test_new_install_creates_config_only_after_dependencies_succeed(installer):
    project, run = installer
    (project / ".env").unlink()
    result, _ = run("pip", "-SkipDesktopShortcut")
    assert result.returncode != 0
    assert not (project / ".env").exists()
    result, calls = run("", "-SkipDesktopShortcut")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["probe", "pip"]
    assert (project / ".env").read_bytes() == (project / ".env.example").read_bytes()
    assert "Installation complete." in result.stdout
