"""Local development server: serves public/ and the same /api/check the Vercel function uses.

    python server.py            ->  http://localhost:8000
"""
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from factora.http_api import handle_check, health

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")


class Dev(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/check":
            return self._json(200, health())
        rel = "index.html" if path == "/" else path.lstrip("/")
        full = os.path.normpath(os.path.join(ROOT, rel))
        if not full.startswith(ROOT) or not os.path.isfile(full):
            self.send_error(404)
            return
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(full)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path.split("?")[0] != "/api/check":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        status, payload = handle_check(self.rfile.read(min(length, 20_001)))
        self._json(status, payload)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"Factora running at http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Dev).serve_forever()
