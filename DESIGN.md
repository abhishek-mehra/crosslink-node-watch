# Design decisions

One entry per decision: what was chosen, what was not, and why. Newest at the bottom.

## 1. Scope of version 1 (2026-11-15)

**Chosen:** read-only checks only: process meters over time, checks over history (invariants), cross-checks
between two RPCs (contracts), and a status page for a phone.

**Not chosen:** auto-staking and the start/stop launcher (they move money and restart nodes; a bug would hurt a
stranger). Alerts (not needed). A one-screen live status and Grafana dashboards (others have them: crosslink-check,
crosslink-tui, the node's own observability folder). Network-wide miner share (ctaz.cash shows it).

**Why:** a search on 2026-11-15 found no public tool doing these four things, and all existing tools assume Linux.

## 2. Settings (2026-11-15)

**Chosen:** one config file (`config.toml`, with `config.example.toml` in the repository): node address, cookie
path, log folder, mining address. Nothing personal in the code.

**Not chosen:** environment variables only (simpler for the author, harder for a new user).

## 3. Where it lives (2026-11-15)

**Chosen:** a new, separate folder and repository. Files are copied in and cleaned. The running node setup is not
touched.

**Not chosen:** turning the node's working folder into the repository (it holds logs, a wallet backup and drafts).

## 4. Name (2026-11-15)

`crosslink-node-watch`.

## 5. Language (2026-11-15)

**Chosen:** Python (standard library only) for version 1. One later part in Rust: the contract checks, as a
separate small command-line tool. Shell scripts are folded into Python over time.

**Not chosen:** rewriting everything in Rust first (delays the first release by months).

## 6. Structure (2026-11-15)

```
config.example.toml      settings, nothing personal
watch/
  rpc.py                 the only file that talks to the node
  meters.py              one file per check
  invariants.py
  contracts.py
  status.py
crosslink/               checks that need Crosslink (add-on)
tests/                   saved node answers, no live node needed
DESIGN.md  README.md  LICENSE
```

**Why:** only `rpc.py` talks to the node, so there is one place to enforce "read-only" and one place to change
when the node's interface changes. Checks that work on any Zebra node are kept apart from Crosslink-only ones,
so the project keeps its value if Crosslink does not ship.

## 7. Safety (2026-11-15)

**Chosen:** an allow-list. `watch/rpc.py` holds the names of the RPC methods that only read (`READ_ONLY`). Any
other name raises `RpcRefused` before the cookie is read or anything is sent, and the check stops with that error.

**Not chosen:** a block-list of known dangerous names (a dangerous method added by a future node version would
pass). Trusting each script to be careful (one mistake can move money).

**Why:** this is a watching tool; it has no reason to change anything. `tests/test_rpc.py` checks the guard
without a node.

## 8. Output contract (2026-11-15)

**Chosen:** every check prints one line per result: time, check name, `PASS` / `FAIL` / `SKIP`, then either a
short reason or metrics as `name=value` pairs.

```
2026-11-15 13:42:03 invariants.finality_lag PASS lag=7 limit=10
2026-11-15 13:42:05 contracts.tip_hash FAIL getbestblockhash and getblock disagree
```

**Not chosen:** each check printing in its own way (every reader then needs separate code per check).

**Why:** the checks are the product; the log of these lines is the record. The status page, a chart or `grep`
are all just readers of the same lines, so a new check needs no change anywhere else. Existing scripts adopt
the shape as they are copied in, not before.

## 9. Testing without a live node (2026-11-17)

**Chosen:** saved answers. The node's real replies are written once as JSON in `tests/fixtures/`. A test hands
them to the check in place of the node (`tests/fake_node.py`). For every check there is also a copy with one
answer altered, and the check must report `FAIL` on it.

**Not chosen:** a small fake node server that replays the answers (more code for little gain now). Testing only
against a live node (a stranger, or an automatic test run, has no node).

**Why:** anyone can run the tests in a second with nothing installed, and the altered copies show that a check
can actually catch a fault, not only that it runs.

**Rules for fixtures:** heights, block hashes and reply shapes are real; anything from a wallet (address, bond
keys, amounts) is made up. The fake node refuses any method that is not on the read-only list, and fails the
test on a call that was not saved. Fixtures are named by node version (`contracts_v17.json`), so a new release
gets a new file and the old one stays as a record.

**Follows from 8:** a difference already reported upstream is `PASS` with `known=<issue number>` in the line,
not a fourth status.

## 10. Invariant checks (2026-11-17)

**Chosen for version 1:** the five checks that need only RPC: `finality_lag`, `bond_count`, `bond_total`,
`conservation`, `on_main_branch`. The known-good block hashes for `on_main_branch` are in the config file.

**Not chosen for version 1:** the two checks that read the node's log (`tip_vs_peers`, `mined_onchain`). Only
`rpc.py` talks to the node (decision 6), and log lines change between releases. They can come back later
through one log-reading file. Notifications: alerts are out of scope (decision 1).

**A failed call is never a wrong answer.** If the node does not answer any one call, the whole run is one
`SKIP` line and the previous run is kept. Why: on 2026-11-13 the old script reported "wrong branch" because
the node was being stopped between two calls and "no answer" was compared with a hash.

**The previous run** is a snapshot in `<state_dir>/invariants.json`. `run()` takes the old snapshot and
returns the new one, so a test can hand it any history.

**A drop must last two runs to fail** (`conservation` and `bond_count`). A bond that was just sent is in no
balance field until the wallet has read its block. The old script excused this by reading a private staking
log; a stranger has no such log, so the rule is time instead. First written with `bond_count` failing at once
("bonds do not vanish in flight"); corrected the same day, when a 3-second sampler showed a bond leaving the
list for 27 s (178, 177, 178) around a tip switch.

**Shared output:** `watch/report.py` prints the decision-8 line for every check group.

## 11. Documentation (2026-11-17)

**Chosen:** one README that tells a stranger two things first: what the tool does and how it does it. Then a
quick start. Each check is described once, at the top of its own file, and the README links there.

**Not chosen:** a separate guide or a documentation site (nothing to fill it with yet).

## 12. Licence and version (2026-11-17)

**Chosen:** MIT or Apache-2.0, at the user's option, the same as Zebra and most Rust and Zcash projects, so
code can move between this project and theirs without a licence question. Version 0.1.0, with a changelog.

**Not chosen:** GPL (anyone reusing a check would have to publish their whole tool under it). No licence at
all (legally nobody may use the code).

## 13. Meters (2026-11-17)

**Chosen:** Linux and macOS. Most node runners use Linux; the author runs macOS. The readers are separate
(`/proc` on Linux; `ps`, `top`, `lsof` on macOS) and give the same names, except `footprint_mb`, which exists
only on macOS, where `rss` leaves out compressed memory (a 6 GB node shows 1 GB of rss).

**The node process is found by its RPC port**, not by a program name: the binary is renamed at every release.

**Meters kept:** memory, threads, open files, uptime, data folder size, write-ahead-log size, free disk.
**Dropped:** swap (the whole machine, not the node) and a crash count (it read a private watcher log).

**The growth rule** is "at least twice the value of 6 hours ago", both numbers in the config. A restart in
between, or an old reading taken in the first 30 minutes of a process, is not compared.

**History is the results log.** The meter reads its own earlier lines back (decision 8: the log is the
record), so there is no second file and no state to corrupt.

**An exception to decision 6:** `meters` does not go through `rpc.py`, because it makes no RPC call at all.
It only reads; it starts `ps`, `top`, `lsof` or `ss` and walks one folder.

**Not tested where it matters most:** the Linux readers run on GitHub's Linux runners against the test's own
process, and the `ss` parser on the documented format. Nobody has yet run it beside a node on Linux.

