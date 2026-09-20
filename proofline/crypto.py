"""Ethereum-compatible signing helpers for the offchain boundary."""

from __future__ import annotations

from typing import Any

from .canonical import canonical_json
from .errors import SignatureError


def _account_api():
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except ImportError as exc:  # pragma: no cover - exercised in minimal installs
        raise SignatureError(
            "eth-account is required for signature creation or verification"
        ) from exc
    return Account, encode_defunct


def private_key_from_seed(seed: bytes) -> str:
    """Derive a deterministic *fixture-only* key from arbitrary seed bytes."""

    from hashlib import sha256

    return "0x" + sha256(seed).hexdigest()


def address_from_private_key(private_key: str) -> str:
    Account, _ = _account_api()
    return Account.from_key(private_key).address


def sign_payload(payload: Any, private_key: str) -> str:
    Account, encode_defunct = _account_api()
    message = encode_defunct(text=canonical_json(payload))
    value = Account.sign_message(message, private_key).signature.hex()
    return value if value.startswith("0x") else "0x" + value


def recover_address(payload: Any, signature: str) -> str:
    Account, encode_defunct = _account_api()
    try:
        message = encode_defunct(text=canonical_json(payload))
        return Account.recover_message(message, signature=signature)
    except Exception as exc:  # noqa: BLE001 - normalize library errors
        raise SignatureError("signature could not be recovered") from exc


def verify_signature(payload: Any, signature: str, expected_address: str) -> bool:
    try:
        recovered = recover_address(payload, signature)
    except SignatureError:
        return False
    return recovered.lower() == expected_address.lower()
