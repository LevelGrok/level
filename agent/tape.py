"""The launch tape: every new pump.fun token, as it is created, from PumpPortal's free data stream.

wss://pumpportal.fun/api/data, method subscribeNewToken. No key. One message per launch with the mint,
the creator wallet, the name and the first buy. The desk keeps a ring of recent launches and counts
them per minute and per hour; that count is the first number on the site.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque

log = logging.getLogger("level.tape")

WS_URL = "wss://pumpportal.fun/api/data"


class Tape:
    def __init__(self, keep: int = 4000):
        self.recent: deque[dict] = deque(maxlen=keep)
        self.times: deque[float] = deque(maxlen=50_000)
        self.lock = threading.Lock()
        self.ok = False
        self.last_error = ""
        self.connected_at = 0.0
        self.total = 0
        self._stop = threading.Event()
        self._ws = None

    # ---- counts
    def per_minute(self) -> int:
        cut = time.time() - 60
        with self.lock:
            return sum(1 for t in self.times if t >= cut)

    def per_hour(self) -> int:
        cut = time.time() - 3600
        with self.lock:
            return sum(1 for t in self.times if t >= cut)

    def latest(self, n: int = 40) -> list[dict]:
        with self.lock:
            return list(self.recent)[-n:][::-1]

    def between(self, min_age_s: float, max_age_s: float) -> list[dict]:
        now = time.time()
        with self.lock:
            return [l for l in self.recent if min_age_s <= now - l["ts"] <= max_age_s]

    # ---- the stream
    def push(self, msg: dict):
        if msg.get("txType") != "create" or not msg.get("mint"):
            return
        now = time.time()
        launch = {
            "ts": int(now), "mint": msg["mint"], "name": (msg.get("name") or "")[:48], "symbol": (msg.get("symbol") or "")[:16],
            "creator": msg.get("traderPublicKey"), "sol_in": round(float(msg.get("solAmount") or 0), 3),
            "mc_sol": round(float(msg.get("marketCapSol") or 0), 2), "uri": msg.get("uri"), "pool": msg.get("pool"),
        }
        with self.lock:
            self.recent.append(launch)
            self.times.append(now)
            self.total += 1

    def start(self):
        t = threading.Thread(target=self._run, name="tape", daemon=True)
        t.start()
        return t

    def stop(self):
        self._stop.set()
        try:
            if self._ws:
                self._ws.close()
        except Exception:  # noqa: BLE001
            pass

    def _run(self):
        try:
            import websocket  # websocket-client
        except ImportError:
            self.last_error = "websocket-client not installed"
            log.error(self.last_error)
            return
        backoff = 2
        while not self._stop.is_set():
            def on_open(ws):
                self.ok = True
                self.connected_at = time.time()
                self.last_error = ""
                backoff_reset()
                ws.send(json.dumps({"method": "subscribeNewToken"}))
                log.info("tape: connected")

            def on_message(ws, data):
                try:
                    self.push(json.loads(data))
                except ValueError:
                    pass

            def on_error(ws, err):
                self.last_error = str(err)[:120]

            def on_close(ws, code, msg):
                self.ok = False

            def backoff_reset():
                nonlocal backoff
                backoff = 2

            self._ws = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
            try:
                self._ws.run_forever(ping_interval=25, ping_timeout=10)
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)[:120]
            self.ok = False
            if self._stop.is_set():
                break
            log.warning("tape: disconnected (%s), retry in %ss", self.last_error or "closed", backoff)
            time.sleep(backoff)
            backoff = min(60, backoff * 2)
