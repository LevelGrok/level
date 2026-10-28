"""HTTP: the files the site reads, and one POST anyone can call.

GET  /state.json  /reads.json  /tape.json  /events.json  /health
POST /read   json: { "mint": "<solana address>" }
             -> the desk reads that launch (chain + market + Grok on X) and returns the card.
                a card younger than ten minutes is returned as is; "force": true re-reads it.
                4 a minute per IP, 2 reads at once, the rest wait up to 30 s and then get a 429.
                nothing about the caller is stored: no address, no IP, no time.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import chain

log = logging.getLogger("level.server")

PUBLIC_FILES = {"state.json", "reads.json", "tape.json", "events.json"}
EMPTY = {"state.json": {"updated": 0}, "reads.json": [], "tape.json": {"launches": []}, "events.json": {"events": []}}
MAX_BODY = 4096


class Store:
    def __init__(self, data_dir: str, agent=None, read_per_min: int = 4):
        self.data_dir = data_dir
        self.agent = agent
        self.read_per_min = read_per_min
        self.rate: dict[str, list[float]] = {}
        self.lock = threading.Lock()
        self.slots = threading.Semaphore(2)

    def read_file(self, name: str):
        try:
            with open(os.path.join(self.data_dir, name)) as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return EMPTY.get(name)

    def allow(self, ip: str, bucket: str, per_min: int) -> bool:
        now = time.time()
        key = f"{bucket}:{ip}"
        with self.lock:
            hits = [t for t in self.rate.get(key, []) if now - t < 60]
            if len(hits) >= per_min:
                self.rate[key] = hits
                return False
            hits.append(now)
            self.rate[key] = hits
            if len(self.rate) > 5000:
                self.rate = {k: v for k, v in self.rate.items() if v and now - v[-1] < 60}
        return True

    def read(self, mint: str, force: bool) -> dict:
        if not chain.is_pubkey(mint):
            raise ValueError("that is not a Solana address")
        if not self.agent:
            raise RuntimeError("desk not running")
        if not self.slots.acquire(timeout=30):
            raise TimeoutError("the desk is busy, try again in a minute")
        try:
            return self.agent.read_now(mint, force=force)
        finally:
            self.slots.release()


def make_handler(store: Store, cors: str = "*"):
    class H(BaseHTTPRequestHandler):
        server_version = "level/0.1"

        def log_message(self, fmt, *args):  # quiet; never log who asked what
            pass

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", cors)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _json(self, code: int, doc):
            body = json.dumps(doc, separators=(",", ":")).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def _ip(self) -> str:
            return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            path = self.path.split("?")[0].strip("/")
            if path == "health":
                return self._json(200, {"ok": True, "ts": int(time.time())})
            if path in PUBLIC_FILES:
                return self._json(200, store.read_file(path))
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.split("?")[0].strip("/")
            if path != "read":
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n > MAX_BODY:
                return self._json(413, {"error": "too big"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json(400, {"error": "bad json"})
            if not store.allow(self._ip(), "read", store.read_per_min):
                return self._json(429, {"error": f"{store.read_per_min} reads a minute, try again shortly"})
            mint = str(body.get("mint") or "").strip()
            try:
                card = store.read(mint, bool(body.get("force")))
                return self._json(200, card)
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            except TimeoutError as e:
                return self._json(429, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                log.exception("read")
                return self._json(500, {"error": f"the desk tripped over: {e}"[:200]})

    return H


def serve(store: Store, host: str = "127.0.0.1", port: int = 8793, cors: str = "*") -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), make_handler(store, cors))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name="http", daemon=True).start()
    log.info("http on %s:%s", host, port)
    return srv
