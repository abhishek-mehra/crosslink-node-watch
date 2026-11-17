"""Contract checks: ask the node the same question two ways and report when the answers disagree.

Usage, from the repository root: python3 -m watch.contracts [--config config.toml] [--quiet]
  --quiet  print only FAIL lines
Read-only, about 2 s per run. Exit code 1 when any check is FAIL.

The five pairs:
  tip_height  getblockcount = getblockchaininfo.blocks = getbestblockheightandhash.height
  tip_hash    getbestblockhash = getblockhash(tip) = getblockchaininfo.bestblockhash
  finality    get_tfl_final_block_hash = getblockhash(final height) = get_tfl_final_block_height_and_hash.hash
  money       wallet_spendable_funds.unshielded_zats = getaddressbalance(mining address).balance
  bonds       every bond in wallet_staking_positions is found by getbondinfo with the same amount

A PASS line with known=N means the answers agree only after a difference already reported upstream as
issue N is undone. When a new node release drops the known=N, the fix is confirmed on this node.
"""
import argparse
import sys
import time

from watch.config import load
from watch.report import FAIL, PASS, SKIP, emit
from watch.rpc import NodeDown, RpcRefused, rpc

TRIES = 3           # a new block can land between two calls, so a mismatch is retried
BOND_CAP = 40        # bonds looked up per run
WALLET_LAG = 20      # s between retries of a wallet-vs-chain comparison
WALLET_AHEAD = 1e-5  # fraction a bond's chain amount may lead the wallet's: one block of rewards is ~3.2e-6
ISSUE_BYTE_ORDER = 203   # hashes returned as reversed byte arrays
ISSUE_BOND_PK = 309      # getbondinfo needs the pk byte-reversed


def as_hex(h):
    """A hash as hex text. Returns (text, was_byte_array). Byte arrays come in reversed order."""
    if isinstance(h, list):
        return bytes(h[::-1]).hex(), True
    return h, False


def tip_height(rpc):
    a, b, c = rpc("getblockcount"), rpc("getblockchaininfo")["blocks"], rpc("getbestblockheightandhash")["height"]
    if a == b == c:
        return PASS, f"height={a}"
    return FAIL, f"getblockcount {a}, getblockchaininfo {b}, getbestblockheightandhash {c}"


def tip_hash(rpc):
    best, info, hh = rpc("getbestblockhash"), rpc("getblockchaininfo"), rpc("getbestblockheightandhash")
    by_height = rpc("getblockhash", [hh["height"]])
    pair, was_bytes = as_hex(hh["hash"])
    if not (best == info["bestblockhash"] == by_height == pair):
        return FAIL, f"best {best[:16]}, chaininfo {info['bestblockhash'][:16]}, by height {by_height[:16]}, pair call {pair[:16]}"
    return PASS, f"hash={best[:16]}" + (f" known={ISSUE_BYTE_ORDER}" if was_bytes else "")


def finality(rpc):
    pair = rpc("get_tfl_final_block_height_and_hash")
    text, by_height = rpc("get_tfl_final_block_hash"), rpc("getblockhash", [pair["height"]])
    pair_hex, was_bytes = as_hex(pair["hash"])
    if not (text == by_height == pair_hex):
        return FAIL, f"final {pair['height']}: hash call {str(text)[:16]}, by height {by_height[:16]}, pair call {pair_hex[:16]}"
    return PASS, f"final={pair['height']} hash={text[:16]}" + (f" known={ISSUE_BYTE_ORDER}" if was_bytes else "")


def money(rpc, addr):
    wallet = rpc("wallet_spendable_funds")["unshielded_zats"]
    index = rpc("getaddressbalance", [{"addresses": [addr]}])["balance"]
    if wallet == index:
        return PASS, f"unshielded_zats={wallet}"
    return FAIL, f"wallet unshielded {wallet} zats, address index {index} zats"


def bonds(rpc):
    def listed():
        pos = rpc("wallet_staking_positions") or {}
        return {x["pk"]: x["latest_val"] for v in (pos.get("active") or {}).values() for x in v}
    before = listed()
    pks = sorted(before)[:BOND_CAP]
    if not pks:
        return SKIP, "no active bonds to look up"
    direct, missing, wrong = 0, [], []
    for pk in pks:
        info = rpc("getbondinfo", [pk])
        if info is not None:
            direct += 1
        else:
            info = rpc("getbondinfo", [bytes.fromhex(pk)[::-1].hex()])
        if info is None:
            missing.append(pk[:8])
        else:
            before[pk] = (before[pk], info["amount"])
    after = listed()   # rewards grow a bond every block, so the amount must sit between the two wallet readings
    for pk in pks:
        if isinstance(before[pk], tuple) and pk in after:
            lo, amount = before[pk]
            hi = max(lo, after[pk])
            # The wallet can trail the chain by a block for longer than the retries, so the chain may be
            # up to WALLET_AHEAD ahead.
            if not (min(lo, after[pk]) <= amount <= hi or 0 < amount - hi <= hi * WALLET_AHEAD):
                wrong.append(f"{pk[:8]} wallet {lo}..{after[pk]} chain {amount}")
    if missing or wrong:
        return FAIL, f"{len(pks)} bonds: not found {missing}, amount differs {wrong}"
    return PASS, f"bonds={len(pks)}" + (f" known={ISSUE_BOND_PK}" if direct < len(pks) else "")


def run(rpc, mining_address="", sleep=time.sleep):
    """All five checks as (name, status, detail). `rpc` is called as rpc(method, params=None)."""
    def retry(fn, wait=1):
        # the first result that is not FAIL, else the last
        for i in range(TRIES):
            out = fn()
            if out[0] != FAIL:
                return out
            if i < TRIES - 1:
                sleep(wait)
        return out

    res = [("tip_height", *retry(lambda: tip_height(rpc))),
           ("tip_hash", *retry(lambda: tip_hash(rpc))),
           ("finality", *retry(lambda: finality(rpc)))]
    if mining_address:
        res.append(("money", *retry(lambda: money(rpc, mining_address), wait=WALLET_LAG)))
    else:
        res.append(("money", SKIP, "mining_address is not set in the config"))
    # The wallet learns of a new block 10-15 s after the chain does, so its bond values trail getbondinfo
    # by one or two blocks of rewards for that long. Wait it out before FAIL.
    res.append(("bonds", *retry(lambda: bonds(rpc), wait=WALLET_LAG)))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cross-check pairs of node RPCs.")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--quiet", action="store_true", help="print only FAIL lines")
    args = ap.parse_args(argv)
    cfg = load(args.config)
    try:
        results = run(lambda method, params=None: rpc(cfg["node_url"], cfg["cookie_path"], method, params),
                      cfg["mining_address"])
    except NodeDown as e:
        results = [("node", SKIP, f"node not answering ({str(e)[:80]})")]
    except RpcRefused:
        raise
    except Exception as e:      # an answer with an unexpected shape is itself a contract failure
        results = [("shape", FAIL, f"{type(e).__name__}: {str(e)[:150]}")]
    return emit("contracts", results, cfg["results_log"], args.quiet)


if __name__ == "__main__":
    sys.exit(main())
