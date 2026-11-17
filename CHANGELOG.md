# Changelog

## 0.1.0 (not released yet)

- Read-only RPC guard: a method that is not on the allow-list is refused before anything is sent.
- `contracts`: five cross-checks between pairs of RPCs (`tip_height`, `tip_hash`, `finality`, `money`, `bonds`).
- `invariants`: five checks against a rule or the previous run (`finality_lag`, `bond_count`, `bond_total`,
  `conservation`, `on_main_branch`).
- One output line per check: time, name, `PASS` / `FAIL` / `SKIP`, detail.
- Tests that run without a node, from saved node replies.
