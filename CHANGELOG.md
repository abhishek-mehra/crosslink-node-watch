# Changelog

## 0.2.0 (not released yet)

- `meters`: memory, threads, open files and uptime of the node process, the size of its data folder and
  write-ahead logs, free disk, and a `FAIL` when one doubles within 6 hours. Linux and macOS.

## 0.1.0 (2026-11-17)

- Read-only RPC guard: a method that is not on the allow-list is refused before anything is sent.
- `contracts`: five cross-checks between pairs of RPCs (`tip_height`, `tip_hash`, `finality`, `money`, `bonds`).
- `invariants`: five checks against a rule or the previous run (`finality_lag`, `bond_count`, `bond_total`,
  `conservation`, `on_main_branch`).
- One output line per check: time, name, `PASS` / `FAIL` / `SKIP`, detail.
- Tests that run without a node, from saved node replies.
