"""Review, schedule, and explicit prospective activation of universe proposals."""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stockrank.config import load_settings
from stockrank.customization import activate_proposed_universe
from stockrank.models import Security
from stockrank.universe_discovery import (
    POLICY_VERSION,
    PROFILE_LABELS,
    DiscoveryPolicy,
    atomic_json,
    build_proposal,
    context_key,
    digest,
    discovery_due,
    personalize_selection,
    validate_selection,
    verify_proposal,
)
from stockrank.universe_review import render_review
from stockrank.universe_sources import collect_evidence


def load_policy(settings) -> DiscoveryPolicy:
    path = settings.root / "config/universe-discovery.local.json"
    return (
        DiscoveryPolicy(**json.loads(path.read_text(encoding="utf-8")))
        if path.exists()
        else DiscoveryPolicy()
    )


def directory(settings) -> Path:
    return settings.runtime_dir / "universe"


@contextmanager
def discovery_lock(settings):
    path = directory(settings) / "operation.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError(
            f"Another universe operation is active. If it crashed, verify it has stopped before removing {path}"
        ) from exc
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def schedule_state(settings) -> dict:
    path = directory(settings) / "schedule.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    policy = load_policy(settings)
    key = context_key(settings, policy)
    if key in state:
        return state
    # Approval changes the context, but is still part of the same weekly discovery.
    # Derive its inherited timestamp from immutable evidence and the atomic receipt.
    # This also repairs the due calculation for approvals made before this fix.
    for previous in list(state.values()):
        proposal_path = directory(settings) / Path(previous["proposal"]).name
        if not proposal_path.exists():
            continue
        proposal = read_proposal(proposal_path)
        receipt_path = directory(settings) / f"decision-{proposal['id']}.json"
        if proposal["preview"] or not receipt_path.exists():
            continue
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            receipt["decision"] != "approved"
            or receipt["version"] != settings.raw["universe"]["name"]
            or receipt["members"] != [asdict(s) for s in settings.universe]
        ):
            continue
        original_policy = DiscoveryPolicy(**proposal["policy"])
        original_settings = replace(
            settings, universe=tuple(Security(**s) for s in proposal["base_members"])
        )
        if (
            context_key(original_settings, original_policy) == proposal["context"]
            and replace(original_policy, keep_tickers=tuple(receipt["protected"])) == policy
        ):
            state[key] = dict(previous)
            break
    return state


def run_discovery(settings, policy, *, preview: bool, now=None) -> Path | None:
    now = now or datetime.now(UTC)
    with discovery_lock(settings):
        state = schedule_state(settings)
        key = context_key(settings, policy)
        if not preview:
            if not policy.enabled:
                print("Weekly universe discovery is disabled.")
                return None
            last = state.get(key, {}).get("last_success")
            if not discovery_due(last, now):
                print(f"Universe discovery not due; last successful discovery: {last}")
                return None
        evidence = collect_evidence(settings, policy, now=now)
        proposal = build_proposal(settings, policy, evidence, now=now, preview=preview)
        path = directory(settings) / f"proposal-{proposal['id'][:16]}.json"
        # Unique, immutable review content. Approval/rejection is stored separately.
        with path.open("x", encoding="utf-8") as handle:
            json.dump(proposal, handle, indent=2, allow_nan=False)
            handle.write("\n")
        path.with_suffix(".html").write_text(render_review(proposal), encoding="utf-8")
        print_proposal(proposal, path)
        if not preview and not proposal["profiles"][policy.profile]["blockers"]:
            state[key] = {"last_success": now.isoformat(), "proposal": path.name}
            atomic_json(directory(settings) / "schedule.json", state)
        elif not preview:
            print(
                "Discovery needs attention; weekly schedule was not advanced. Next morning will retry."
            )
        return path


def print_proposal(proposal, path):
    print(f"\nUniverse proposal: {path}")
    print(f"Readable review: {path.with_suffix('.html')}")
    for name, result in proposal["profiles"].items():
        print(
            f"  {PROFILE_LABELS[name]}: {len(result['members'])} stocks, "
            f"+{len(result['additions'])} / -{len(result['removals'])}, "
            f"{len(result['sector_counts'])} sectors"
        )
        for blocker in result["blockers"]:
            print(f"    BLOCKED: {blocker}")
    print(
        "Active universe unchanged. Review the HTML, then use universe-approve or universe-reject."
    )


def command_universe_configure(args):
    settings = load_settings()
    policy = load_policy(settings)
    changes = {
        name: getattr(args, name)
        for name in ("profile", "target_size", "per_sector", "enabled")
        if getattr(args, name, None) is not None
    }
    updated = replace(policy, **changes)
    with discovery_lock(settings):
        atomic_json(settings.root / "config/universe-discovery.local.json", asdict(updated))
    print(
        f"Universe discovery: {PROFILE_LABELS[updated.profile]}, size {updated.target_size}, "
        f"{updated.per_sector} candidates/sector, weekly {'enabled' if updated.enabled else 'disabled'}"
    )
    print(
        "This controls future proposals only; active membership and scoring settings are unchanged."
    )
    return 0


def command_universe_status(args):
    settings = load_settings()
    policy = load_policy(settings)
    state = schedule_state(settings).get(context_key(settings, policy), {})
    print(
        f"Universe profile: {PROFILE_LABELS[policy.profile]} | target={policy.target_size} | "
        f"candidates/sector={policy.per_sector} | weekly enabled={policy.enabled}"
    )
    print(f"Active: {settings.raw['universe']['name']} ({len(settings.universe)} stocks)")
    due = discovery_due(state.get("last_success"), datetime.now(UTC))
    print(f"Discovery due: {due} | last success: {state.get('last_success', 'never')}")
    for path in sorted(
        directory(settings).glob("proposal-*.json"), key=lambda p: p.stat().st_mtime
    )[-10:]:
        try:
            proposal = read_proposal(path)
        except ValueError as exc:
            print(f"  {path.name} | unavailable: {exc}")
            continue
        receipt = directory(settings) / f"decision-{proposal['id']}.json"
        status = (
            json.loads(receipt.read_text(encoding="utf-8"))["decision"]
            if receipt.exists()
            else "pending"
        )
        print(
            f"  {path.name} | {status} | {proposal['created_at']} | preview={proposal['preview']}"
        )
    return 0


def command_universe_preview(args):
    settings = load_settings()
    policy = load_policy(settings)
    try:
        path = run_discovery(settings, policy, preview=True)
    except Exception as exc:  # noqa: BLE001 - report provider failures without hiding their cause.
        print(
            f"Universe preview failed: {exc}. Active universe and schedule unchanged.",
            file=sys.stderr,
        )
        return 1
    if getattr(args, "open", False) and path:
        from stockrank.universe_review_server import serve_review

        serve_review(settings, path)
    return 1 if read_proposal(path)["profiles"][policy.profile]["blockers"] else 0


def read_proposal(path):
    proposal = json.loads(Path(path).read_text(encoding="utf-8"))
    verify_proposal(proposal)
    return proposal


def command_universe_review(args):
    from stockrank.universe_review_server import serve_review

    settings = load_settings()
    path = getattr(args, "file", None)
    if not path:
        paths = list(directory(settings).glob("proposal-*.json"))
        if not paths:
            raise ValueError("No proposals found. Run universe-preview first.")
        path = max(paths, key=lambda p: p.stat().st_mtime)
    serve_review(settings, path, open_browser=not getattr(args, "no_open", False))
    return 0


def approve_proposal(
    settings, proposal, profile, *, now=None, protect=None, skip=(), include=(), remove=()
):
    """Caller supplies explicit user consent. This function never runs from discovery."""
    now = now or datetime.now(UTC)
    verify_proposal(proposal)
    if proposal["policy_version"] != POLICY_VERSION:
        raise ValueError(
            "This proposal predates stock overrides. Generate a new preview before approval"
        )
    policy = load_policy(settings)
    if proposal["context"] != context_key(settings, policy):
        raise ValueError(
            "Active membership, discovery policy, or scoring changed; generate a new preview"
        )
    created = datetime.fromisoformat(proposal["created_at"])
    if not timedelta(0) <= now - created <= timedelta(days=2):
        raise ValueError(
            "Proposal is expired or future-dated (48-hour approval window); preview again"
        )
    selected = proposal["profiles"][profile]
    if selected["blockers"]:
        raise ValueError("Proposal cannot be activated: " + "; ".join(selected["blockers"]))
    # Recompute selection from stored evidence to reject accidental membership corruption.
    rebuilt = build_proposal(
        settings, policy, proposal["evidence"], now=created, preview=proposal["preview"]
    )
    if rebuilt["profiles"] != proposal["profiles"]:
        raise ValueError("Proposal membership does not match its evidence")
    selected = personalize_selection(
        proposal,
        profile,
        protect=selected.get("protected", []) if protect is None else protect,
        skip=skip,
        include=include,
        remove=remove,
    )
    validate_selection(selected)
    receipt_path = directory(settings) / f"decision-{proposal['id']}.json"
    if receipt_path.exists():
        raise ValueError("This proposal already has a decision")
    members = [Security(**r) for r in selected["members"]]
    version = (
        "discovery-"
        + digest({"proposal": proposal["id"], "profile": profile, "selection": selected})[:16]
    )
    receipt = {
        "decision": "approved",
        "proposal": proposal["id"],
        "profile": profile,
        "version": version,
        "decided_at": now.isoformat(),
        "members": selected["members"],
        "protected": selected["protected"],
        "skipped": selected["skipped"],
        "included": selected["included"],
        "override_warnings": selected["override_warnings"],
        "manual_removals": selected["manual_removals"],
    }
    # A user may leave the approval prompt open while changing their configuration.
    current = load_settings(settings.root)
    if context_key(current, load_policy(current)) != proposal["context"]:
        raise ValueError("Configuration changed during review; generate a new preview")
    activate_proposed_universe(
        settings.root,
        members,
        version=version,
        receipt_path=receipt_path,
        receipt=receipt,
        discovery_policy=asdict(replace(policy, keep_tickers=tuple(selected["protected"]))),
    )
    return version


def command_universe_approve(args):
    settings = load_settings()
    with discovery_lock(settings):
        proposal = read_proposal(args.file)
        result = proposal["profiles"][args.profile]
        print(f"Review: {PROFILE_LABELS[args.profile]} | {proposal['id']}")
        print("Exact proposed membership: " + ", ".join(r["ticker"] for r in result["members"]))
        print("Add: " + ", ".join(result["additions"]))
        print("Remove: " + ", ".join(r["ticker"] for r in result["removals"]))
        for warning in proposal["warnings"]:
            print("NOTE: " + warning)
        if not args.yes:
            if not sys.stdin.isatty():
                raise ValueError("Approval requires an interactive terminal or explicit --yes")
            if (
                input("Activate this reviewed membership for future reports? [y/N]: ")
                .strip()
                .lower()
                != "y"
            ):
                print("Not activated.")
                return 0
        version = approve_proposal(settings, proposal, args.profile)
    print(
        f"Activated {version}. Existing reports unchanged; run config-check before the next report."
    )
    return 0


def command_universe_reject(args):
    settings = load_settings()
    with discovery_lock(settings):
        proposal = read_proposal(args.file)
        path = directory(settings) / f"decision-{proposal['id']}.json"
        if path.exists():
            raise ValueError("This proposal already has a decision")
        atomic_json(
            path,
            {
                "decision": "rejected",
                "proposal": proposal["id"],
                "decided_at": datetime.now(UTC).isoformat(),
            },
        )
    print("Proposal rejected. Active membership and historical reports are unchanged.")
    return 0


def run_due_discovery():
    """An unavailable proposal must not prevent a report for the current universe."""
    try:
        settings = load_settings()
        policy = load_policy(settings)
        path = run_discovery(settings, policy, preview=False)
        if path and read_proposal(path)["profiles"][policy.profile]["blockers"]:
            print("Universe discovery is blocked. Continuing with the current stock list.")
            return None
        return path
    except Exception as exc:  # noqa: BLE001 - discovery must not invalidate the completed report.
        print(
            f"Universe discovery needs attention: {exc}. Active universe unchanged; "
            "next morning will retry. You can also run universe-preview now.",
            file=sys.stderr,
        )


def add_universe_parsers(subparsers):
    review = subparsers.add_parser(
        "universe-review", help="Review a saved proposal with approval controls; no data refresh"
    )
    review.add_argument("--file", help="Proposal JSON; default is the latest saved proposal")
    review.add_argument(
        "--no-open", action="store_true", help="Print review URL without opening a browser"
    )
    review.set_defaults(handler=command_universe_review)
    config = subparsers.add_parser(
        "universe-configure", help="Choose the universe proposal profile"
    )
    config.add_argument("--profile", choices=tuple(PROFILE_LABELS))
    config.add_argument("--size", dest="target_size", type=int)
    config.add_argument("--per-sector", type=int)
    enabled = config.add_mutually_exclusive_group()
    enabled.add_argument("--enable", dest="enabled", action="store_true", default=None)
    enabled.add_argument("--disable", dest="enabled", action="store_false")
    config.set_defaults(handler=command_universe_configure)
    status = subparsers.add_parser(
        "universe-status", help="Show universe profile, due state, and proposals"
    )
    status.set_defaults(handler=command_universe_status)
    preview = subparsers.add_parser(
        "universe-preview", help="Discover now; keep weekly schedule unchanged"
    )
    preview.add_argument("--open", action="store_true", help="Open the saved HTML review")
    preview.set_defaults(handler=command_universe_preview)
    approve = subparsers.add_parser(
        "universe-approve", help="Explicitly activate reviewed future membership"
    )
    approve.add_argument("--file", required=True)
    approve.add_argument("--profile", required=True, choices=tuple(PROFILE_LABELS))
    approve.add_argument(
        "--yes", action="store_true", help="Confirm reviewed membership without prompting"
    )
    approve.set_defaults(handler=command_universe_approve)
    reject = subparsers.add_parser(
        "universe-reject", help="Reject a proposal without changing membership"
    )
    reject.add_argument("--file", required=True)
    reject.set_defaults(handler=command_universe_reject)
