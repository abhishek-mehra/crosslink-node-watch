"""Invariant checks: facts about a node that must always hold, checked against a rule or against the
previous run.

Usage, from the repository root: python3 -m watch.invariants [--config config.toml] [--quiet] [--accept]
  --quiet   print only FAIL lines
  --accept  you moved money out on purpose: stop bond_count / conservation failing over it
Meant to run every few minutes. Read-only, about 1 s per run. Exit code 1 when any check is FAIL.
The previous run is kept in <state_dir>/invariants.json.

The checks (a FAIL means "look at this", not "money lost"):
  finality_lag    tip minus finalized stays at or under LAG_MAX blocks
  bond_count      active bonds never drop unless some became withdrawable (an unbond you asked for)
  bond_total      with the same bonds, their total value never goes down (rewards only add)
  conservation    wallet + bonds never lose more than FEE_SLACK zats (fees only)
  on_main_branch  the block hash at each known_good height in the config equals the one given there

A drop in bond_count or conservation must still be there on the next run to be a FAIL (the line says
watching=1 meanwhile): a bond that was just sent is in no balance field until the wallet has read its
block, and a bond can leave the list for one block when the chain tip switches.

If the node does not answer any one call, the whole run is a single SKIP line and the previous run is kept:
a call that failed is never treated as a wrong answer.
"""
import argparse
import json
import os
import sys
import time

from watch.config import load
from watch.report import FAIL, PASS, SKIP, emit
from watch.rpc import NodeDown, RpcRefused, rpc

LAG_MAX = 50          # blocks; normal lag is 5-10
FEE_SLACK = 100_000   # zats a run may lose to fees
DROP_RUNS = 2         # runs in a row a drop must last before bond_count or conservation fails
WALLET_FIELDS = ("spendable_zats", "pending_zats", "committed_zats", "unshielded_zats")


def read(rpc, now=None):
    """One snapshot of the node: the numbers every check works from."""
    tip = rpc("getblockcount")
    final = (rpc("get_tfl_final_block_height_and_hash") or {}).get("height")
    funds = rpc("wallet_spendable_funds") or {}
    pos = rpc("wallet_staking_positions") or {}
    bonds = [x for v in (pos.get("active") or {}).values() for x in v]
    unbonded = [x for x in (pos.get("withdrawable") or []) if isinstance(x, dict)]
    bond_sum = sum(x["latest_val"] for x in bonds)
    wallet = sum(funds.get(k, 0) for k in WALLET_FIELDS)
    # An unbonded bond leaves "active" but is still the wallet's money, so it counts in the total.
    return {"time": time.time() if now is None else now, "height": tip, "final": final,
            "bond_n": len(bonds), "bond_sum": bond_sum, "withdrawable": len(pos.get("withdrawable") or []),
            "wallet": wallet, "total": wallet + bond_sum + sum(x.get("latest_val", 0) for x in unbonded)}


def finality_lag(snap):
    tip, final = snap["height"], snap["final"]
    if not isinstance(final, int):
        return FAIL, f"tip {tip}, the finality RPC gave no height"
    if tip - final > LAG_MAX:
        return FAIL, f"lag {tip - final} blocks is over the limit {LAG_MAX} (tip {tip}, finalized {final})"
    return PASS, f"tip={tip} final={final} lag={tip - final} limit={LAG_MAX}"


# bond_count and conservation remember the value from before a drop ("baseline", carried in the snapshot)
# and keep failing until the wallet is back at it, so a drop is not forgotten one run later.

def bond_count(snap, prev, base):
    n, withdrawable = snap["bond_n"], snap["withdrawable"]
    if prev.get("bond_n") is None:
        return PASS, f"bonds={n} first_run=1"
    held = "bond_n" in base
    ref_n = base["bond_n"] if held else prev["bond_n"]
    ref_w = base["bond_w"] if held else prev.get("withdrawable", 0)
    drop = ref_n - n
    if drop <= 0 or drop <= withdrawable - ref_w:
        for key in ("bond_n", "bond_w", "bond_runs"):
            base.pop(key, None)
        return PASS, f"bonds={n} withdrawable={withdrawable}"
    base["bond_n"], base["bond_w"], base["bond_runs"] = ref_n, ref_w, base.get("bond_runs", 0) + 1
    if base["bond_runs"] < DROP_RUNS:
        return PASS, f"bonds={n} was={ref_n} watching=1"
    return FAIL, (f"{n} active bonds, was {ref_n}; withdrawable {withdrawable}, was {ref_w}; "
                  f"for {base['bond_runs']} runs")


def bond_total(snap, prev):
    total = snap["bond_sum"]
    if prev.get("bond_sum") is None or prev.get("bond_n") != snap["bond_n"]:
        return PASS, f"bonded_zats={total} same_bonds=0"
    if total < prev["bond_sum"]:
        return FAIL, f"the same {snap['bond_n']} bonds are worth {total} zats, was {prev['bond_sum']}"
    return PASS, f"bonded_zats={total} same_bonds=1"


def conservation(snap, prev, base):
    total = snap["total"]
    if prev.get("total") is None:
        return PASS, f"total_zats={total} first_run=1"
    ref = base["total"] if "total" in base else prev["total"]
    change = total - ref
    if change >= -FEE_SLACK:
        base.pop("total", None)
        base.pop("total_runs", None)
        return PASS, f"total_zats={total} change={change}"
    base["total"], base["total_runs"] = ref, base.get("total_runs", 0) + 1
    if base["total_runs"] < DROP_RUNS:
        return PASS, f"total_zats={total} change={change} watching=1"
    return FAIL, f"wallet + bonds fell {-change} zats, from {ref} to {total}, for {base['total_runs']} runs"


def on_main_branch(rpc, final, known_good):
    if not known_good:
        return SKIP, "no known_good heights in the config"
    passed = sorted(h for h in known_good if isinstance(final, int) and h <= final)
    if not passed:
        return SKIP, f"none of the {len(known_good)} known_good heights is finalized yet"
    wrong = [h for h in passed if rpc("getblockhash", [h]) != known_good[h]]
    if wrong:
        return FAIL, f"wrong branch: the hash at {wrong} differs from known_good"
    return PASS, f"matching={len(passed)} of={len(known_good)}"


def run(rpc, prev, known_good=None, now=None):
    """All checks as (name, status, detail), and the snapshot to hand to the next run as `prev`.
    `rpc` is called as rpc(method, params=None); NodeDown from any call ends the run."""
    snap = read(rpc, now)
    base = dict(prev.get("baseline") or {})
    snap["baseline"] = base
    res = [("finality_lag", *finality_lag(snap)),
           ("bond_count", *bond_count(snap, prev, base)),
           ("bond_total", *bond_total(snap, prev)),
           ("conservation", *conservation(snap, prev, base)),
           ("on_main_branch", *on_main_branch(rpc, snap["final"], known_good or {}))]
    return res, snap


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check facts about the node that must always hold.")
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--quiet", action="store_true", help="print only FAIL lines")
    ap.add_argument("--accept", action="store_true", help="take the current balances as the new normal")
    args = ap.parse_args(argv)
    cfg = load(args.config)
    path = os.path.join(cfg["state_dir"], "invariants.json")
    try:
        with open(path) as f:
            prev = json.load(f)
    except (FileNotFoundError, ValueError):
        prev = {}
    if args.accept:
        dropped = prev.pop("baseline", None)
        print(f"accepted: cleared held baseline {dropped}" if dropped else "nothing was being held")
    try:
        results, snap = run(lambda method, params=None: rpc(cfg["node_url"], cfg["cookie_path"], method, params),
                            prev, {int(h): hash_ for h, hash_ in cfg["known_good"].items()})
    except NodeDown as e:       # keep the previous run: the next one compares with the last good snapshot
        results, snap = [("node", SKIP, f"node not answering ({str(e)[:80]})")], prev
    except RpcRefused:
        raise
    except Exception as e:      # an answer with an unexpected shape
        results, snap = [("shape", FAIL, f"{type(e).__name__}: {str(e)[:150]}")], prev
    os.makedirs(cfg["state_dir"], exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(snap, f, indent=1)
    os.replace(path + ".tmp", path)
    return emit("invariants", results, cfg["results_log"], args.quiet)


if __name__ == "__main__":
    sys.exit(main())
