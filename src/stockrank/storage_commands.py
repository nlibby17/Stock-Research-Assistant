"""Runtime inventory and explicit retention cleanup commands."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta

from stockrank.config import load_settings, validate_settings
from stockrank.runtime_maintenance import (
    apply_cleanup_plan,
    plan_cleanup,
    runtime_sizes,
    scan_cleanup_files,
    validate_cleanup_plan,
)
from stockrank.storage import Storage


def _human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def command_storage_status(_: argparse.Namespace) -> int:
    settings = load_settings()
    storage = Storage(settings.database_path)
    storage.initialize()
    sizes = runtime_sizes(settings.runtime_dir, settings.database_path)
    for name, size in sizes.items():
        print(f"{name}: {_human_bytes(size)}")
    print(f"total: {_human_bytes(sum(sizes.values()))}")
    for table, count in storage.counts().items():
        print(f"{table}: {count} rows")
    return 0


def command_storage_clean(args: argparse.Namespace) -> int:
    try:
        settings = load_settings()
    except (OSError, KeyError, ValueError) as exc:
        print(f"Cleanup refused: invalid configuration | {exc}", file=sys.stderr)
        return 1
    errors, _ = validate_settings(settings)
    if errors:
        for error in errors:
            print(f"Cleanup refused: {error}", file=sys.stderr)
        return 1
    apply = bool(args.apply)
    try:
        root = settings.runtime_dir.resolve()
        now = datetime.now(UTC)
        plan = plan_cleanup(
            root,
            scan_cleanup_files(root),
            report_cutoff=now - timedelta(days=int(settings.raw["retention"]["report_days"])),
            temporary_cutoff=now
            - timedelta(days=int(settings.raw["retention"]["temporary_file_days"])),
            sec_cache_cutoff=now
            - timedelta(
                hours=float(settings.raw.get("sec", {}).get("maximum_stale_cache_hours", 168.0))
            ),
        )
        validate_cleanup_plan(plan, root)
    except (OSError, ValueError) as exc:
        print(f"Cleanup refused: {exc}", file=sys.stderr)
        return 1
    storage = Storage(settings.database_path)
    storage.initialize()
    preview = storage.cleanup_database(
        int(settings.raw["retention"]["price_history_days"]), apply=apply
    )
    print(("Applied" if apply else "Dry run") + " database cleanup: " + json.dumps(preview))
    print(f"Expired runtime files: {len(plan.entries)}")
    for entry in plan.entries:
        print(f"  {entry.file.path}")
    if apply:
        try:
            apply_cleanup_plan(plan, root)
        except (OSError, ValueError) as exc:
            print(
                f"Runtime file cleanup stopped: {exc}. "
                "Database cleanup and earlier file removals may already have completed.",
                file=sys.stderr,
            )
            return 1
    if not apply:
        print(
            "Nothing was removed. Re-run with --apply to perform this exact policy-based cleanup."
        )
    return 0
