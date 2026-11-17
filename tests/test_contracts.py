"""The contract checks, run against saved node answers. Each check must pass on the real answers and
fail when one answer is altered. Run from the repository root: python3 -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fake_node import changed, fake_rpc, load
from watch import contracts
from watch.contracts import FAIL, PASS, SKIP

ADDR = "tmExampleMiningAddress0000000000000"
TIP, FINAL = 166907, 166902
OTHER_HASH = "00" * 32


def run(calls, addr=ADDR):
    """Results as {name: (status, detail)}. Retries do not wait."""
    return {name: (status, detail) for name, status, detail in
            contracts.run(fake_rpc(calls), addr, sleep=lambda s: None)}


class OnRealAnswers(unittest.TestCase):
    def setUp(self):
        self.res = run(load("contracts_v17"))

    def test_every_check_passes(self):
        self.assertEqual({name: status for name, (status, _) in self.res.items()},
                         dict.fromkeys(["tip_height", "tip_hash", "finality", "money", "bonds"], PASS))

    def test_v17_differences_are_marked_known(self):
        self.assertIn("known=203", self.res["tip_hash"][1])
        self.assertIn("known=203", self.res["finality"][1])
        self.assertIn("known=309", self.res["bonds"][1])
        self.assertNotIn("known", self.res["tip_height"][1])


class OnAlteredAnswers(unittest.TestCase):
    def setUp(self):
        self.calls = load("contracts_v17")

    def only_fails(self, calls, name):
        res = run(calls)
        self.assertEqual([n for n, (status, _) in res.items() if status == FAIL], [name])

    def test_tip_height_off_by_one(self):
        self.only_fails(changed(self.calls, "getblockcount", [], TIP - 1), "tip_height")

    def test_tip_hash_differs(self):
        self.only_fails(changed(self.calls, "getbestblockhash", [], OTHER_HASH), "tip_hash")

    def test_final_hash_differs(self):
        self.only_fails(changed(self.calls, "get_tfl_final_block_hash", [], OTHER_HASH), "finality")

    def test_address_index_misses_a_coin(self):
        self.only_fails(changed(self.calls, "getaddressbalance", [{"addresses": [ADDR]}], {"balance": 0}), "money")

    def test_bond_missing_from_chain(self):
        lookup = next(c for c in self.calls if c["method"] == "getbondinfo" and c["result"])
        self.only_fails(changed(self.calls, "getbondinfo", lookup["params"], None), "bonds")

    def test_bond_amount_differs(self):
        lookup = next(c for c in self.calls if c["method"] == "getbondinfo" and c["result"])
        half = {**lookup["result"], "amount": lookup["result"]["amount"] // 2}
        self.only_fails(changed(self.calls, "getbondinfo", lookup["params"], half), "bonds")


class OtherCases(unittest.TestCase):
    def setUp(self):
        self.calls = load("contracts_v17")

    def test_bond_one_block_of_rewards_ahead_passes(self):
        lookup = next(c for c in self.calls if c["method"] == "getbondinfo" and c["result"])
        ahead = {**lookup["result"], "amount": lookup["result"]["amount"] + 100}
        self.assertEqual(run(changed(self.calls, "getbondinfo", lookup["params"], ahead))["bonds"][0], PASS)

    def test_no_mining_address_skips_money(self):
        self.assertEqual(run(self.calls, addr="")["money"][0], SKIP)

    def test_no_bonds_skips_bonds(self):
        empty = changed(self.calls, "wallet_staking_positions", [], {"active": {}, "withdrawable": []})
        self.assertEqual(run(empty)["bonds"][0], SKIP)

    def test_fixed_node_has_no_known_marks(self):
        # A node with issues 203 and 309 fixed: hashes as hex text, bonds found by the pk as listed.
        calls = changed(self.calls, "getbestblockheightandhash", [],
                        {"height": TIP, "hash": fake_rpc(self.calls)("getbestblockhash")})
        calls = changed(calls, "get_tfl_final_block_height_and_hash", [],
                        {"height": FINAL, "hash": fake_rpc(self.calls)("get_tfl_final_block_hash")})
        for c in self.calls:
            if c["method"] == "getbondinfo" and c["result"]:
                listed_pk = bytes.fromhex(c["params"][0])[::-1].hex()
                calls = changed(calls, "getbondinfo", [listed_pk], c["result"])
        res = run(calls)
        for name, (status, detail) in res.items():
            self.assertEqual(status, PASS, name)
            self.assertNotIn("known", detail, name)


if __name__ == "__main__":
    unittest.main()
