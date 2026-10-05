"""Serve the Phase 2 buyer planner on this computer only."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def load_planner(project_root: Path):
    path = project_root / "scripts/phase_2_buyer_planner" / "11_buyer_cost_planner.py"
    spec = importlib.util.spec_from_file_location("buyer_cost_planner_12", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load buyer calculator at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_seller(project_root: Path):
    path = project_root / "scripts/phase_3_seller_planner" / "13_seller_proceeds_planner.py"
    spec = importlib.util.spec_from_file_location("seller_proceeds_planner_14", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load seller calculator at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuyerFormHandler(BaseHTTPRequestHandler):
    server: ThreadingHTTPServer

    def log_message(self, format_string: str, *args) -> None:
        # Requests may contain financial assumptions; do not log them.
        pass

    def send_content(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def allowed_host(self) -> bool:
        host = self.headers.get("Host", "")
        return host in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def do_GET(self) -> None:
        if not self.allowed_host():
            self.send_error(HTTPStatus.BAD_REQUEST, "Local host required")
            return
        path = urlsplit(self.path).path
        files = {
            "/": ("12_buyer_form.html", "text/html; charset=utf-8"),
            "/app.css": ("12_buyer_form.css", "text/css; charset=utf-8"),
            "/app.js": ("12_buyer_form.js", "text/javascript; charset=utf-8"),
            "/validation.js": ("12_buyer_validation.js", "text/javascript; charset=utf-8"),
            "/seller": ("14_seller_form.html", "text/html; charset=utf-8"),
            "/seller.css": ("14_seller_form.css", "text/css; charset=utf-8"),
            "/seller.js": ("14_seller_form.js", "text/javascript; charset=utf-8"),
        }
        if path == "/notes":
            source = self.server.project_root / "data" / "reference" / "11_policy_notes.md"
            kind = "text/plain; charset=utf-8"
        elif path == "/seller-notes":
            source = self.server.project_root / "data" / "reference" / "13_seller_policy_notes.md"
            kind = "text/plain; charset=utf-8"
        elif path in files:
            filename, kind = files[path]
            source = self.server.project_root / "web" / filename
        else:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            self.send_content(source.read_bytes(), kind)
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if not self.allowed_host() or path not in {"/calculate", "/seller/calculate"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}:
            self.send_content(b'{"error":"This form can only be used locally."}', "application/json; charset=utf-8", HTTPStatus.FORBIDDEN)
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self.send_content(b'{"error":"Expected JSON form data."}', "application/json; charset=utf-8", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                raise ValueError("Form submission is too large or empty")
            payload = json.loads(self.rfile.read(length))
            calculator = self.server.planner if path == "/calculate" else self.server.seller
            result = calculator.build(payload)
            body = json.dumps(result, allow_nan=False).encode("utf-8")
            self.send_content(body, "application/json; charset=utf-8")
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            body = json.dumps({"error": str(error)}).encode("utf-8")
            self.send_content(body, "application/json; charset=utf-8", HTTPStatus.BAD_REQUEST)


def make_server(project_root: Path, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), BuyerFormHandler)
    server.project_root = project_root
    server.planner = load_planner(project_root)
    server.seller = load_seller(project_root)
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be from 0 to 65535")
    try:
        server = make_server(args.project_root.resolve(), args.port)
    except OSError as error:
        print(f"Could not start local buyer form: {error}", file=sys.stderr)
        return 1
    address = f"http://127.0.0.1:{server.server_port}/"
    print(f"Buyer form: {address}", flush=True)
    print("This page runs on your computer only. Keep this terminal open; press Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
