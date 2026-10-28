"""Public reads from Solana and Dexscreener. No key, no signer, read-only RPC methods only.

Everything here is a GET or a read-only JSON-RPC call. There is an allow-list of RPC methods and
nothing outside it can be sent, so a bug elsewhere cannot turn this file into something that moves
funds. The agent never holds a key of any kind.
"""

from __future__ import annotations

import logging
import struct
import time

import requests

log = logging.getLogger("level.chain")

UA = "level-desk/0.1 (+https://github.com/LevelGrok/level)"
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
LAMPORTS = 1_000_000_000

# the only RPC methods this agent will ever send. anything else is refused before the request is built.
READ_ONLY = {
    "getAccountInfo", "getTokenSupply", "getTokenLargestAccounts", "getTokenAccountsByOwner",
    "getSignaturesForAddress", "getBalance", "getTransaction", "getHealth", "getSlot", "getMultipleAccounts",
}

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    out = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + out


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    pad = len(b) - len(b.lstrip(b"\x00"))
    return "1" * pad + s


def is_pubkey(s: str) -> bool:
    try:
        return 32 <= len(s) <= 44 and len(b58decode(s)) == 32
    except Exception:  # noqa: BLE001
        return False


class RpcError(Exception):
    pass


class Rpc:
    def __init__(self, url: str, timeout: float = 12.0):
        self.url = url
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.calls = 0
        self.errors = 0

    def call(self, method: str, params: list):
        if method not in READ_ONLY:
            raise RpcError(f"refused: {method} is not a read-only method this agent sends")
        self.calls += 1
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        for attempt in range(3):
            try:
                r = self.s.post(self.url, json=body, timeout=self.timeout)
                if r.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                doc = r.json()
                if "error" in doc:
                    err = doc["error"]
                    if isinstance(err, dict) and err.get("code") == 429:
                        time.sleep(1.5 * (attempt + 1))
                        continue
                    raise RpcError(str(err.get("message") if isinstance(err, dict) else err))
                return doc.get("result")
            except (requests.RequestException, ValueError) as e:
                self.errors += 1
                if attempt == 2:
                    raise RpcError(str(e)) from e
                time.sleep(1.0)
        raise RpcError("rate limited")

    # ---- reads the desk uses
    def account(self, pubkey: str, encoding: str = "base64") -> dict | None:
        res = self.call("getAccountInfo", [pubkey, {"encoding": encoding, "commitment": "confirmed"}])
        return (res or {}).get("value")

    def token_supply(self, mint: str) -> dict | None:
        res = self.call("getTokenSupply", [mint, {"commitment": "confirmed"}])
        return (res or {}).get("value")

    def largest(self, mint: str) -> list[dict]:
        res = self.call("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
        return (res or {}).get("value") or []

    def signatures(self, address: str, limit: int = 1000, before: str | None = None) -> list[dict]:
        opts = {"limit": limit, "commitment": "confirmed"}
        if before:
            opts["before"] = before
        return self.call("getSignaturesForAddress", [address, opts]) or []

    def balance(self, address: str) -> float:
        res = self.call("getBalance", [address, {"commitment": "confirmed"}])
        return ((res or {}).get("value") or 0) / LAMPORTS

    def token_balance_of(self, owner: str, mint: str) -> float:
        res = self.call("getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        total = 0.0
        for acc in (res or {}).get("value") or []:
            try:
                total += float(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"] or 0)
            except (KeyError, TypeError, ValueError):
                pass
        return total

    def multiple(self, pubkeys: list[str]) -> list[dict | None]:
        if not pubkeys:
            return []
        res = self.call("getMultipleAccounts", [pubkeys, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        return (res or {}).get("value") or []


# ---------------------------------------------------------------- pump.fun bonding curve
def _find_pda(seeds: list[bytes], program: bytes) -> bytes | None:
    """Program-derived address, the same walk the Solana SDK does, without the SDK."""
    import hashlib
    try:
        from nacl.signing import VerifyKey  # type: ignore
        def on_curve(b: bytes) -> bool:
            try:
                VerifyKey(b).to_curve25519_public_key()
                return True
            except Exception:  # noqa: BLE001
                return False
    except Exception:  # noqa: BLE001
        on_curve = _ed25519_on_curve
    for bump in range(255, -1, -1):
        h = hashlib.sha256(b"".join(seeds) + bytes([bump]) + program + b"ProgramDerivedAddress").digest()
        if not on_curve(h):
            return h
    return None


def _ed25519_on_curve(b: bytes) -> bool:
    """Pure-python check whether 32 bytes decode to a point on ed25519 (so a PDA must avoid it)."""
    p = 2**255 - 19
    d = (-121665 * pow(121666, p - 2, p)) % p
    y = int.from_bytes(b, "little") & ((1 << 255) - 1)
    if y >= p:
        return False
    y2 = y * y % p
    u = (y2 - 1) % p
    v = (d * y2 + 1) % p
    x2 = u * pow(v, p - 2, p) % p
    if x2 == 0:
        return True
    x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p != 0:
        x = x * pow(2, (p - 1) // 4, p) % p
    return (x * x - x2) % p == 0


def bonding_curve_address(mint: str) -> str | None:
    pda = _find_pda([b"bonding-curve", b58decode(mint)], b58decode(PUMP_PROGRAM))
    return b58encode(pda) if pda else None


def decode_bonding_curve(data_b64: str) -> dict | None:
    """pump.fun BondingCurve account: 8-byte discriminator, 5 u64, bool complete, creator pubkey."""
    import base64
    raw = base64.b64decode(data_b64)
    if len(raw) < 49:
        return None
    vt, vs, rt, rs, supply = struct.unpack_from("<QQQQQ", raw, 8)
    complete = bool(raw[48])
    creator = b58encode(raw[49:81]) if len(raw) >= 81 else None
    # progress along the curve: real sol collected over the sol it takes to graduate (85 SOL historically)
    progress = min(100.0, rs / LAMPORTS / 85.0 * 100.0) if not complete else 100.0
    return {"virtual_token": vt, "virtual_sol": vs, "real_token": rt, "real_sol": rs, "supply": supply,
            "complete": complete, "creator": creator, "progress_pct": round(progress, 1)}


# ---------------------------------------------------------------- dexscreener
DEX = "https://api.dexscreener.com"


class Dex:
    def __init__(self, timeout: float = 10.0):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.timeout = timeout
        self.calls = 0
        self.ok = True
        self.last_error = ""

    def _get(self, path: str):
        self.calls += 1
        try:
            r = self.s.get(DEX + path, timeout=self.timeout)
            if r.status_code == 429:
                self.last_error = "rate limited"
                self.ok = False
                return None
            r.raise_for_status()
            self.ok = True
            self.last_error = ""
            return r.json()
        except (requests.RequestException, ValueError) as e:
            self.ok = False
            self.last_error = str(e)[:120]
            return None

    def pairs(self, mints: list[str]) -> dict[str, dict]:
        """Best pair per mint (highest liquidity), for up to 30 mints in one call."""
        out: dict[str, dict] = {}
        for i in range(0, len(mints), 30):
            chunk = mints[i:i + 30]
            doc = self._get("/tokens/v1/solana/" + ",".join(chunk))
            if not isinstance(doc, list):
                continue
            for p in doc:
                m = (p.get("baseToken") or {}).get("address")
                if not m:
                    continue
                liq = ((p.get("liquidity") or {}).get("usd") or 0)
                cur = out.get(m)
                if cur is None or liq > ((cur.get("liquidity") or {}).get("usd") or 0):
                    out[m] = p
        return out

    def pair(self, mint: str) -> dict | None:
        return self.pairs([mint]).get(mint)


def market_facts(p: dict | None) -> dict:
    if not p:
        return {"indexed": False}
    info = p.get("info") or {}
    socials = [{"type": s.get("type"), "url": s.get("url")} for s in (info.get("socials") or []) if s.get("url")]
    websites = [w.get("url") for w in (info.get("websites") or []) if w.get("url")]
    tx = (p.get("txns") or {}).get("h24") or {}
    return {
        "indexed": True,
        "dex": p.get("dexId"),
        "pair": p.get("pairAddress"),
        "name": (p.get("baseToken") or {}).get("name"),
        "symbol": (p.get("baseToken") or {}).get("symbol"),
        "price_usd": _num(p.get("priceUsd")),
        "mc": _num(p.get("marketCap") or p.get("fdv")),
        "liq": _num((p.get("liquidity") or {}).get("usd")),
        "vol24": _num((p.get("volume") or {}).get("h24")),
        "buys24": tx.get("buys"), "sells24": tx.get("sells"),
        "change1h": _num((p.get("priceChange") or {}).get("h1")),
        "change24": _num((p.get("priceChange") or {}).get("h24")),
        "created": int(p["pairCreatedAt"] / 1000) if p.get("pairCreatedAt") else None,
        "socials": socials, "websites": websites,
        "image": info.get("imageUrl"),
        "boosts": (p.get("boosts") or {}).get("active"),
    }


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- the on-chain read
def x_handle_from(socials: list[dict], websites: list[str]) -> str | None:
    import re
    for s in socials:
        u = s.get("url") or ""
        m = re.search(r"(?:x|twitter)\.com/(?:i/communities/\d+|@?([A-Za-z0-9_]{1,15}))", u)
        if m and m.group(1) and m.group(1).lower() not in ("home", "search", "i", "intent", "share"):
            return m.group(1)
    for u in websites:
        m = re.search(r"(?:x|twitter)\.com/@?([A-Za-z0-9_]{1,15})", u or "")
        if m:
            return m.group(1)
    return None


def chain_facts(rpc: Rpc, mint: str, bc: dict | None) -> dict:
    """Counts and ratios only. Never a prediction, never a score."""
    out: dict = {"creator": None}
    try:
        sup = rpc.token_supply(mint) or {}
        supply = float(sup.get("uiAmount") or 0)
        out["supply"] = supply
        out["decimals"] = sup.get("decimals")
    except RpcError as e:
        out["error"] = f"supply: {e}"
        supply = 0.0
    # mint account: are the authorities still live?
    try:
        acc = rpc.account(mint, "jsonParsed")
        info = (((acc or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
        out["mint_authority"] = bool(info.get("mintAuthority"))
        out["freeze_authority"] = bool(info.get("freezeAuthority"))
    except RpcError:
        pass
    creator = (bc or {}).get("creator")
    out["creator"] = creator
    out["bonding"] = {k: bc[k] for k in ("complete", "progress_pct")} if bc else None
    # top holders, with the curve / pool accounts named so they are not mistaken for people
    try:
        top = rpc.largest(mint)
        owners = _owners_of(rpc, [t["address"] for t in top[:20]])
        programs = _program_of(rpc, sorted(set(owners.values())))
        rows = []
        for t in top[:20]:
            amt = float(t.get("uiAmount") or 0)
            owner = owners.get(t["address"])
            prog = programs.get(owner or "")
            kind = "curve" if prog == PUMP_PROGRAM else ("pool" if prog in POOL_PROGRAMS else "wallet")
            rows.append({"owner": owner or t["address"], "pct": round(amt / supply * 100, 2) if supply else None, "kind": kind})
        people = [r for r in rows if r["kind"] == "wallet"]
        out["top10_pct"] = round(sum(r["pct"] or 0 for r in people[:10]), 1)
        out["top1_pct"] = round(people[0]["pct"], 1) if people and people[0]["pct"] is not None else None
        out["holders"] = rows[:12]
        if creator:
            held = sum(r["pct"] or 0 for r in rows if r["owner"] == creator)
            out["creator_holds_pct"] = round(held, 2)
    except RpcError as e:
        out["error"] = f"holders: {e}"
    # the creator wallet: how old, how busy, how much sol
    if creator:
        try:
            sigs = rpc.signatures(creator, 1000)
            out["creator_tx_sampled"] = len(sigs)
            out["creator_tx_more"] = len(sigs) >= 1000
            if sigs:
                oldest = min((s.get("blockTime") or 0) for s in sigs if s.get("blockTime")) if any(s.get("blockTime") for s in sigs) else None
                newest = max((s.get("blockTime") or 0) for s in sigs)
                if oldest:
                    out["creator_first_seen"] = oldest
                    out["creator_age_days"] = round((time.time() - oldest) / 86400, 1)
                    out["creator_age_floor"] = len(sigs) >= 1000   # older than this, we stopped counting
                out["creator_last_seen"] = newest
            out["creator_sol"] = round(rpc.balance(creator), 3)
            if "creator_holds_pct" not in out and supply:
                out["creator_holds_pct"] = round(rpc.token_balance_of(creator, mint) / supply * 100, 2)
        except RpcError as e:
            out["error"] = f"creator: {e}"
    return out


def _program_of(rpc: Rpc, accounts: list[str]) -> dict[str, str]:
    """Which program owns each account: the system program for a person's wallet, a dex program for a pool."""
    out = {}
    try:
        for acc_addr, acc in zip(accounts, rpc.multiple(accounts)):
            if acc:
                out[acc_addr] = acc.get("owner")
    except RpcError:
        pass
    return out


def _owners_of(rpc: Rpc, token_accounts: list[str]) -> dict[str, str]:
    out = {}
    try:
        accs = rpc.multiple(token_accounts)
        for addr, acc in zip(token_accounts, accs):
            try:
                out[addr] = acc["data"]["parsed"]["info"]["owner"]
            except (KeyError, TypeError):
                pass
    except RpcError:
        pass
    return out


POOL_PROGRAMS = {
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # pumpswap
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # raydium amm v4
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C",  # raydium cpmm
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",   # orca
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",   # meteora dlmm
}


