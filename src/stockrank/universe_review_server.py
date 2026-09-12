"""Loopback-only, two-step user decisions for one immutable universe proposal."""

from __future__ import annotations

import json
import secrets
import subprocess
import threading
import time
import webbrowser
from dataclasses import asdict
from html import escape
from http.server import HTTPServer
from urllib.parse import parse_qs

from stockrank.config import load_settings
from stockrank.dashboard_data import load_dashboard
from stockrank.dashboard_server import make_handler
from stockrank.universe_commands import (
    approve_proposal,
    directory,
    discovery_lock,
    load_policy,
    read_proposal,
)
from stockrank.universe_discovery import (
    MAX_UNIVERSE_SIZE,
    PROFILE_LABELS,
    DiscoveryPolicy,
    atomic_json,
    personalize_selection,
    suggested_trim,
    validate_selection,
)
from stockrank.universe_jobs import ReviewJob, verify_active, verify_report
from stockrank.universe_review import render_review


def make_review_server(settings, proposal_path, *, job_factory=ReviewJob, report_force=False):
    proposal = read_proposal(proposal_path)
    token = secrets.token_urlsafe(32)
    confirmations = {}
    job = job_factory(settings)
    dashboard_run = None
    handoff_lock = threading.Lock()

    class ReviewServer(HTTPServer):
        dashboard_open = False
        dashboard_ready = False
        last_notice = None
        dashboard_handoff = None

        def claim_dashboard(self, owner):
            with handoff_lock:
                if self.dashboard_handoff is None:
                    self.dashboard_handoff = owner
                return self.dashboard_handoff == owner

        def get_request(self):
            connection, address = super().get_request()
            # Browsers may preconnect without sending a request. Keep the worker
            # completion check responsive even when one such connection is idle.
            connection.settimeout(1)
            return connection, address

        def advance_job(self):
            nonlocal proposal, proposal_path, dashboard_run
            try:
                result = job.poll()
            except (ValueError, OSError) as exc:
                result = {"status": "error", "message": f"Could not read refresh result: {exc}"}
            if result["status"] == "complete":
                if job.kind == "nominate":
                    if str(proposal_path) != str(result["proposal_path"]):
                        proposal_path = result["proposal_path"]
                        proposal = read_proposal(proposal_path)
                        confirmations.clear()
                elif dashboard_run != result["run_id"]:
                    try:
                        receipt = report_receipt()
                        verify_active(load_settings(settings.root), receipt)
                        current_run = verify_report(load_dashboard(settings.root), receipt)
                        if current_run != result["run_id"]:
                            raise ValueError("The report changed. Retry the report build.")
                        dashboard_run = current_run
                        self.dashboard_ready = True
                    except (ValueError, OSError, KeyError) as exc:
                        result = {"status": "error", "message": str(exc)}
            notice = (result["status"], result.get("message"))
            if result["status"] == "error" and notice != self.last_notice:
                print(
                    f"\nRefresh needs attention: {result['message']}\n"
                    f"Review details and retry: http://127.0.0.1:{self.server_port}/progress",
                    flush=True,
                )
            self.last_notice = notice
            return result

        def service_actions(self):
            # Completion must not depend on an open/foreground browser progress page.
            self.advance_job()

        def server_close(self):
            job.close()
            super().server_close()

    def receipt_path():
        return directory(settings) / f"decision-{proposal['id']}.json"

    def report_receipt():
        receipt = json.loads(receipt_path().read_text(encoding="utf-8"))
        if receipt["decision"] == "rejected":
            # Older rejection receipts predate the report handoff.
            receipt.setdefault("version", proposal["base_universe"])
            receipt.setdefault("members", proposal["base_members"])
        return receipt

    def start_report():
        receipt = report_receipt()
        if receipt["decision"] not in {"approved", "rejected"}:
            raise ValueError("Confirm a decision before building the dashboard")
        verify_active(load_settings(settings.root), receipt)
        job.start("report", {**receipt, "force": report_force})

    class Handler(make_handler(settings.root)):
        def log_message(self, format, *args):
            return

        def valid_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def reply(self, status, html):
            content = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            nonlocal proposal, proposal_path, dashboard_run
            if not self.valid_host():
                return self.reply(403, "Invalid host")
            if self.path == "/progress":
                result = self.server.advance_job()
                if result["status"] == "complete":
                    if job.kind == "nominate":
                        return self.redirect("/")
                    if self.server.claim_dashboard("review"):
                        return self.redirect("/dashboard")
                    return self.reply(
                        200,
                        "<h1>Your dashboard is ready</h1>"
                        "<p>The dashboard has opened in another tab.</p>"
                        '<p><a href="/dashboard">Open dashboard here</a></p>',
                    )
                running = result["status"] == "running"
                log_path = getattr(job, "log_path", None)
                progress = (
                    log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
                    if log_path and log_path.exists()
                    else "Starting the data refresh…"
                )
                heading = (
                    "Decision saved. Building your dashboard"
                    if job.kind == "report"
                    else "Screening your candidate list"
                )
                retry = (
                    '<form method="post" action="/report">'
                    f'<input type="hidden" name="token" value="{token}"><button>Retry report build</button></form>'
                    if not running and job.kind == "report"
                    else ""
                )
                return self.reply(
                    200,
                    f"""<!doctype html><html lang="en"><meta charset="utf-8">
{'<meta http-equiv="refresh" content="2">' if running else ""}<title>Universe update progress</title>
<body style="font:18px/1.6 system-ui;max-width:850px;margin:50px auto;padding:20px">
<h1>{heading if running else "Refresh needs attention"}</h1>
<p>{"This page will open the result when it is ready. Keep the terminal open." if running else escape(result.get("message", "No refresh is running."))}</p>
<p>{"The new report contains deterministic analysis. AI research remains a separate step." if job.kind == "report" else "Candidate preferences are saved for future searches. Active membership stays unchanged."}</p>
{retry}<h2>Live processing progress</h2><p>The same updates also appear in your terminal.</p>
<pre style="white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f1;padding:18px;max-height:420px;overflow:auto">{escape(progress)}</pre>
<a href="/job-log" target="_blank">View progress details</a> · <a href="/">Return to review</a></body></html>""",
                )
            if self.path == "/job-log":
                path = getattr(job, "log_path", None)
                log = (
                    path.read_text(encoding="utf-8", errors="replace")[-16000:]
                    if path and path.exists()
                    else "No log yet."
                )
                return self.reply(200, "<h1>Refresh progress</h1><pre>" + escape(log) + "</pre>")
            if (
                self.path == "/dashboard"
                or self.path.startswith("/api/")
                or self.path
                in {"/app.js", "/app.css", "/app.js.LEGAL.txt", "/THIRD_PARTY_NOTICES.txt"}
            ):
                try:
                    receipt = report_receipt()
                    verify_active(load_settings(settings.root), receipt)
                    current_run = verify_report(load_dashboard(settings.root), receipt)
                    if dashboard_run is None or current_run != dashboard_run:
                        raise ValueError(
                            "The report changed. Build the updated dashboard again from the review."
                        )
                except (ValueError, OSError, KeyError) as exc:
                    return self.reply(
                        409,
                        "<h1>Dashboard needs a refresh</h1><p>"
                        + escape(str(exc))
                        + '</p><a href="/">Return to review</a>',
                    )
                if self.path == "/dashboard":
                    self.server.claim_dashboard("review")
                    self.server.dashboard_open = True
                    self.path = "/"
                return super().do_GET()
            if self.path != "/":
                return self.reply(404, "Not found")
            confirmations.clear()
            decision = None
            receipt = None
            if receipt_path().exists():
                receipt = report_receipt()
                decision = receipt["decision"] + (
                    " — " + receipt["profile"] if "profile" in receipt else ""
                )
            return self.reply(
                200,
                render_review(
                    proposal,
                    action_token=token,
                    decision=decision,
                    considered=load_policy(load_settings(settings.root)).consider_tickers,
                    receipt=receipt,
                ),
            )

        def redirect(self, location):
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if not self.valid_host() or self.headers.get("Origin") != origin:
                return self.reply(403, "Invalid origin")
            if self.path not in {"/confirm", "/decision", "/nominate", "/report"}:
                return self.reply(404, "Not found")
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if (
                    not 0 < length <= 32768
                    or self.headers.get("Content-Type") != "application/x-www-form-urlencoded"
                ):
                    raise ValueError("Invalid request")
                values = parse_qs(
                    self.rfile.read(length).decode("utf-8"),
                    strict_parsing=True,
                    keep_blank_values=True,
                )
                if any(
                    len(v) != 1
                    for k, v in values.items()
                    if k not in {"protect", "include", "remove", "skip"}
                ):
                    raise ValueError("Duplicate fields")
                fields = {k: v[0] for k, v in values.items()}
                if not secrets.compare_digest(fields.get("token", ""), token):
                    return self.reply(403, "Invalid review token")
                if job.poll()["status"] == "running":
                    raise ValueError("A refresh is already running. Open /progress to follow it.")
                if self.path == "/nominate":
                    import re

                    tickers = tuple(
                        t for t in re.split(r"[\s,]+", fields.get("tickers", "").strip()) if t
                    )
                    validated = DiscoveryPolicy(consider_tickers=tickers)
                    confirmations.clear()
                    job.start("nominate", {"tickers": list(validated.consider_tickers)})
                    return self.redirect("/progress")
                if self.path == "/report":
                    start_report()
                    return self.redirect("/progress")
                if self.path == "/confirm":
                    action, profile = fields.get("action"), fields.get("profile")
                    if action not in {"approve", "reject"} or profile not in PROFILE_LABELS:
                        raise ValueError("Invalid decision")
                    if receipt_path().exists():
                        raise ValueError(
                            "This proposal already has a decision. Return to the review."
                        )
                    selected = proposal["profiles"][profile]
                    if action == "approve" and selected["blockers"]:
                        raise ValueError("This profile has blockers and cannot be approved")
                    edits = {
                        key: values.get(key, []) for key in ("protect", "skip", "include", "remove")
                    }
                    if action == "approve":
                        selected = personalize_selection(proposal, profile, **edits)
                        if fields.get("trim") == "suggest":
                            edits["remove"] = sorted(
                                set(edits["remove"])
                                | set(suggested_trim(proposal, profile, selected))
                            )
                            selected = personalize_selection(proposal, profile, **edits)
                        if len(selected["members"]) > MAX_UNIVERSE_SIZE:
                            hidden = "".join(
                                f'<input type="hidden" name="{key}" value="{escape(value, quote=True)}">'
                                for key in edits
                                for value in edits[key]
                            )
                            choices = "".join(
                                f'<p><label><input type="checkbox" name="remove" value="{escape(m["ticker"], quote=True)}"> Remove {escape(m["ticker"])} · {escape(m["sector"])}</label></p>'
                                for m in selected["members"]
                                if m["ticker"] not in selected["protected"]
                            )
                            return self.reply(
                                200,
                                f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Your list is full</title>
<body style="font:18px/1.6 system-ui;max-width:850px;margin:40px auto;padding:20px"><h1>Your list is full</h1>
<p>You selected {len(selected["members"])} stocks. The hard cap is {MAX_UNIVERSE_SIZE}. Remove at least {len(selected["members"]) - MAX_UNIVERSE_SIZE} before approval.</p>
<p>Choose stocks to remove below, or request a suggested trim. Suggestions preserve protected stocks, prefer lower scores, and account for sector counts in Diversified. You will review the result before confirming.</p>
<form method="post" action="/confirm"><input type="hidden" name="token" value="{token}"><input type="hidden" name="action" value="approve">
<input type="hidden" name="profile" value="{profile}">{hidden}<button name="trim" value="suggest">Suggest a trim to 100</button>
<h2>Make removals manually</h2><p>Protected stocks are excluded here. To remove one, return to the review and uncheck its keep option first.</p>
{choices}<button>Review my revised list</button></form><a href="/">Return to review</a></body></html>''',
                            )
                        validate_selection(selected)
                    confirmations.clear()
                    nonce = secrets.token_urlsafe(32)
                    confirmations[nonce] = (action, profile, time.monotonic(), edits)
                    changes = (
                        f"<h2>Exact future membership</h2><p>{len(selected['members'])} / {MAX_UNIVERSE_SIZE} stocks</p><p>"
                        + escape(", ".join(m["ticker"] for m in selected["members"]))
                        + "</p>"
                        "<h2>Add</h2><p>"
                        + escape(", ".join(selected["additions"]) or "None")
                        + "</p>"
                        "<h2>Remove</h2><p>"
                        + escape(", ".join(r["ticker"] for r in selected["removals"]) or "None")
                        + "</p>"
                        if action == "approve"
                        else "<p>Reject both alternatives in this proposal. Active membership stays unchanged.</p>"
                    )
                    warnings = "".join(
                        "<li>" + escape(w) + "</li>"
                        for w in proposal["warnings"] + selected.get("override_warnings", [])
                    )
                    return self.reply(
                        200,
                        f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Confirm universe decision</title>
<body style="font:18px/1.6 system-ui;max-width:900px;margin:40px auto;padding:20px">
<h1>{"Review your final list — " + PROFILE_LABELS[profile] if action == "approve" else "Confirm rejection"}</h1>
{changes}<ul>{warnings}</ul><form method="post" action="/decision">
<input type="hidden" name="token" value="{token}"><input type="hidden" name="confirmation" value="{nonce}">
<button type="submit" style="font:inherit;padding:12px">{"Confirm &amp; build updated dashboard" if action == "approve" else "Confirm rejection &amp; build dashboard"}</button>
 <a href="/">Cancel and return to review</a></form></body></html>''',
                    )
                confirmation = confirmations.pop(fields.get("confirmation", ""), None)
                if not confirmation or time.monotonic() - confirmation[2] > 600:
                    raise ValueError("Confirmation expired or already used. Return to review.")
                action, profile, _, edits = confirmation
                with discovery_lock(settings):
                    # Re-read the exact reviewed artifact before any write.
                    current = read_proposal(proposal_path)
                    if current["id"] != proposal["id"]:
                        raise ValueError("Proposal changed; reopen the review")
                    if receipt_path().exists():
                        raise ValueError("This proposal already has a decision")
                    if action == "approve":
                        approve_proposal(load_settings(settings.root), current, profile, **edits)
                    else:
                        from datetime import UTC, datetime

                        retained = load_settings(settings.root)
                        atomic_json(
                            receipt_path(),
                            {
                                "decision": "rejected",
                                "version": retained.raw["universe"]["name"],
                                "members": [asdict(s) for s in retained.universe],
                                "proposal": current["id"],
                                "decided_at": datetime.now(UTC).isoformat(),
                            },
                        )
                start_report()
                return self.redirect("/progress")
            except (ValueError, OSError, KeyError, RuntimeError) as exc:
                approved = False
                if receipt_path().exists():
                    approved = json.loads(receipt_path().read_text(encoding="utf-8")).get(
                        "decision"
                    ) in {"approved", "rejected"}
                self.reply(
                    409,
                    (
                        "<h1>Decision saved; next step not completed</h1><p>"
                        if approved
                        else "<h1>Decision not completed</h1><p>"
                    )
                    + escape(str(exc))
                    + '</p><a href="/">Return to review</a>',
                )

    return ReviewServer(("127.0.0.1", 0), Handler)


def serve_review(settings, proposal_path, *, open_browser=True, report_force=False):
    from stockrank.daily_workflow import print_goodbye, wait_with_welcome

    with make_review_server(settings, proposal_path, report_force=report_force) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Universe review: {url}", flush=True)
        print(
            "Keep this terminal open while reviewing. Ctrl+C stops the review server.", flush=True
        )
        if open_browser:
            webbrowser.open(url)
        stopped = threading.Event()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        class DashboardLifetime:
            def poll(self):
                return 0 if stopped.is_set() else None

            def wait(self, timeout=None):
                if stopped.wait(timeout):
                    return 0
                raise subprocess.TimeoutExpired("universe dashboard", timeout)

        try:
            while not server.dashboard_ready:
                stopped.wait(0.2)
            dashboard_url = url + "dashboard"
            print(f"\nUpdated dashboard ready: {dashboard_url}", flush=True)
            # Give the progress page one refresh cycle to reuse its existing tab.
            # Claim the fallback atomically so a late refresh cannot also redirect.
            deadline = time.monotonic() + 3
            while (
                open_browser
                and not server.dashboard_open
                and server.dashboard_handoff is None
                and time.monotonic() < deadline
            ):
                stopped.wait(0.1)
            if open_browser and not server.dashboard_open and server.claim_dashboard("launcher"):
                webbrowser.open(dashboard_url)
            print("\nUpdated dashboard is running. Ctrl+C closes it.", flush=True)
            wait_with_welcome(DashboardLifetime())
        except KeyboardInterrupt:
            pass
        finally:
            stopped.set()
            server.shutdown()
            thread.join(timeout=5)

    # Release the server and report worker before the farewell can close the launcher.
    print_goodbye()
