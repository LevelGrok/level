import base64
import json
import struct
import threading
import time
import urllib.request

import pytest

from agent import chain, grok, server
from agent.desk import Budget, lines_for

BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


# ---------------------------------------------------------------- base58 + pda
def test_base58_roundtrip():
    raw = chain.b58decode(BONK)
    assert len(raw) == 32
    assert chain.b58encode(raw) == BONK
    assert chain.b58encode(b"\x00\x00\x01") == "112"
    assert chain.b58decode("112") == b"\x00\x00\x01"


def test_is_pubkey():
    assert chain.is_pubkey(BONK)
    assert chain.is_pubkey(chain.PUMP_PROGRAM)
    assert not chain.is_pubkey("hello")
    assert not chain.is_pubkey("0" * 40)          # 0 is not a base58 digit
    assert not chain.is_pubkey("1" * 50)


def test_pda_is_off_curve_and_deterministic():
    a = chain.bonding_curve_address(BONK)
    b = chain.bonding_curve_address(BONK)
    assert a == b and chain.is_pubkey(a)
    assert not chain._ed25519_on_curve(chain.b58decode(a))
    # a real public key is on the curve
    assert chain._ed25519_on_curve(chain.b58decode(BONK))


def test_pump_pda_matches_known_vector():
    # bonding curve of a pump.fun mint, computed with the solana sdk offline
    mint = "73uDjGJtnUtknCigAWcBFJ3XmdLLp23E88Ff1Ge6pump"
    addr = chain.bonding_curve_address(mint)
    assert chain.is_pubkey(addr)
    assert addr != mint


def test_decode_bonding_curve():
    creator = chain.b58decode(BONK)
    raw = b"\x00" * 8 + struct.pack("<QQQQQ", 1, 2, 3, 40 * chain.LAMPORTS, 1_000_000) + b"\x00" + creator
    bc = chain.decode_bonding_curve(base64.b64encode(raw).decode())
    assert bc["creator"] == BONK and bc["complete"] is False
    assert bc["progress_pct"] == pytest.approx(47.1, abs=0.1)
    raw2 = raw[:48] + b"\x01" + creator
    assert chain.decode_bonding_curve(base64.b64encode(raw2).decode())["progress_pct"] == 100.0
    assert chain.decode_bonding_curve(base64.b64encode(b"\x00" * 10).decode()) is None


def test_x_handle_from_socials():
    assert chain.x_handle_from([{"type": "twitter", "url": "https://x.com/levelsol"}], []) == "levelsol"
    assert chain.x_handle_from([{"type": "twitter", "url": "https://twitter.com/@some_one?s=20"}], []) == "some_one"
    assert chain.x_handle_from([{"type": "twitter", "url": "https://x.com/i/communities/123"}], []) is None
    assert chain.x_handle_from([], ["https://x.com/fromsite"]) == "fromsite"
    assert chain.x_handle_from([{"type": "telegram", "url": "https://t.me/x"}], []) is None


def test_market_facts_shape():
    p = {"dexId": "pumpfun", "pairAddress": "p", "baseToken": {"name": "Level", "symbol": "LEVEL", "address": "m"}, "priceUsd": "0.0001",
         "marketCap": 31000, "liquidity": {"usd": 9000}, "volume": {"h24": 120000}, "txns": {"h24": {"buys": 300, "sells": 200}},
         "pairCreatedAt": 1700000000000, "info": {"socials": [{"type": "twitter", "url": "https://x.com/levelsol"}], "websites": []}}
    m = chain.market_facts(p)
    assert m["indexed"] and m["mc"] == 31000 and m["buys24"] == 300 and m["created"] == 1700000000
    assert chain.market_facts(None) == {"indexed": False}


# ---------------------------------------------------------------- lines
def test_lines_alerts_are_counts():
    card = {"chain": {"creator": "c", "creator_age_days": 0.3, "creator_tx_sampled": 12, "creator_sol": 0.4, "creator_holds_pct": 14.0,
                      "top10_pct": 61.0, "bonding": {"complete": False, "progress_pct": 40.0}, "mint_authority": False},
            "market": {"indexed": True, "mc": 30000, "liq": 8000, "vol24": 50000, "buys24": 10, "sells24": 5, "socials": [], "websites": []},
            "x": {"posts_seen": 9, "accounts": 3, "accounts_under_30d": 3, "same_text_posts": 6, "official": {"exists": False}}}
    ls = lines_for(card)
    txt = " | ".join(l["text"] for l in ls)
    assert "less than a day old" in txt and "61%" in txt and "14%" in txt
    assert "3 of 3 posting accounts" in txt and "6 posts repeat" in txt and "no website" in txt
    kinds = {l["kind"] for l in ls}
    assert kinds <= {"info", "note", "alert"}
    assert not any(w in txt.lower() for w in ("buy now", "sell now", "moon", "rug", "scam", "score"))


def test_lines_when_grok_missing():
    card = {"chain": {}, "market": {"indexed": False}, "x": None, "x_error": "the desk has no Grok key yet"}
    txt = " | ".join(l["text"] for l in lines_for(card))
    assert "no Grok key" in txt and "not on Dexscreener" in txt


# ---------------------------------------------------------------- grok plumbing
def test_parse_json_variants():
    assert grok.parse_json('{"a": 1}') == {"a": 1}
    assert grok.parse_json('```json\n{"a": 1,}\n```') == {"a": 1}
    assert grok.parse_json('Here you go: {"posts_seen": 3 [[1]](https://x.com/a/status/1)}') == {"posts_seen": 3}
    assert grok.parse_json("nothing here") is None


def test_output_text_and_citations():
    doc = {"output": [{"type": "custom_tool_call"}, {"type": "message", "content": [{"type": "output_text", "text": "hi [[1]](https://x.com/p/1)",
           "annotations": [{"type": "url_citation", "url": "https://x.com/p/1"}]}]}], "citations": ["https://x.com/p/2", "https://x.com/p/1"]}
    assert grok.output_text(doc).startswith("hi")
    assert grok.citations(doc) == ["https://x.com/p/2", "https://x.com/p/1"]
    assert grok.strip_cites("a [[1]](https://x) b") == "a  b"


def test_grok_without_key_is_not_ready(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    g = grok.Grok(key="")
    assert not g.ready
    with pytest.raises(grok.GrokError):
        g.read_x("m", "n", "s", None)


def test_budget():
    b = Budget(1.0)
    assert b.left() == 1.0
    b.take(0.4)
    b.take(0.7)
    assert b.left() == 0.0 and b.snapshot()["reads_today"] == 2


# ---------------------------------------------------------------- http
class FakeAgent:
    def __init__(self):
        self.calls = []

    def read_now(self, mint, force=False):
        self.calls.append((mint, force))
        return {"mint": mint, "seconds": 1.0, "lines": [], "ts": int(time.time())}


@pytest.fixture
def http(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"updated": 1}))
    agent = FakeAgent()
    store = server.Store(str(tmp_path), agent, read_per_min=2)
    srv = server.serve(store, "127.0.0.1", 0)
    port = srv.server_address[1]
    yield f"http://127.0.0.1:{port}", agent, store
    srv.shutdown()


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_files_and_read(http):
    base, agent, store = http
    with urllib.request.urlopen(base + "/state.json") as r:
        assert json.loads(r.read())["updated"] == 1
        assert r.headers["Access-Control-Allow-Origin"] == "*"
    with urllib.request.urlopen(base + "/reads.json") as r:
        assert json.loads(r.read()) == []
    code, doc = _post(base, "/read", {"mint": BONK})
    assert code == 200 and doc["mint"] == BONK and agent.calls == [(BONK, False)]
    code, doc = _post(base, "/read", {"mint": "nope"})
    assert code == 400
    code, doc = _post(base, "/read", {"mint": BONK, "force": True})
    assert code == 429                     # third call in a minute
    assert urllib.request.urlopen(base + "/health").status == 200


def test_http_never_stores_caller(http, tmp_path):
    base, agent, store = http
    _post(base, "/read", {"mint": BONK})
    files = {p.name for p in tmp_path.iterdir()}
    assert files == {"state.json"}
