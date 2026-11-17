# crosslink-node-watch

[![tests](https://github.com/abhishek-mehra/crosslink-node-watch/actions/workflows/tests.yml/badge.svg)](https://github.com/abhishek-mehra/crosslink-node-watch/actions/workflows/tests.yml)

Read-only checks for a [Crosslink](https://github.com/ShieldedLabs/crosslink_monolith) feature-net node. It asks
the node questions over its RPC and prints one line per check: `PASS`, `FAIL` or `SKIP`.

It cannot send coins, stake or stop the node. Python 3.11 or newer, standard library only, Linux or macOS.

## What it does

Two groups of checks so far:

| Group | Question it answers | Checks |
|---|---|---|
| `contracts` | Does the node give the same answer when asked the same thing two ways? | `tip_height`, `tip_hash`, `finality`, `money`, `bonds` |
| `invariants` | Does a fact that must always hold still hold, compared with a rule or with the previous run? | `finality_lag`, `bond_count`, `bond_total`, `conservation`, `on_main_branch` |

```
$ python3 -m watch.contracts
2026-11-17 03:05:24 contracts.tip_height PASS height=166912
2026-11-17 03:05:24 contracts.tip_hash PASS hash=b0da826511c7fcd5 known=203
2026-11-17 03:05:24 contracts.finality PASS final=166907 hash=315515eca689eef0 known=203
2026-11-17 03:05:24 contracts.money SKIP mining_address is not set in the config
2026-11-17 03:05:24 contracts.bonds PASS bonds=40 known=309

$ python3 -m watch.invariants
2026-11-17 08:40:24 invariants.finality_lag PASS tip=167697 final=167691 lag=6 limit=50
2026-11-17 08:40:24 invariants.bond_count PASS bonds=3 withdrawable=0
2026-11-17 08:40:24 invariants.bond_total PASS bonded_zats=175067443 same_bonds=1
2026-11-17 08:40:24 invariants.conservation PASS total_zats=725067443 change=160
2026-11-17 08:40:24 invariants.on_main_branch PASS matching=3 of=3
```

- `PASS` with `known=203` means the answers agree only after undoing a difference already reported upstream
  as issue 203. When a new node release drops the `known=`, the fix is confirmed on your node.
- `PASS` with `watching=1` means a balance or bond count dropped on this run. It becomes `FAIL` only if the
  drop is still there on the next run (a bond that was just sent is in no balance field for a block or two).
- `SKIP` means the check could not run (the node did not answer, or a setting is empty). A call that failed is
  never treated as a wrong answer.
- The exit code is 1 when any check is `FAIL`, so a shell script or cron job can react.

Each check is described at the top of [`watch/contracts.py`](watch/contracts.py) and
[`watch/invariants.py`](watch/invariants.py).

## How it does it

- **One door to the node.** [`watch/rpc.py`](watch/rpc.py) is the only file that talks to the node. It holds a
  list of RPC methods that only read. Any other method is refused before the cookie is read or anything is
  sent, so a mistake in a check cannot move money.
- **Checks are plain functions.** A check gets a function to call the node with and returns a status and a
  short detail. It does not know where its answers come from.
- **`invariants` remembers one run.** It saves a small snapshot (tip, bond count, totals) in
  `state/invariants.json` and compares the next run with it.
- **Tests need no node.** `tests/fixtures/` holds real replies saved from a node, with the wallet side made
  up. Each check must pass on them and must fail on a copy with one answer altered.

  ```
  python3 -m unittest discover tests
  ```

The reasons behind these choices, and what was left out, are in [`DESIGN.md`](DESIGN.md).

## Quick start

```
git clone https://github.com/abhishek-mehra/crosslink-node-watch
cd crosslink-node-watch
cp config.example.toml config.toml      # then set cookie_path for your system
python3 -m watch.contracts
python3 -m watch.invariants
```

`config.toml` has the node address, the cookie path, and three optional settings (your mining address, a file
to append results to, and known-good block hashes for `on_main_branch`). It is not committed.

To run the checks on a schedule, for example every 5 minutes with cron:

```
*/5 * * * * cd /path/to/crosslink-node-watch && python3 -m watch.invariants --quiet
```

`--quiet` prints only `FAIL` lines. Set `results_log` in the config to keep every line.

## Status

Version 0.1.0. Written against Crosslink v17 on the Round 3 feature net. The RPC interface changes between
releases; a check that meets an answer in a new shape reports `FAIL shape` and does not guess.

Not included: alerts, dashboards, staking or any other action on the node. Planned next: process meters over
time (memory, threads, open files) and a small status page.

## Licence

Licensed under either of [Apache License, Version 2.0](LICENSE-APACHE) or [MIT license](LICENSE-MIT), at your
option.
