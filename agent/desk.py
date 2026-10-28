"""The desk: one read of one launch, start to finish, and the wall it writes to.

A read is: the pump.fun bonding curve (creator, progress), the mint (supply, authorities), the top
holders with curve and pool accounts named, the creator wallet (age, activity, balance, what it still
holds), the Dexscreener pair (market cap, liquidity, volume, socials), and then Grok reading X. The
result is a card of counts and quotes with the time it took and what it cost. No score, no verdict,
no price call: the desk reports, the person decides.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time

from . import chain
from .grok import Grok, GrokError

log = logging.getLogger("level.desk")

MAX_WALL = 400


class Budget:
    """Dollars per UTC day for Grok. When it is gone, the desk says so instead of pretending."""

    def __init__(self, daily_usd: float):
        self.daily = float(daily_usd)
        self.day = _today()
        self.spent = 0.0
        self.reads = 0
        self.lock = threading.Lock()

    def _roll(self):
        d = _today()
        if d != self.day:
            self.day, self.spent, self.reads = d, 0.0, 0

    def left(self) -> float:
        with self.lock:
            self._roll()
            return max(0.0, self.daily - self.spent)

    def take(self, usd: float):
        with self.lock:
            self._roll()
            self.spent += usd
            self.reads += 1

    def snapshot(self) -> dict:
        with self.lock:
            self._roll()
            return {"daily_usd": self.daily, "spent_usd": round(self.spent, 3), "left_usd": round(max(0.0, self.daily - self.spent), 3), "reads_today": self.reads}


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


class Desk:
    def __init__(self, rpc: chain.Rpc, dex: chain.Dex, grok: Grok, budget: Budget, data_dir: str, x_days: int = 7, max_turns: int = 4):
        self.rpc, self.dex, self.grok, self.budget = rpc, dex, grok, budget
        self.data_dir = data_dir
        self.x_days = x_days
        self.max_turns = max_turns
        self.lock = threading.Lock()
        self.wall: list[dict] = self._load("reads.json", [])
        self.events: list[dict] = self._load("events.json", {"events": []}).get("events", [])
        self.reads_total = sum(1 for _ in self.wall)
        self.inflight: dict[str, threading.Event] = {}
        self.inflight_lock = threading.Lock()

    # ---------------------------------------------------------------- storage
    def _load(self, name, default):
        try:
            with open(os.path.join(self.data_dir, name)) as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return default

    def _save(self, name, doc):
        path = os.path.join(self.data_dir, name)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(doc, f, separators=(",", ":"))
        os.replace(tmp, path)

    def save(self):
        with self.lock:
            self._save("reads.json", self.wall[:MAX_WALL])
            self._save("events.json", {"updated": int(time.time()), "events": self.events[-300:]})

    def event(self, kind: str, text: str, **extra):
        ev = {"ts": int(time.time()), "kind": kind, "text": text}
        ev.update(extra)
        with self.lock:
            self.events.append(ev)
            self.events = self.events[-400:]

    def find(self, mint: str) -> dict | None:
        with self.lock:
            for c in self.wall:
                if c["mint"] == mint:
                    return c
        return None

    # ---------------------------------------------------------------- the read
    def read(self, mint: str, source: str = "request", name: str | None = None, symbol: str | None = None, force: bool = False) -> dict:
        """Read one launch. Same mint twice within ten minutes returns the card on the wall."""
        if not chain.is_pubkey(mint):
            raise ValueError("that is not a Solana address")
        cur = self.find(mint)
        if cur and not force and time.time() - cur["ts"] < 600:
            return cur
        # one read per mint at a time; a second caller waits for the first
        with self.inflight_lock:
            ev = self.inflight.get(mint)
            if ev is None:
                ev = threading.Event()
                self.inflight[mint] = ev
                owner = True
            else:
                owner = False
        if not owner:
            ev.wait(120)
            return self.find(mint) or {"mint": mint, "error": "read did not finish"}
        try:
            card = self._read(mint, source, name, symbol)
            self._put(card)
            return card
        finally:
            with self.inflight_lock:
                self.inflight.pop(mint, None)
            ev.set()

    def _read(self, mint: str, source: str, name: str | None, symbol: str | None) -> dict:
        t0 = time.time()
        steps = []
        card: dict = {"mint": mint, "ts": int(t0), "source": source, "name": name, "symbol": symbol}
        # 1. bonding curve
        bc = None
        try:
            addr = chain.bonding_curve_address(mint)
            acc = self.rpc.account(addr) if addr else None
            data = (acc or {}).get("data")
            if data and isinstance(data, list):
                bc = chain.decode_bonding_curve(data[0])
            steps.append(("curve", round(time.time() - t0, 2), "ok" if bc else "not a pump.fun token"))
        except chain.RpcError as e:
            steps.append(("curve", round(time.time() - t0, 2), f"rpc: {e}"))
        # 2. market
        t1 = time.time()
        pair = self.dex.pair(mint)
        market = chain.market_facts(pair)
        card["market"] = market
        if not card.get("name"):
            card["name"] = market.get("name")
        if not card.get("symbol"):
            card["symbol"] = market.get("symbol")
        steps.append(("market", round(time.time() - t1, 2), "indexed" if market.get("indexed") else "no pair on dexscreener yet"))
        # 3. chain facts
        t2 = time.time()
        facts = chain.chain_facts(self.rpc, mint, bc)
        card["chain"] = facts
        steps.append(("chain", round(time.time() - t2, 2), facts.get("error") or "ok"))
        if not card.get("name") and not card.get("symbol"):
            card["name"], card["symbol"] = self._name_from_tape(mint)
        # 4. grok reads x
        t3 = time.time()
        handle = chain.x_handle_from(market.get("socials") or [], market.get("websites") or [])
        card["x"] = None
        if not self.grok.ready:
            steps.append(("x", 0, "no XAI_API_KEY on the box: X not read"))
            card["x_error"] = "the desk has no Grok key yet"
        elif self.budget.left() <= 0:
            steps.append(("x", 0, "daily Grok budget spent"))
            card["x_error"] = "today's Grok budget is spent, chain facts only"
        else:
            try:
                x = self.grok.read_x(mint, card.get("name") or "", card.get("symbol") or "", handle, days=self.x_days, max_turns=self.max_turns)
                self.budget.take(float((x.get("usage") or {}).get("cost_usd") or 0))
                card["x"] = x
                steps.append(("x", round(time.time() - t3, 2), f"{(x.get('usage') or {}).get('posts', 0)} posts fetched"))
            except GrokError as e:
                card["x_error"] = f"grok: {e}"[:200]
                steps.append(("x", round(time.time() - t3, 2), f"grok: {e}"[:120]))
        card["handle"] = handle
        card["lines"] = lines_for(card)
        card["steps"] = [{"step": s, "s": t, "note": n} for s, t, n in steps]
        card["seconds"] = round(time.time() - t0, 1)
        card["cost_usd"] = round(float(((card.get("x") or {}).get("usage") or {}).get("cost_usd") or 0), 4)
        return card

    def _name_from_tape(self, mint: str):
        return None, None

    def _put(self, card: dict):
        with self.lock:
            prev = next((c for c in self.wall if c["mint"] == card["mint"]), None)
            card["reads"] = (prev.get("reads", 1) + 1) if prev else 1
            card["first_read"] = prev.get("first_read", prev["ts"]) if prev else card["ts"]
            self.wall = [c for c in self.wall if c["mint"] != card["mint"]]
            self.wall.insert(0, card)
            self.wall = self.wall[:MAX_WALL]
            self.reads_total += 1
        self.event("read", f"{card.get('symbol') or card['mint'][:6]} read in {card['seconds']}s", mint=card["mint"], source=card["source"],
                   seconds=card["seconds"], cost=card["cost_usd"], symbol=card.get("symbol"))
        self.save()


# ---------------------------------------------------------------- the lines on a card
def lines_for(card: dict) -> list[dict]:
    """Facts as short lines. Each has a kind the site colours: info, note, alert. Alert is a count, not a verdict."""
    out = []
    ch = card.get("chain") or {}
    mk = card.get("market") or {}
    x = card.get("x") or {}

    def add(kind, text):
        out.append({"kind": kind, "text": text})

    b = ch.get("bonding")
    if b:
        add("info", "graduated to a pool" if b.get("complete") else f"{b.get('progress_pct')}% of the way to graduation on the bonding curve")
    if ch.get("creator"):
        age = ch.get("creator_age_days")
        tx = ch.get("creator_tx_sampled")
        if age is not None:
            if ch.get("creator_age_floor"):
                add("info", f"creator wallet: older than {int(age)} days, more than 1,000 transactions")
            elif age < 1:
                add("alert", f"creator wallet is less than a day old ({tx} transactions)")
            elif age < 7:
                add("note", f"creator wallet is {age:.0f} days old, {tx} transactions")
            else:
                add("info", f"creator wallet: {age:.0f} days old, {tx} transactions")
        if ch.get("creator_sol") is not None:
            add("info", f"creator holds {ch['creator_sol']} SOL")
        if ch.get("creator_holds_pct") is not None:
            p = ch["creator_holds_pct"]
            add("alert" if p >= 10 else "note" if p >= 3 else "info", f"creator still holds {p:g}% of supply")
    if ch.get("top10_pct") is not None:
        p = ch["top10_pct"]
        add("alert" if p >= 50 else "note" if p >= 30 else "info", f"top 10 wallets hold {p:g}% (curve and pools excluded)")
    if ch.get("mint_authority"):
        add("alert", "mint authority is still live")
    if ch.get("freeze_authority"):
        add("alert", "freeze authority is still live")
    if mk.get("indexed"):
        add("info", f"market cap ${_k(mk.get('mc'))} · liquidity ${_k(mk.get('liq'))} · 24h volume ${_k(mk.get('vol24'))}")
        if mk.get("buys24") is not None:
            add("info", f"24h: {mk.get('buys24')} buys, {mk.get('sells24')} sells")
        if not mk.get("socials") and not mk.get("websites"):
            add("note", "no website and no social links on the listing")
        if mk.get("boosts"):
            add("note", f"{mk['boosts']} paid Dexscreener boosts active")
    else:
        add("note", "not on Dexscreener yet")
    if x and not x.get("unparsed"):
        posts, accts = x.get("posts_seen"), x.get("accounts")
        if posts is not None and accts is not None:
            if posts == 0:
                add("alert", "Grok found no posts about it on X")
            else:
                add("info", f"X: {posts} posts from {accts} accounts in {7} days")
        if x.get("accounts_under_30d") and accts:
            share = x["accounts_under_30d"] / max(1, accts) * 100
            add("alert" if share >= 50 else "note", f"{x['accounts_under_30d']} of {accts} posting accounts were created in the last 30 days")
        if x.get("same_text_posts"):
            add("alert" if x["same_text_posts"] >= 5 else "note", f"{x['same_text_posts']} posts repeat the same text")
        off = x.get("official") or {}
        if off.get("exists"):
            fol = off.get("followers")
            add("info", f"project account @{off.get('handle')}: {_k(fol) if fol is not None else '?'} followers, since {off.get('created') or '?'}")
        elif card.get("handle"):
            add("note", f"listed handle @{card['handle']} was not found by Grok")
        else:
            add("note", "no project account on X")
        if x.get("big_accounts"):
            add("info", "mentioned by " + ", ".join("@" + h.lstrip("@") for h in x["big_accounts"][:5]))
    elif card.get("x_error"):
        add("note", card["x_error"])
    return out


def _k(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "–"
    if n >= 1e6:
        return f"{n/1e6:.1f}M"
    if n >= 1e3:
        return f"{n/1e3:.1f}K"
    return f"{n:.0f}"
