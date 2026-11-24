"""Loopback-only, read-only HTTP server for the embedded viewers.

Chromium (Qt WebEngine) blocks ES modules and fetch() from file:// pages, so the 2D/3D
viewers are served from 127.0.0.1. Only two roots are exposed: the packaged viewer
assets (/app/...) and the currently open revision folder (/rev/<token>/...). The token
changes per revision, paths are resolved with traversal protection, and only GET is served.
"""
from __future__ import annotations

import http.server
import mimetypes
import secrets
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..security.paths import UnsafePath, safe_join

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("image/svg+xml", ".svg")


class ViewerServer:
    def __init__(self, app_root: Path):
        self.app_root = app_root
        self.rev_root: Path | None = None
        self.token = secrets.token_hex(8)
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                path = unquote(urlparse(self.path).path)
                try:
                    if path.startswith("/app/"):
                        f = safe_join(server.app_root, path[5:])
                    elif path.startswith(f"/rev/{server.token}/") and server.rev_root:
                        f = safe_join(server.rev_root, path[len(f"/rev/{server.token}/"):])
                    else:
                        raise UnsafePath(path)
                except UnsafePath:
                    self.send_error(404)
                    return
                if not f.is_file():
                    self.send_error(404)
                    return
                data = f.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.guess_type(f.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def set_revision(self, rev_dir: Path | None):
        self.rev_root = rev_dir
        self.token = secrets.token_hex(8)

    def app_url(self, rel: str) -> str:
        return f"http://127.0.0.1:{self.port}/app/{rel}"

    def rev_url(self, rel: str) -> str:
        return f"http://127.0.0.1:{self.port}/rev/{self.token}/{rel}"

    def stop(self):
        self.httpd.shutdown()
