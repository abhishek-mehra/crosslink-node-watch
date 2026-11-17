"""The invariant checks, run against saved node answers and a saved previous run. Each check must pass on
the real answers and fail when one answer is altered. Run from the repository root:
python3 -m unittest discover tests"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fake_node import changed, fake_rpc, load
from watch import invariants
from watch.report import FAIL, PASS, SKIP
from watch.rpc import NodeDown

KNOWN_GOOD = {72015: "00000a27eacb68e9e9846c6bb6927a31e712a64d1b133925ebe0968eadad1bc9",
              125202: "000055cc297557ecec82269320a987d4431a16e05e6f86878c9ef90028eda120",
              129374: "0000572e33977902282ee74b2753178c6221705e06a28412892c48445f7dd5f7"}
TIP, FINAL = 166907, 166902
ALL = ["finality_lag", "bond_count", "bond_total", "conservation", "on_main_branch"]


def run(calls, prev, known_good=KNOWN_GOOD):
    """({name: (status, detail)}, snapshot)."""
    res, snap = invariants.run(fake_rpc(calls), prev, known_good, now=0)
    return {name: (status, detail) for name, status, detail in res}, snap


def failing(res):
    return [name for name, (status, _) in res.items() if status == FAIL]


def with_wallet(calls, **fields):
    funds = fake_rpc(calls)("wallet_spendable_funds")
    return changed(calls, "wallet_spendable_funds", [], {**funds, **fields})


def with_bonds(calls, edit):
    """`edit` gets the list of active bonds and the withdrawable list, and changes them in place."""
    pos = fake_rpc(calls)("wallet_staking_positions")
    edit(next(iter(pos["active"].values())), pos["withdrawable"])
    return changed(calls, "wallet_staking_positions", [], pos)


class OnRealAnswers(unittest.TestCase):
    def setUp(self):
        self.calls = load("invariants_v17")

    def test_first_run_passes(self):
        res, _ = run(self.calls, {})
        self.assertEqual({name: status for name, (status, _) in res.items()}, dict.fromkeys(ALL, PASS))
        self.assertIn("first_run=1", res["bond_count"][1])

    def test_second_run_with_the_same_answers_passes(self):
        _, snap = run(self.calls, {})
        res, _ = run(self.calls, snap)
        self.assertEqual(failing(res), [])
        self.assertIn("same_bonds=1", res["bond_total"][1])

    def test_rewards_growing_a_bond_pass(self):
        _, snap = run(self.calls, {})
        grown = with_bonds(self.calls, lambda active, _: active[0].update(latest_val=active[0]["latest_val"] + 160))
        self.assertEqual(failing(run(grown, snap)[0]), [])


class OnAlteredAnswers(unittest.TestCase):
    def setUp(self):
        self.calls = load("invariants_v17")
        _, self.prev = run(self.calls, {})

    def test_finality_60_blocks_behind(self):
        late = changed(self.calls, "get_tfl_final_block_height_and_hash", [], {"height": TIP - 60, "hash": [0] * 32})
        self.assertEqual(failing(run(late, self.prev)[0]), ["finality_lag"])

    def test_finality_rpc_gives_no_height(self):
        res, _ = run(changed(self.calls, "get_tfl_final_block_height_and_hash", [], {}), self.prev)
        self.assertEqual(failing(res), ["finality_lag"])
        self.assertEqual(res["on_main_branch"][0], SKIP)

    def test_a_bond_that_disappears_is_watched_then_fails(self):
        gone = with_bonds(self.calls, lambda active, _: active.pop())
        res, snap = run(gone, self.prev)
        self.assertEqual(failing(res), [])
        self.assertIn("watching=1", res["bond_count"][1])
        res, _ = run(gone, snap)
        self.assertEqual(failing(res), ["bond_count", "conservation"])   # its value is gone too

    def test_a_bond_gone_for_one_run_never_fails(self):
        # a tip switch drops the newest bond from the list until the next block (seen Nov 17 2026)
        _, snap = run(with_bonds(self.calls, lambda active, _: active.pop()), self.prev)
        res, snap = run(self.calls, snap)
        self.assertEqual(failing(res), [])
        self.assertEqual(snap["baseline"], {})

    def test_a_missing_bond_keeps_failing_until_it_is_back(self):
        gone = with_bonds(self.calls, lambda active, _: active.pop())
        _, snap = run(gone, self.prev)
        _, snap = run(gone, snap)
        res, snap = run(gone, snap)
        self.assertIn("bond_count", failing(res))
        self.assertEqual(failing(run(self.calls, snap)[0]), [])

    def test_a_bond_that_became_withdrawable_passes(self):
        unbonded = with_bonds(self.calls, lambda active, withdrawable: withdrawable.append(active.pop()))
        self.assertEqual(failing(run(unbonded, self.prev)[0]), [])

    def test_the_same_bonds_lose_value(self):
        less = with_bonds(self.calls, lambda active, _: active[0].update(latest_val=active[0]["latest_val"] - 1))
        self.assertEqual(failing(run(less, self.prev)[0]), ["bond_total"])

    def test_wrong_hash_at_a_known_good_height(self):
        forked = changed(self.calls, "getblockhash", [129374], "00" * 32)
        self.assertEqual(failing(run(forked, self.prev)[0]), ["on_main_branch"])


class Conservation(unittest.TestCase):
    def setUp(self):
        self.calls = load("invariants_v17")
        _, self.prev = run(self.calls, {})
        self.lower = with_wallet(self.calls, spendable_zats=200000000)   # 1 coin gone

    def test_fees_pass(self):
        res, _ = run(with_wallet(self.calls, spendable_zats=300000000 - 30000), self.prev)
        self.assertEqual(res["conservation"][0], PASS)
        self.assertNotIn("watching", res["conservation"][1])

    def test_a_drop_is_watched_on_the_first_run(self):
        res, _ = run(self.lower, self.prev)
        self.assertEqual(failing(res), [])
        self.assertIn("watching=1", res["conservation"][1])

    def test_a_drop_that_lasts_fails_on_the_second_run(self):
        _, snap = run(self.lower, self.prev)
        res, _ = run(self.lower, snap)
        self.assertEqual(failing(res), ["conservation"])

    def test_a_drop_that_comes_back_never_fails(self):
        # a bond in flight: out of the wallet on one run, listed as a bond on the next
        _, snap = run(self.lower, self.prev)
        res, snap = run(self.calls, snap)
        self.assertEqual(failing(res), [])
        self.assertEqual(snap["baseline"], {})

    def test_a_lasting_drop_fails_until_accepted(self):
        _, snap = run(self.lower, self.prev)
        _, snap = run(self.lower, snap)
        self.assertEqual(failing(run(self.lower, snap)[0]), ["conservation"])
        snap.pop("baseline")                                   # what --accept does
        self.assertEqual(failing(run(self.lower, snap)[0]), [])


class OtherCases(unittest.TestCase):
    def setUp(self):
        self.calls = load("invariants_v17")

    def test_no_known_good_heights_skips(self):
        self.assertEqual(run(self.calls, {}, known_good={})[0]["on_main_branch"][0], SKIP)

    def test_known_good_height_not_finalized_yet_is_not_looked_up(self):
        res, _ = run(self.calls, {}, known_good={**KNOWN_GOOD, FINAL + 1: "00" * 32})
        self.assertEqual(res["on_main_branch"], (PASS, "matching=3 of=4"))

    def test_a_call_that_fails_ends_the_run_and_is_not_a_wrong_answer(self):
        # The node stops between two calls (seen Nov 13 2026): no result, no new snapshot.
        answers = fake_rpc(self.calls)

        def stopping(method, params=None):
            if method == "getblockhash":
                raise NodeDown("getblockhash: connection refused")
            return answers(method, params)
        with self.assertRaises(NodeDown):
            invariants.run(stopping, {}, KNOWN_GOOD, now=0)

    def test_the_previous_run_is_not_changed(self):
        _, prev = run(self.calls, {})
        before = copy.deepcopy(prev)
        run(with_bonds(self.calls, lambda active, _: active.pop()), prev)
        self.assertEqual(prev, before)


if __name__ == "__main__":
    unittest.main()
