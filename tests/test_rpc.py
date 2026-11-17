"""The read-only guard, tested without a node. Run from the repository root: python3 -m unittest discover tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from watch.rpc import READ_ONLY, NodeDown, RpcRefused, rpc

# The address and cookie path do not exist, so any call that gets past the guard fails with NodeDown.
NOWHERE = ("http://127.0.0.1:1", "/nonexistent/cookie")

CHANGES_STATE = ["stop", "generate", "generatetoaddress", "addnode", "requestfaucetdonation",
                 "sendrawtransaction", "submitblock", "wallet_basic_send", "wallet_staking_action"]


class ReadOnlyGuard(unittest.TestCase):
    def test_state_changing_methods_are_refused(self):
        for method in CHANGES_STATE:
            with self.assertRaises(RpcRefused, msg=method):
                rpc(*NOWHERE, method)

    def test_unknown_method_is_refused(self):
        with self.assertRaises(RpcRefused):
            rpc(*NOWHERE, "some_method_added_in_v19")

    def test_refusal_comes_before_the_cookie_is_read(self):
        # RpcRefused, not NodeDown: the guard runs before the cookie file is opened.
        with self.assertRaises(RpcRefused):
            rpc(*NOWHERE, "stop")

    def test_allowed_method_passes_the_guard(self):
        with self.assertRaises(NodeDown):
            rpc(*NOWHERE, "getblockcount")

    def test_no_state_changing_name_is_on_the_list(self):
        self.assertFalse(READ_ONLY & set(CHANGES_STATE))


if __name__ == "__main__":
    unittest.main()
