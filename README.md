# level

A Grok teammate that **reads a pump.fun launch before you do.**

**Live:** [level-bot.tech](https://level-bot.tech) · X: [@level_grok](https://x.com/level_grok) · desk json: [state](https://api.level-bot.tech/state.json) · [reads](https://api.level-bot.tech/reads.json) · [tape](https://api.level-bot.tech/tape.json)

Paste a Solana mint. In about ten seconds the desk opens the pump.fun bonding curve (who created it,
how far along it is), names the top holders with the curve and the pools excluded, reads the creator
wallet (first seen, how busy, SOL balance, what it still holds), asks Dexscreener for the market, and
then **Grok reads X** for it through xAI's `x_search` tool: how many posts, how many accounts, how
many of them were created this month, how many repeat the same text, whether the project has an
account of its own, who with an audience mentioned it, five real quotes with their links.

The result is one card of **counts and quotes**. A line turns red when a count passes a published
threshold. **The card never says buy, sell, safe or rug.** It says what is there.

The desk also reads on its own: every pump.fun launch arrives on the tape the second it is created
(PumpPortal's free stream), and a launch that reaches $25K market cap while it is still a launch
(3 to 180 minutes old) gets read without anyone asking, up to 20 an hour and a daily Grok budget.

## What it is, and is not

- **Read-only.** The RPC client has an allow-list of read methods and refuses anything else before a
  request exists. There is no key pair anywhere in this repo and no code path that signs.
- **No price call.** No score, no rating, no target. Counts past thresholds that are printed on the
  site and in `agent/desk.py`.
- **Nothing about you is kept.** A read is not logged with who asked: no IP, no address, no time of
  the request. The card goes on the wall without you.
- **Grok is real, not a word.** Every X line on a card comes from an actual `x_search` call, and the
  card shows how many posts were fetched and what the call cost. When the daily budget is spent the
  card says so and shows chain facts only.
- **Never an invented post.** Grok is told to never invent a post; every quote carries its URL.
- **The token gates nothing.** Holding $LEVEL changes nothing on the site. It pays the Grok bill.

## Run it

    pip install -r requirements.txt
    cp config.example.toml config.toml
    export XAI_API_KEY=...                  # or leave it unset: chain facts only
    python -m agent doctor                  # what this machine can reach
    python -m agent read <mint>             # read one launch, print the card
    python -m agent run                     # the tape, the desk, http on 127.0.0.1:8793

    pytest                                  # the pure parts: base58, the pump pda, the curve layout, the lines, the http layer

A free Helius key makes the RPC side faster and more reliable than the public endpoint; put its url in
`rpc_url`. Nothing else needs a key.

## Deploy

    DOMAIN=api.example.com bash deploy/push.sh      # to the box, in one ssh session
    sudo bash /opt/level/app/deploy/key.sh          # on the box: the xAI key, typed blind, stored root-only in /etc/level.env

`deploy/install.sh` sets up a sandboxed `level` user and systemd service, a Caddy site for https, the
static site zipped for a Netlify drop at `/site.zip`, and the source over http at `/level.git`. It
adds its own Caddy site file, so it can share a box with other services.

## The card

    $FOGMACHINE  Fog Machine  4vCD7b…UQPR
    read in 11.2s, grok $0.160
       62.0% of the way to graduation on the bonding curve
    !! creator wallet is less than a day old (31 transactions)
       creator holds 1.2 SOL
    !! creator still holds 12.5% of supply
    !! top 10 wallets hold 58.3% (curve and pools excluded)
       market cap $41.2K · liquidity $11.8K · 24h volume $212.0K
       24h: 812 buys, 640 sells
     ! no website and no social links on the listing
       X: 14 posts from 4 accounts in 7 days
    !! 3 of 4 posting accounts were created in the last 30 days
    !! 9 posts repeat the same text
     ! no project account on X
     x: Fourteen posts from four accounts, three of them created this month, nine repeating the same text. ...

## Files the site reads

`state.json` (tape rate, reads, budget, which services are up), `reads.json` (the wall, newest
first), `tape.json` (the last 60 launches), `events.json` (the desk's journal). One POST: `/read`
with `{"mint": "..."}`, four a minute per IP, two at once.

## Layout

    agent/      chain.py (rpc reads, the pump pda, the curve layout, dexscreener), grok.py (xai responses + x_search),
                tape.py (pumpportal stream), desk.py (one read, the lines, the wall), main.py (the loop), server.py (http), __main__.py (cli)
    site/       static html + css + js, reads the desk's json; sample/ for local previews
    deploy/     install.sh, push.sh, key.sh, mint.sh, the sandboxed systemd unit, the Caddy site
    tests/      the pure parts
