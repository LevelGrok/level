"""The Grok side of the desk: one call to xAI's Responses API with the x_search tool.

Grok searches X for the launch (ticker, name, mint, the project's handle if it has one) and answers
with counts: how many posts, how many different accounts, how old those accounts look, whether the
project has an account of its own and how old that is. It is asked for facts and short quotes, never
for a price call. The key lives in the environment of the service (XAI_API_KEY), nowhere in the code.

Cost: xAI bills per post fetched ($5 per 1k) and per profile ($10 per 1k) on top of tokens, so every
call carries max_turns and a date window, and the desk keeps a daily budget.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger("level.grok")

API = "https://api.x.ai/v1/responses"
MODELS = ["grok-4-fast-non-reasoning", "grok-4-fast", "grok-4.7", "grok-4"]
POST_COST = 0.005     # $ per X post fetched
USER_COST = 0.010     # $ per X profile fetched
TOKEN_COST_IN = 0.20 / 1e6   # grok-4-fast list price, used for the estimate only
TOKEN_COST_OUT = 0.50 / 1e6

PROMPT = """You are the desk at LEVEL, a read-only teammate that reads a new Solana token launch on pump.fun.
Search X for posts about this launch from the last {days} days. Use the ticker, the name and the mint address as queries, and the project's handle if given. Keep it to a few searches.

launch:
  name: {name}
  ticker: ${symbol}
  mint: {mint}
  project x handle (from its listing, may be wrong): {handle}

Answer with ONE json object and nothing else, no markdown fence, no prose:
{{
  "posts_seen": <int, posts you actually saw about this launch>,
  "accounts": <int, distinct accounts posting about it>,
  "accounts_under_30d": <int or null, how many of those accounts were created in the last 30 days, if you could see it>,
  "oldest_account_days": <int or null>,
  "official": {{"handle": "<handle without @ or null>", "exists": <bool>, "followers": <int or null>, "created": "<YYYY-MM or null>", "posts_about_launch": <int or null>}},
  "first_mention": "<ISO datetime of the earliest post you saw, or null>",
  "same_text_posts": <int, posts that repeat the same text, a sign of a farm>,
  "big_accounts": [<handles with 50k+ followers that mentioned it, max 5>],
  "notable": [{{"handle": "<handle>", "text": "<quote, max 140 chars, verbatim>", "url": "<post url>"}}],  // max 5, the most informative, include critical ones
  "summary": "<2 sentences, facts only: who is talking, what they say, what is missing. no price opinion, no advice, no prediction>"
}}
If you find nothing, say so with zeros and nulls and a one-sentence summary. Never invent a post."""


class GrokError(Exception):
    pass


class Grok:
    def __init__(self, key: str | None = None, models: list[str] | None = None, timeout: float = 90.0):
        self.key = key or os.environ.get("XAI_API_KEY", "")
        self.models = models or MODELS
        self.model = self.models[0]
        self.timeout = timeout
        self.s = requests.Session()
        self.calls = 0
        self.errors = 0
        self.last_error = ""
        self.spent_usd = 0.0
        self.posts_fetched = 0

    @property
    def ready(self) -> bool:
        return bool(self.key)

    def _request(self, body: dict) -> dict:
        if not self.key:
            raise GrokError("no XAI_API_KEY in the environment")
        last = None
        for model in self.models:
            body["model"] = model
            try:
                r = self.s.post(API, json=body, headers={"Authorization": f"Bearer {self.key}"}, timeout=self.timeout)
            except requests.RequestException as e:
                self.errors += 1
                self.last_error = str(e)[:160]
                raise GrokError(self.last_error) from e
            if r.status_code == 404 or (r.status_code == 400 and "model" in r.text.lower()):
                last = f"{model}: {r.text[:120]}"
                continue
            if r.status_code >= 400:
                self.errors += 1
                self.last_error = f"http {r.status_code}: {r.text[:160]}"
                raise GrokError(self.last_error)
            self.model = model
            if model != self.models[0]:
                self.models.remove(model)
                self.models.insert(0, model)
            return r.json()
        self.errors += 1
        self.last_error = f"no model accepted: {last}"
        raise GrokError(self.last_error)

    def read_x(self, mint: str, name: str, symbol: str, handle: str | None, days: int = 7, max_turns: int = 4) -> dict:
        """One desk read. Returns the parsed json plus usage and an estimated cost."""
        self.calls += 1
        today = datetime.now(timezone.utc).date()
        tool = {"type": "x_search", "from_date": (today - timedelta(days=days)).isoformat(), "to_date": (today + timedelta(days=1)).isoformat()}
        prompt = PROMPT.format(days=days, name=name or "unknown", symbol=symbol or "?", mint=mint, handle=("@" + handle) if handle else "none listed")
        body = {"input": [{"role": "user", "content": prompt}], "tools": [tool], "max_turns": max_turns}
        t0 = time.time()
        doc = self._request(body)
        text = output_text(doc)
        usage = doc.get("usage") or {}
        det = usage.get("server_side_tool_usage_details") or {}
        posts = int(det.get("x_posts_fetched") or 0)
        users = int(det.get("x_users_fetched") or 0)
        cost = posts * POST_COST + users * USER_COST + (usage.get("input_tokens") or 0) * TOKEN_COST_IN + (usage.get("output_tokens") or 0) * TOKEN_COST_OUT
        self.spent_usd += cost
        self.posts_fetched += posts
        parsed = parse_json(text)
        if parsed is None:
            parsed = {"posts_seen": None, "accounts": None, "summary": strip_cites(text)[:400], "unparsed": True}
        parsed["usage"] = {"posts": posts, "users": users, "in": usage.get("input_tokens"), "out": usage.get("output_tokens"),
                           "cost_usd": round(cost, 4), "model": self.model, "seconds": round(time.time() - t0, 1),
                           "searches": int(det.get("x_search_calls") or 0)}
        parsed["citations"] = citations(doc)[:12]
        return parsed


# ---------------------------------------------------------------- response plumbing
def output_text(doc: dict) -> str:
    parts = []
    for item in doc.get("output") or []:
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if c.get("type") == "output_text":
                    parts.append(c.get("text") or "")
    if not parts and isinstance(doc.get("output_text"), str):
        parts.append(doc["output_text"])
    return "\n".join(parts)


def citations(doc: dict) -> list[str]:
    urls = []
    for u in doc.get("citations") or []:
        if isinstance(u, str):
            urls.append(u)
        elif isinstance(u, dict) and u.get("url"):
            urls.append(u["url"])
    for item in doc.get("output") or []:
        for c in item.get("content") or []:
            for a in c.get("annotations") or []:
                if a.get("url"):
                    urls.append(a["url"])
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def strip_cites(text: str) -> str:
    return re.sub(r"\[\[\d+\]\]\([^)]*\)", "", text or "").strip()


def parse_json(text: str) -> dict | None:
    """The model is asked for bare json; be forgiving about fences and citation markers."""
    t = strip_cites(text)
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t.strip(), flags=re.S)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    for cand in (t[start:end + 1],):
        try:
            d = json.loads(cand)
            return d if isinstance(d, dict) else None
        except ValueError:
            try:
                d = json.loads(re.sub(r",\s*([}\]])", r"\1", cand))
                return d if isinstance(d, dict) else None
            except ValueError:
                return None
    return None
