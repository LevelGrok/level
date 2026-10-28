"""The loop: tape in, desk reads out, files for the site every few seconds."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

from . import chain
from .desk import Budget, Desk
from .grok import Grok
from .tape import Tape

log = logging.getLogger("level")


@dataclass
class Config:
    data_dir: str = "./data"
    rpc_url: str = "https://api.mainnet-beta.solana.com"
    auto_reads: bool = True
    auto_mc_usd: float = 25_000       # a launch the desk reads on its own has to be at least this big
    auto_min_age_min: int = 3          # and at least this old, so the pair exists
    auto_max_age_min: int = 180        # and at most this old, so the desk reads launches, not history
    auto_per_hour: int = 20
    daily_budget_usd: float = 5.0
    x_days: int = 7
    max_turns: int = 4
    poll_s: int = 15
    links: dict = field(default_factory=dict)
    server: dict = field(default_factory=dict)


class Agent:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        os.makedirs(cfg.data_dir, exist_ok=True)
        self.started = time.time()
        self.rpc = chain.Rpc(cfg.rpc_url)
        self.dex = chain.Dex()
        self.grok = Grok()
        self.budget = Budget(cfg.daily_budget_usd)
        self.desk = Desk(self.rpc, self.dex, self.grok, self.budget, cfg.data_dir, cfg.x_days, cfg.max_turns)
        self.tape = Tape()
        self.checked: dict[str, float] = {}     # mint -> last time the auto desk looked at its market cap
        self.auto_times: list[float] = []
        self.requests_total = 0
        self._stop = threading.Event()
        self.queue: list[tuple[str, str, str]] = []
        self.qlock = threading.Lock()

    # ---------------------------------------------------------------- run
    def run(self):
        log.info("level desk starting. grok key: %s. rpc: %s", "yes" if self.grok.ready else "NO", self.cfg.rpc_url)
        self.desk.event("boot", f"desk up · grok {'ready' if self.grok.ready else 'no key'} · auto reads {'on' if self.cfg.auto_reads else 'off'}")
        self.tape.start()
        threading.Thread(target=self._worker, name="desk-worker", daemon=True).start()
        last_auto = 0.0
        while not self._stop.is_set():
            try:
                if self.cfg.auto_reads and time.time() - last_auto >= 60:
                    self._pick()
                    last_auto = time.time()
                self.write()
            except Exception as e:  # noqa: BLE001
                log.exception("loop: %s", e)
            self._stop.wait(self.cfg.poll_s)

    def stop(self):
        self._stop.set()
        self.tape.stop()

    # ---------------------------------------------------------------- the auto desk
    def _pick(self):
        """Launches old enough to have a pair, young enough to still be a launch, big enough to matter."""
        cands = [l for l in self.tape.between(self.cfg.auto_min_age_min * 60, self.cfg.auto_max_age_min * 60)
                 if time.time() - self.checked.get(l["mint"], 0) > 600 and not self.desk.find(l["mint"])]
        if not cands:
            return
        cands = cands[-150:]      # newest 150 per minute; dexscreener takes 30 per call
        pairs = self.dex.pairs([l["mint"] for l in cands])
        now = time.time()
        for l in cands:
            self.checked[l["mint"]] = now
        hot = []
        for l in cands:
            p = pairs.get(l["mint"])
            if not p:
                continue
            mc = float(p.get("marketCap") or p.get("fdv") or 0)
            if mc >= self.cfg.auto_mc_usd:
                hot.append((mc, l))
        hot.sort(reverse=True)
        for mc, l in hot:
            self.auto_times = [t for t in self.auto_times if now - t < 3600]
            if len(self.auto_times) >= self.cfg.auto_per_hour:
                self.desk.event("skip", f"{l['symbol']} crossed ${mc/1000:.0f}K but the desk already read {self.cfg.auto_per_hour} this hour", mint=l["mint"])
                break
            if self.budget.left() <= 0 and self.grok.ready:
                self.desk.event("skip", f"{l['symbol']} crossed ${mc/1000:.0f}K, Grok budget for today is spent", mint=l["mint"])
                break
            self.auto_times.append(now)
            self.enqueue(l["mint"], "desk", l.get("name"), l.get("symbol"))
            self.desk.event("pick", f"{l['symbol']} crossed ${mc/1000:.0f}K at {int((now - l['ts'])/60)} min, reading", mint=l["mint"], symbol=l["symbol"])
        if len(self.checked) > 20_000:
            cut = now - 7200
            self.checked = {m: t for m, t in self.checked.items() if t > cut}

    def enqueue(self, mint: str, source: str, name=None, symbol=None):
        with self.qlock:
            if any(q[0] == mint for q in self.queue):
                return
            self.queue.append((mint, source, name, symbol))

    def _worker(self):
        while not self._stop.is_set():
            item = None
            with self.qlock:
                if self.queue:
                    item = self.queue.pop(0)
            if not item:
                time.sleep(1)
                continue
            mint, source, name, symbol = item
            try:
                self.desk.read(mint, source=source, name=name, symbol=symbol)
            except Exception as e:  # noqa: BLE001
                log.warning("read %s: %s", mint, e)
                self.desk.event("error", f"read of {mint[:8]}… failed: {e}"[:160], mint=mint)
            self.write()

    # ---------------------------------------------------------------- the files
    def read_now(self, mint: str, force: bool = False) -> dict:
        """A person asked. Runs in the request thread, the wall is shared."""
        self.requests_total += 1
        l = next((x for x in self.tape.latest(4000) if x["mint"] == mint), None)
        return self.desk.read(mint, source="request", name=l and l.get("name"), symbol=l and l.get("symbol"), force=force)

    def state(self) -> dict:
        now = time.time()
        wall = self.desk.wall
        day = [c for c in wall if now - c["ts"] < 86400]
        secs = [c["seconds"] for c in day if c.get("seconds")]
        return {
            "updated": int(now),
            "uptime_s": int(now - self.started),
            "tape": {"ok": self.tape.ok, "per_min": self.tape.per_minute(), "per_hour": self.tape.per_hour(), "total": self.tape.total, "error": self.tape.last_error},
            "desk": {
                "reads_total": self.desk.reads_total, "reads_24h": len(day), "wall": len(wall),
                "avg_seconds": round(sum(secs) / len(secs), 1) if secs else None,
                "requests_total": self.requests_total,
                "auto": {"on": self.cfg.auto_reads, "mc_usd": self.cfg.auto_mc_usd, "age_min": [self.cfg.auto_min_age_min, self.cfg.auto_max_age_min], "per_hour": self.cfg.auto_per_hour},
                "budget": self.budget.snapshot(),
                "queue": len(self.queue),
            },
            "grok": {"ready": self.grok.ready, "model": self.grok.model, "calls": self.grok.calls, "errors": self.grok.errors, "error": self.grok.last_error,
                     "posts_fetched": self.grok.posts_fetched, "spent_usd": round(self.grok.spent_usd, 3), "x_days": self.cfg.x_days},
            "rpc": {"url": self.cfg.rpc_url.split("?")[0].split("api-key")[0], "calls": self.rpc.calls, "errors": self.rpc.errors},
            "dex": {"ok": self.dex.ok, "calls": self.dex.calls, "error": self.dex.last_error},
            "links": {k: v for k, v in self.cfg.links.items() if k in ("x", "github", "site")},
        }

    def write(self):
        from .desk import Desk as _D  # noqa: F401
        self.desk._save("state.json", self.state())
        self.desk._save("tape.json", {"updated": int(time.time()), "per_min": self.tape.per_minute(), "per_hour": self.tape.per_hour(), "launches": self.tape.latest(60)})
        self.desk.save()
