"""Phase 0 stub for the app: validates signal tokens and serves the harness."""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

TOKENS = set(filter(None, os.environ.get("SPIKE_TOKENS", "").split(",")))
HARNESS = Path(__file__).parent / "harness"
SEEN = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/internal/signal-auth":
            original = self.headers.get("X-Original-URI", "")
            token = parse_qs(urlsplit(original).query).get("t", [""])[0]
            SEEN.append(token)
            self.send_response(204 if token in TOKENS else 403)
            self.end_headers()
            return
        if path == "/spike/seen-tokens":
            body = ",".join(SEEN).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/spike/"):
            f = HARNESS / path.removeprefix("/spike/")
            if f.is_file():
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(f.read_bytes())
                return
        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        print("authstub:", fmt % args)


ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
