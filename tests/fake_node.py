"""A stand-in for the node: answers RPC calls from a saved fixture, so tests need no live node."""
import copy
import json
import os

from watch.rpc import READ_ONLY, RpcRefused

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    """The list of saved calls in tests/fixtures/<name>.json."""
    with open(os.path.join(FIXTURES, name + ".json")) as f:
        return json.load(f)["calls"]


def changed(calls, method, params, result):
    """A copy of `calls` where the answer to one call is replaced: how a test plants a fault."""
    out = copy.deepcopy(calls)
    hits = [c for c in out if c["method"] == method and c["params"] == params]
    assert hits, f"{method} {params} is not in the fixture"
    for c in hits:
        c["result"] = result
    return out


def fake_rpc(calls):
    """An rpc(method, params=None) that answers from `calls`. A call that was not saved fails the test."""
    table = {(c["method"], json.dumps(c["params"])): c["result"] for c in calls}

    def rpc(method, params=None):
        if method not in READ_ONLY:
            raise RpcRefused(method)
        key = (method, json.dumps(params or []))
        if key not in table:
            raise AssertionError(f"not in the fixture: {method} {params or []}")
        return copy.deepcopy(table[key])
    return rpc
