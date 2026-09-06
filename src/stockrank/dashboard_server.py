"""Loopback-only, read-only HTTP server for the prebuilt React dashboard."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from stockrank.dashboard_data import load_dashboard
from stockrank.presentation import rankings_csv

ASSETS = Path(__file__).with_name("web_assets")


def make_handler(root: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(
            self, status, body: bytes, content_type="text/plain; charset=utf-8", download=False
        ):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; object-src 'none'",
            )
            if download:
                self.send_header("Content-Disposition", 'attachment; filename="rankings.csv"')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            port = self.server.server_port
            allowed = {f"localhost:{port}", f"127.0.0.1:{port}"}
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            if host not in allowed or (origin and origin not in {f"http://{h}" for h in allowed}):
                return self.send(403, b"Local dashboard access only.")
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                return self.send(403, b"Cross-site access denied.")
            url = urlsplit(self.path)
            if url.path in ("/api/report", "/api/rankings.csv"):
                try:
                    data = load_dashboard(root)
                    if url.path.endswith(".csv"):
                        expected = parse_qs(url.query).get("run", [None])[0]
                        if not data["run"] or expected != data["run"]["run_id"]:
                            return self.send(
                                409, b"The report changed. Reload the dashboard before downloading."
                            )
                        return self.send(
                            200,
                            rankings_csv(data["results"]),
                            "text/csv; charset=utf-8",
                            True,
                        )
                    return self.send(
                        200,
                        json.dumps(data, allow_nan=False).encode(),
                        "application/json; charset=utf-8",
                    )
                except Exception as error:  # noqa: BLE001 - HTTP boundary must not expose internals
                    print(
                        f"Dashboard read failed: {type(error).__name__}: {error}", file=sys.stderr
                    )
                    return self.send(
                        500,
                        b'{"error":"Stored report could not be read. Check the terminal and run setup-check; no data was changed."}',
                        "application/json",
                    )
            name = "index.html" if url.path == "/" else url.path.lstrip("/")
            if name not in {
                "index.html",
                "app.js",
                "app.css",
                "app.js.LEGAL.txt",
                "THIRD_PARTY_NOTICES.txt",
            }:
                return self.send(404, b"Not found")
            asset = ASSETS / name
            if not asset.is_file():
                return self.send(
                    503,
                    b"Dashboard assets are missing. Rebuild the frontend or use --ui streamlit.",
                )
            return self.send(
                200, asset.read_bytes(), mimetypes.guess_type(name)[0] or "application/octet-stream"
            )

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if not (ASSETS / "index.html").exists():
        parser.error("React assets missing; run the frontend build or use --ui streamlit")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.root))
    print(f"React dashboard ready at http://localhost:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
