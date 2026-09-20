"""Read-only GenLayer endpoint identity and block-height probe.

This command never signs or broadcasts a transaction. It intentionally uses
only JSON-RPC methods that are safe for endpoint verification.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _rpc(url: str, method: str, params: list[object]) -> object:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "proofline-read-only-probe/1.0",
        },
        method="POST",
    )
    with urlopen(request, timeout=20) as response:  # noqa: S310 - URL is operator supplied
        payload = json.loads(response.read().decode("utf-8"))
    if "error" in payload:
        raise RuntimeError(f"{method} returned RPC error {payload['error'].get('code')}")
    return payload.get("result")


def _int_hex(value: object, field: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise RuntimeError(f"{field} was not a hexadecimal JSON-RPC quantity")
    return int(value, 16)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a GenLayer RPC without writing")
    parser.add_argument("--rpc-url", default=os.getenv("GENLAYER_RPC_URL", ""))
    parser.add_argument("--chain-id", default=os.getenv("GENLAYER_CHAIN_ID", ""))
    args = parser.parse_args()
    if not args.rpc_url or not args.chain_id:
        parser.error("--rpc-url/GENLAYER_RPC_URL and --chain-id/GENLAYER_CHAIN_ID are required")
    try:
        expected = int(str(args.chain_id), 0)
        actual = _int_hex(_rpc(args.rpc_url, "eth_chainId", []), "eth_chainId")
        block = _int_hex(_rpc(args.rpc_url, "eth_blockNumber", []), "eth_blockNumber")
        result: dict[str, object] = {
            "rpc_url": args.rpc_url,
            "expected_chain_id": expected,
            "actual_chain_id": actual,
            "chain_id_matches": expected == actual,
            "latest_block": block,
            "writes_broadcast": False,
        }
        # gen_syncing is optional across hosted Studio versions. A method
        # error is reported as unavailable rather than treated as unhealthy.
        try:
            result["syncing"] = _rpc(args.rpc_url, "gen_syncing", [])
        except RuntimeError:
            result["syncing"] = "UNAVAILABLE"
        print(json.dumps(result, sort_keys=True))
        return 0 if expected == actual else 2
    except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"read-only network check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
