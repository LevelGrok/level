"""python -m agent [-c config.toml] run | read <mint> [--json] [--force] | doctor | status

run      watch the launch tape, read what crosses the line, serve the files on http
read     read one launch now and print the card
doctor   what this box can reach: rpc, dexscreener, pumpportal, grok
status   the desk's counters from the data dir
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

try:
    import tomllib
except ModuleNotFoundError:  # python < 3.11
    import tomli as tomllib  # type: ignore

from .main import Agent, Config


def load_config(path: str | None) -> Config:
    cfg = Config()
    if path and os.path.exists(path):
        with open(path, "rb") as f:
            doc = tomllib.load(f)
        for k, v in (doc.get("agent") or {}).items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        cfg.links = dict(doc.get("links") or {})
        cfg.server = dict(doc.get("server") or {})
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(prog="level", description="a Grok teammate that reads a pump.fun launch before you do")
    ap.add_argument("-c", "--config", default="config.toml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    r = sub.add_parser("read")
    r.add_argument("mint")
    r.add_argument("--json", action="store_true")
    r.add_argument("--force", action="store_true")
    sub.add_parser("doctor")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = load_config(a.config)

    if a.cmd == "run":
        from . import server
        agent = Agent(cfg)
        store = server.Store(cfg.data_dir, agent, int(cfg.server.get("read_per_min", 4)))
        server.serve(store, cfg.server.get("host", "127.0.0.1"), int(cfg.server.get("port", 8793)), cfg.server.get("cors", "*"))
        try:
            agent.run()
        except KeyboardInterrupt:
            agent.stop()
        return 0

    if a.cmd == "read":
        agent = Agent(cfg)
        card = agent.desk.read(a.mint, source="cli", force=a.force)
        if a.json:
            print(json.dumps(card, indent=1))
        else:
            print_card(card)
        return 0

    if a.cmd == "doctor":
        return doctor(cfg)

    if a.cmd == "status":
        try:
            with open(os.path.join(cfg.data_dir, "state.json")) as f:
                st = json.load(f)
        except Exception:  # noqa: BLE001
            print("no state.json yet")
            return 1
        print(json.dumps(st, indent=1))
        return 0
    return 1


def print_card(c: dict):
    print(f"{c.get('symbol') or '?'}  {c.get('name') or ''}  {c['mint']}")
    print(f"read in {c.get('seconds')}s, grok ${c.get('cost_usd', 0):.3f}")
    for ln in c.get("lines") or []:
        mark = {"alert": "!!", "note": " !", "info": "  "}.get(ln["kind"], "  ")
        print(f" {mark} {ln['text']}")
    x = c.get("x") or {}
    if x.get("summary"):
        print(" x:", x["summary"])
    for n in (x.get("notable") or [])[:5]:
        print(f"   @{n.get('handle')}: {n.get('text')}")
    for s in c.get("steps") or []:
        print(f"   [{s['step']} {s['s']}s] {s['note']}")


def doctor(cfg: Config) -> int:
    from . import chain
    from .grok import Grok
    ok = True
    print(f"config: data_dir={cfg.data_dir} rpc={cfg.rpc_url.split('?')[0]} budget=${cfg.daily_budget_usd}/day auto={'on' if cfg.auto_reads else 'off'} (>= ${cfg.auto_mc_usd:,.0f})")
    rpc = chain.Rpc(cfg.rpc_url)
    try:
        slot = rpc.call("getSlot", [{"commitment": "confirmed"}])
        print(f"rpc:        ok, slot {slot}")
    except Exception as e:  # noqa: BLE001
        print(f"rpc:        FAIL {e}")
        ok = False
    try:
        top = rpc.largest("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
        print(f"rpc reads:  ok, getTokenLargestAccounts returned {len(top)} rows")
    except Exception as e:  # noqa: BLE001
        print(f"rpc reads:  FAIL {e}  (the public endpoint often blocks this; use a free Helius key in rpc_url)")
        ok = False
    dex = chain.Dex()
    p = dex.pair("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
    print(f"dexscreener: {'ok, bonk indexed' if p else 'FAIL ' + dex.last_error}")
    ok = ok and bool(p)
    try:
        import websocket
        ws = websocket.create_connection("wss://pumpportal.fun/api/data", timeout=10)
        ws.send(json.dumps({"method": "subscribeNewToken"}))
        ws.settimeout(20)
        got = 0
        t0 = time.time()
        while time.time() - t0 < 20 and got < 2:
            m = json.loads(ws.recv())
            if m.get("txType") == "create":
                got += 1
        ws.close()
        print(f"pumpportal: ok, {got} launches in {time.time()-t0:.0f}s")
    except Exception as e:  # noqa: BLE001
        print(f"pumpportal: FAIL {e}")
        ok = False
    g = Grok()
    if not g.ready:
        print("grok:       NO KEY (XAI_API_KEY). the desk will read chain facts only. set it with deploy/key.sh")
    else:
        try:
            doc = g._request({"input": [{"role": "user", "content": "Reply with the single word: ready"}]})
            from .grok import output_text
            print(f"grok:       ok, model {g.model}, said: {output_text(doc).strip()[:40]}")
        except Exception as e:  # noqa: BLE001
            print(f"grok:       FAIL {e}")
            ok = False
    print("all good" if ok else "something above needs fixing")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
