"""Local review workers: bounded public-data refreshes with explicit progress/results."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

from stockrank.config import load_settings
from stockrank.universe_discovery import atomic_json


class ProgressOutput:
    """Flush every update for the browser and retain explicit diagnostic lines."""

    def __init__(self, stream):
        self.stream = stream
        self.pending = ""
        self.warnings = []

    def write(self, text):
        self.stream.write(text)
        self.stream.flush()
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if line.startswith(("WARNING:", "ERROR:")):
                self.warnings.append(line)
        return len(text)

    def flush(self):
        self.stream.flush()


def assess_workflow(exit_code, steps):
    required = {"Configuration validation", "Yahoo ranking and base report", "Final validation"}
    passed = {s["step"] for s in steps if s["exit_code"] == 0}
    failed = [s["step"] for s in steps if s["exit_code"] != 0]
    auxiliary = {
        "SEC Company Facts sync",
        "SEC financial snapshot build",
        "SEC/Yahoo shadow comparison",
    }
    if not required <= passed or set(failed) - auxiliary or (exit_code and not failed):
        raise ValueError(
            "Your decision is saved, but the ranking report could not be validated. "
            + (
                "Steps requiring attention: " + ", ".join(failed)
                if failed
                else "The workflow did not finish all required checks."
            )
        )
    return failed


def verify_report(data, receipt, *, previous_run=None):
    run = data.get("run") or {}
    expected = {m["ticker"] for m in receipt["members"]}
    if (
        run.get("status") != "completed"
        or run.get("universe_name") != receipt["version"]
        or {r["ticker"] for r in data.get("results", [])} != expected
        or run.get("run_id") == previous_run
    ):
        raise ValueError(
            "The completed report does not match the selected universe. Retry the report build."
        )
    return run["run_id"]


def verify_active(settings, receipt):
    if settings.raw["universe"]["name"] != receipt["version"] or {
        s.ticker for s in settings.universe
    } != {m["ticker"] for m in receipt["members"]}:
        raise ValueError(
            "The active universe has changed since this decision. Review the current universe first."
        )


class ReviewJob:
    def __init__(self, settings):
        self.settings = settings
        self.process = None
        self.log = None
        self.result = None
        self.kind = None
        self.reader = None

    def start(self, kind, payload):
        if self.process and self.process.poll() is None:
            raise ValueError("A refresh is already running")
        self.kind, self.result = kind, None
        folder = self.settings.runtime_dir / "universe" / "jobs"
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / f"{uuid4().hex}.json"
        self.log_path = self.path.with_suffix(".log")
        request = self.path.with_suffix(".request.json")
        atomic_json(
            request,
            {
                "kind": kind,
                "payload": payload,
                "root": str(self.settings.root),
                "result": str(self.path),
            },
        )
        try:
            self.log = self.log_path.open("w", encoding="utf-8")
            self.process = subprocess.Popen(
                [sys.executable, "-u", "-m", "stockrank.universe_jobs", str(request)],
                cwd=self.settings.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.reader = threading.Thread(target=self.relay_output, daemon=True)
            self.reader.start()
        except OSError as exc:
            self.result = {"status": "error", "message": str(exc)}
            if self.log:
                self.log.close()

    def relay_output(self):
        with self.process.stdout as output:
            for line in output:
                self.log.write(line)
                self.log.flush()
                try:
                    print(line, end="", flush=True)
                except (OSError, UnicodeError):
                    pass  # The file/browser log remains available if the terminal closes.

    def poll(self):
        if self.result is not None:
            return self.result
        if self.process is None:
            return {"status": "idle"}
        if self.process.poll() is None:
            return {"status": "running"}
        if self.reader:
            self.reader.join(timeout=5)
        if self.log:
            self.log.close()
        self.result = (
            json.loads(self.path.read_text(encoding="utf-8"))
            if self.path.exists()
            else {
                "status": "error",
                "message": "The refresh stopped before finishing. See the terminal log and retry.",
            }
        )
        return self.result

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.reader:
            self.reader.join(timeout=5)
        if self.log:
            self.log.close()
        # Clean only a lock owned by our confirmed-stopped worker, never another operation.
        lock = self.settings.runtime_dir / "universe" / "operation.lock"
        if (
            self.process
            and self.process.poll() is not None
            and lock.exists()
            and lock.read_text(encoding="utf-8").strip() == str(self.process.pid)
        ):
            lock.unlink()


def execute(request):
    from stockrank.universe_commands import discovery_lock, load_policy, run_discovery

    root = Path(request["root"])
    os.chdir(root)
    settings = load_settings(root)
    payload = request["payload"]
    if request["kind"] == "nominate":
        with discovery_lock(settings):
            policy = replace(load_policy(settings), consider_tickers=tuple(payload["tickers"]))
            atomic_json(root / "config/universe-discovery.local.json", asdict(policy))
        path = run_discovery(load_settings(root), policy, preview=True)
        return {"status": "complete", "proposal_path": str(path)}
    if request["kind"] != "report":
        raise ValueError("Unknown review job")
    from stockrank.cli import command_daily_report
    from stockrank.dashboard_data import load_dashboard

    verify_active(settings, payload)
    previous = (load_dashboard(root).get("run") or {}).get("run_id")
    steps = []
    output = ProgressOutput(sys.stdout)
    with redirect_stdout(output), redirect_stderr(output):
        exit_code = command_daily_report(
            Namespace(force=bool(payload.get("force", False)), step_results=steps)
        )
    failed = assess_workflow(exit_code, steps)
    verify_active(load_settings(root), payload)
    run_id = verify_report(load_dashboard(root), payload, previous_run=previous)
    warnings = (
        ["Rankings passed validation. Additional SEC checks need attention: " + ", ".join(failed)]
        + list(dict.fromkeys(output.warnings))
        if failed
        else []
    )
    atomic_json(
        settings.runtime_dir / "universe" / f"report-status-{run_id}.json",
        {"run_id": run_id, "warnings": warnings},
    )
    return {"status": "complete", "run_id": run_id, "warnings": warnings}


def main():
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        result = execute(request)
    except Exception as exc:  # noqa: BLE001 - persist worker failure for the review UI.
        print(f"Refresh failed: {exc}", flush=True)
        result = {"status": "error", "message": str(exc)}
    atomic_json(Path(request["result"]), result)
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
