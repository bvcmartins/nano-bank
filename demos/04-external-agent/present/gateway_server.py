#!/usr/bin/env python3
"""Serve the animated gateway cinematic AND capture new runs from the UI.

    demos/04-external-agent/present/gateway_server.py            # http://localhost:8521/gateway.html
    demos/04-external-agent/present/gateway_server.py 8531

Endpoints:
    POST /api/capture           -> starts a capture (one external-agent run)
    GET  /api/capture/status    -> {running, phase, ok, message}

Drives present/capture.py against $DEMO_BRANCH_BASE (default
http://localhost:8086 — port-forward svc/agent-api first).
"""
from __future__ import annotations
import contextlib
import json
import os
import socket
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
import state  # noqa: E402

CAPTURE_PY = os.path.join(HERE, "capture.py")

_LOCK = threading.Lock()
CAP = {"running": False, "phase": "idle", "ok": None, "message": ""}


def _set(**kw):
    with _LOCK:
        CAP.update(kw)


def _rebuild():
    subprocess.run([sys.executable, os.path.join(HERE, "build_gateway.py")],
                   cwd=REPO, check=False)


def _capture_worker():
    _set(running=True, phase="capturing", ok=None, message="Running the external agent…")
    jsonl = os.path.join("/tmp", "gateway-run.jsonl")
    open(jsonl, "w").close()
    env = dict(os.environ,
               XDG_RUNTIME_DIR=os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"),
               XDG_DATA_HOME=os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")))
    proc = subprocess.run([sys.executable, CAPTURE_PY, "--emit-jsonl", jsonl],
                          cwd=REPO, env=env, capture_output=True, text=True)
    if proc.returncode == 0:
        events = state.read_jsonl(open(jsonl).read())
        d = os.path.join(HERE, "recordings")
        os.makedirs(d, exist_ok=True)
        p = state.save_recording(d, events)
        import shutil
        shutil.copy(p, os.path.join(d, "canonical.json"))
        _rebuild()
        _set(phase="done", ok=True, message=f"Captured {len(events)} events — reloading.")
    else:
        msg = (proc.stderr or proc.stdout or "capture.py failed").strip().splitlines()[-1:]
        _set(phase="failed", ok=False,
             message=f"Capture failed — kept the previous recording. {msg[0] if msg else ''}")
    _set(running=False)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=HERE, **kw)

    def log_message(self, *a):  # quiet
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/capture/status"):
            with _LOCK:
                return self._json(200, dict(CAP))
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/capture"):
            with _LOCK:
                if CAP["running"]:
                    return self._json(409, {"error": "a capture is already running"})
            threading.Thread(target=_capture_worker, daemon=True).start()
            return self._json(202, {"started": True})
        self._json(404, {"error": "not found"})


class DualStackServer(ThreadingHTTPServer):
    # http.server's own idiom (see http.server.test()'s DualStackServer) --
    # HTTPServer defaults to AF_INET (IPv4-only), which refuses connections
    # to "localhost" on setups where that resolves to ::1 first (the Linux
    # default). Binding AF_INET6 to "::" and clearing IPV6_V6ONLY accepts
    # both IPv6 and IPv4 clients on one socket.
    address_family = socket.AF_INET6

    def server_bind(self):
        with contextlib.suppress(Exception):
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        return super().server_bind()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8521
    _rebuild()
    httpd = DualStackServer(("::", port), Handler)
    print(f"▶ gateway cinematic: http://localhost:{port}/gateway.html")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
