"""The only file that talks to the node.

Every check calls rpc() from here. A method that is not in READ_ONLY is refused before anything is sent,
so no check can send coins, stake or stop the node, even by mistake.
"""
import base64
import json
import urllib.request

# Methods that only read. Adding a name here is a deliberate act: read what the method does first.
READ_ONLY = frozenset({
    # any Zebra node
    "getaddressbalance",
    "getbestblockhash",
    "getbestblockheightandhash",
    "getblock",
    "getblockchaininfo",
    "getblockcount",
    "getblockhash",
    "getmininginfo",
    "getpeerinfo",
    "getrawmempool",
    "getrawtransaction",
    # Crosslink only
    "get_tfl_final_block_hash",
    "get_tfl_final_block_height_and_hash",
    "getbondinfo",
    "wallet_spendable_funds",
    "wallet_staking_positions",
})


class RpcRefused(Exception):
    """The method is not on the read-only list. Nothing was sent to the node."""


class NodeDown(Exception):
    """The node did not answer, or answered with an error."""


def rpc(url, cookie_path, method, params=None, timeout=20):
    if method not in READ_ONLY:
        raise RpcRefused(f"{method}: not on the read-only list in watch/rpc.py; nothing was sent")
    try:
        with open(cookie_path) as f:
            auth = base64.b64encode(f.read().strip().encode()).decode()
        req = urllib.request.Request(
            url,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode(),
            headers={"content-type": "application/json", "authorization": "Basic " + auth})
        reply = json.load(urllib.request.urlopen(req, timeout=timeout))
    except Exception as e:
        raise NodeDown(f"{method}: {e}")
    if reply.get("error"):
        raise NodeDown(f"{method}: {reply['error']}")
    return reply.get("result")
