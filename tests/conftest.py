"""Testhilfen, u. a. ein kleiner WebDAV-Server, der sich wie Nextcloud verhält."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

import pytest

PREFIX = "/remote.php/dav/files/anna"
USER, PASSWORD = "anna", "geheim"


class FakeDav:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {""}
        self.fail_puts = 0  # so viele PUTs mit 503 beantworten


def make_handler(dav: FakeDav):
    import base64

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _path(self):
            p = unquote(urlsplit(self.path).path)
            if not p.startswith(PREFIX):
                return None
            return p[len(PREFIX):].strip("/")

        def _auth_ok(self):
            want = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
            if self.headers.get("Authorization") != want:
                self._reply(401)
                return False
            return True

        def _reply(self, code, body=b""):
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def do_PROPFIND(self):
            self._body()
            if not self._auth_ok():
                return
            p = self._path()
            if p is None or (p not in dav.dirs and p not in dav.files):
                return self._reply(404)
            entries = [p]
            if self.headers.get("Depth") == "1" and p in dav.dirs:
                pre = p + "/" if p else ""
                entries += [d for d in dav.dirs if d.startswith(pre) and d != p
                            and "/" not in d[len(pre):]]
                entries += [f for f in dav.files if f.startswith(pre)
                            and "/" not in f[len(pre):]]
            parts = []
            for e in entries:
                from urllib.parse import quote
                href = quote(PREFIX + "/" + e + ("/" if e in dav.dirs else ""))
                rt = "<d:collection/>" if e in dav.dirs else ""
                parts.append(f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>"
                             f"<d:resourcetype>{rt}</d:resourcetype></d:prop></d:propstat>"
                             f"</d:response>")
            xml = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">'
                   + "".join(parts) + "</d:multistatus>")
            self._reply(207, xml.encode())

        def do_MKCOL(self):
            if not self._auth_ok():
                return
            p = self._path()
            if p in dav.dirs:
                return self._reply(405)
            if p.rsplit("/", 1)[0] not in dav.dirs and "/" in p:
                return self._reply(409)
            dav.dirs.add(p)
            self._reply(201)

        def do_PUT(self):
            data = self._body()
            if not self._auth_ok():
                return
            if dav.fail_puts:
                dav.fail_puts -= 1
                return self._reply(503)
            p = self._path()
            parent = p.rsplit("/", 1)[0] if "/" in p else ""
            if parent not in dav.dirs:
                return self._reply(409)
            dav.files[p] = data
            self._reply(201)

    return Handler


@pytest.fixture
def dav_server():
    dav = FakeDav()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(dav))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    dav.base = f"http://127.0.0.1:{server.server_address[1]}"
    yield dav
    server.shutdown()
