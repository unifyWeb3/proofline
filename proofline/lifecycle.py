"""Read-only GenLayer transaction lifecycle and receipt construction.

This module intentionally has no broadcast method. It consumes an injected
``GenLayerClient`` (or a test double), observes transaction status and execution
result, reads the finalized contract decision, and only then builds the
canonical ``proofline.receipt.v1`` object.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import inspect
from typing import Any, Mapping, Sequence

from .canonical import strict_json_loads
from .errors import ProtocolError
from .receipt import ProoflineDecision, ProoflineReceipt


@dataclass(frozen=True)
class TransactionSnapshot:
    transaction_reference: str
    protocol_status: str
    execution_result: str | None
    consensus_result: str | None
    appeal_status: str | None
    finality_observed: bool
    network: str
    chain_id: int
    raw: Mapping[str, Any]


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    value = getattr(value, "value", value)
    return str(value)


def _status_name(value: Any) -> str:
    value = _enum_value(value)
    if value is None:
        return "UNKNOWN"
    try:
        from genlayer_py.types import TRANSACTION_STATUS_NUMBER_TO_NAME

        if value in TRANSACTION_STATUS_NUMBER_TO_NAME:
            return _enum_value(TRANSACTION_STATUS_NUMBER_TO_NAME[value]) or "UNKNOWN"
    except ImportError:
        pass
    return value


def _execution_name(value: Any) -> str | None:
    value = _enum_value(value)
    if value is None:
        return None
    try:
        from genlayer_py.types import EXECUTION_RESULT_NUMBER_TO_NAME

        if value in EXECUTION_RESULT_NUMBER_TO_NAME:
            return _enum_value(EXECUTION_RESULT_NUMBER_TO_NAME[value])
    except ImportError:
        pass
    return value


def _execution_name_from_transaction(value: Mapping[str, Any]) -> str | None:
    raw = value.get(
        "tx_execution_result_name",
        value.get(
            "txExecutionResultName",
            value.get("tx_execution_result", value.get("txExecutionResult")),
        ),
    )
    return _execution_name(raw)


def _lifecycle_status_name(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    state = value.get("state")
    if state == "finalized":
        return "FINALIZED"
    if state == "canceled":
        return "CANCELED"
    if state == "decided":
        outcome = str(value.get("outcome", "")).lower()
        return {
            "accepted": "ACCEPTED",
            "undetermined": "UNDETERMINED",
            "validators_timeout": "VALIDATORS_TIMEOUT",
            "leader_timeout": "LEADER_TIMEOUT",
        }.get(outcome, "UNKNOWN")
    if state == "processing":
        phase = str(value.get("phase", "")).lower()
        return {
            "uninitialized": "UNINITIALIZED",
            "pending": "PENDING",
            "proposing": "PROPOSING",
            "committing": "COMMITTING",
            "revealing": "REVEALING",
            "appeal_revealing": "APPEAL_REVEALING",
            "appeal_committing": "APPEAL_COMMITTING",
            "leader_revealing": "LEADER_REVEALING",
        }.get(phase, "UNKNOWN")
    return None


def _transaction_status_name(value: Mapping[str, Any]) -> str:
    raw_status = value.get("status_name", value.get("status"))
    if raw_status is not None:
        return _status_name(raw_status)
    return _lifecycle_status_name(value.get("lifecycle")) or "UNKNOWN"


def _is_finalized(value: Mapping[str, Any]) -> bool:
    return _transaction_status_name(value) == "FINALIZED"


def _timestamp(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.isdigit():
        return int(text)
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return None
        return int(parsed.astimezone(timezone.utc).timestamp())
    except (TypeError, ValueError, OverflowError):
        return None


def _finality_timestamp(value: Mapping[str, Any]) -> int | None:
    """Return the protocol finalization timestamp when Studio exposes one.

    Consensus v0.6 currently reports the finalization observation under the
    authoritative RPC lifecycle record at
    ``consensus_history.current_monitoring.FINALIZED``.  The other accepted
    keys are explicit lifecycle fields used by alternate SDK shapes.  Vote,
    acceptance, creation, and moving observation timestamps are deliberately
    excluded.
    """
    for key in (
        "finalized_at",
        "finalization_timestamp",
        "timestamp_finalized",
        "finalized_timestamp",
    ):
        timestamp = _timestamp(value.get(key))
        if timestamp is not None:
            return timestamp
    history = value.get("consensus_history")
    if isinstance(history, Mapping):
        monitoring = history.get("current_monitoring")
        if isinstance(monitoring, Mapping):
            timestamp = _timestamp(monitoring.get("FINALIZED"))
            if timestamp is not None:
                return timestamp
    return None


def _read_uleb128(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ValueError("truncated calldata length")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return value, offset
        shift += 7


def _decode_calldata(data: bytes, offset: int = 0) -> tuple[Any, int]:
    code, offset = _read_uleb128(data, offset)
    kind = code & 0x7
    value = code >> 3
    if kind == 0:
        if code == 0:
            return None, offset
        if code == 8:
            return False, offset
        if code == 16:
            return True, offset
        raise ValueError("unsupported special calldata value")
    if kind == 1:
        return value, offset
    if kind == 2:
        return -value - 1, offset
    if kind in {3, 4}:
        end = offset + value
        if end > len(data):
            raise ValueError("truncated calldata value")
        raw = data[offset:end]
        return (raw if kind == 3 else raw.decode("utf-8")), end
    if kind == 5:
        result = []
        for _ in range(value):
            item, offset = _decode_calldata(data, offset)
            result.append(item)
        return result, offset
    if kind == 6:
        result: dict[str, Any] = {}
        previous = None
        for _ in range(value):
            key_length, offset = _read_uleb128(data, offset)
            end = offset + key_length
            if end > len(data):
                raise ValueError("truncated calldata key")
            key = data[offset:end].decode("utf-8")
            offset = end
            if previous is not None and previous >= key:
                raise ValueError("calldata map keys are not ordered")
            previous = key
            item, offset = _decode_calldata(data, offset)
            result[key] = item
        return result, offset
    raise ValueError("unsupported calldata type")


def _call_from_decoded(value: Any) -> tuple[str, list[Any]] | None:
    if not isinstance(value, Mapping):
        return None
    method = value.get("method", value.get("function_name", value.get("")))
    args = value.get("args")
    if not isinstance(method, str) or not isinstance(args, list):
        return None
    return method, args


def _transaction_call(value: Mapping[str, Any]) -> tuple[str, list[Any]] | None:
    for key in ("tx_data_decoded", "calldata_decoded"):
        decoded = _call_from_decoded(value.get(key))
        if decoded is not None:
            return decoded
    data = value.get("data")
    if not isinstance(data, Mapping):
        return None
    calldata_value = data.get("calldata")
    if isinstance(calldata_value, Mapping):
        decoded = _call_from_decoded(calldata_value.get("decoded"))
        if decoded is not None:
            return decoded
        readable = calldata_value.get("readable")
        if isinstance(readable, str):
            try:
                decoded = _call_from_decoded(strict_json_loads(readable))
            except Exception:  # noqa: BLE001 - try the encoded representation
                decoded = None
            if decoded is not None:
                return decoded
    if isinstance(calldata_value, str):
        try:
            raw = base64.b64decode(calldata_value, validate=True)
            decoded, offset = _decode_calldata(raw)
            if offset == len(raw):
                return _call_from_decoded(decoded)
        except Exception:  # noqa: BLE001 - provenance remains unavailable
            pass
    return None


def _transaction_effect_decisions(value: Mapping[str, Any]) -> list[ProoflineDecision]:
    """Decode canonical decisions returned by successful leader execution."""
    consensus_data = value.get("consensus_data")
    if not isinstance(consensus_data, Mapping):
        return []
    leader_receipts = consensus_data.get("leader_receipt")
    if not isinstance(leader_receipts, list):
        return []
    decisions: list[ProoflineDecision] = []
    for receipt in leader_receipts:
        if not isinstance(receipt, Mapping):
            continue
        result = receipt.get("result")
        if not isinstance(result, Mapping) or result.get("status") != "return":
            continue
        raw = result.get("raw")
        if not isinstance(raw, str):
            continue
        try:
            encoded = base64.b64decode(raw, validate=True)
        except (ValueError, TypeError):
            continue
        for offset in (0, 1):
            try:
                decoded, end = _decode_calldata(encoded, offset)
                if end != len(encoded) or not isinstance(decoded, str):
                    continue
                decision = ProoflineDecision.from_dict(strict_json_loads(decoded))
            except Exception:  # noqa: BLE001 - unavailable effect stays unverified
                continue
            decisions.append(decision)
            break
    return decisions


def _tx_reference(value: Any) -> str:
    if isinstance(value, bytes):
        return "0x" + value.hex()
    text = str(value)
    return text if text.startswith("0x") else text


class GenLayerLifecycleClient:
    """Observe transactions and construct receipts without broadcasting."""

    def __init__(self, sdk_client: Any, *, network: str | None = None, chain_id: int | None = None):
        self.sdk_client = sdk_client
        chain = getattr(sdk_client, "chain", None)
        self.network = network or str(getattr(chain, "name", "genlayer"))
        configured_chain_id = chain_id if chain_id is not None else getattr(chain, "id", None)
        if isinstance(configured_chain_id, bool) or not isinstance(configured_chain_id, int):
            raise ValueError("chain_id must be an integer")
        self.chain_id = configured_chain_id

    def inspect(self, transaction_reference: Any) -> TransactionSnapshot:
        reference = _tx_reference(transaction_reference)
        try:
            tx = self.sdk_client.get_transaction(transaction_hash=transaction_reference)
        except Exception as exc:  # noqa: BLE001 - preserve infrastructure boundary
            raise ProtocolError("transaction query failed") from exc
        if not isinstance(tx, Mapping):
            raise ProtocolError("transaction query returned no object")
        protocol_status = _transaction_status_name(tx)
        execution_result = _execution_name_from_transaction(tx)
        consensus_result = _enum_value(tx.get("result_name", tx.get("result")))
        appeal_status = _enum_value(tx.get("appeal_status"))
        if appeal_status is None and protocol_status in {
            "APPEAL_REVEALING", "APPEAL_COMMITTING", "READY_TO_FINALIZE"
        }:
            appeal_status = protocol_status
        return TransactionSnapshot(
            transaction_reference=reference,
            protocol_status=protocol_status,
            execution_result=execution_result,
            consensus_result=consensus_result,
            appeal_status=appeal_status,
            finality_observed=protocol_status == "FINALIZED",
            network=self.network,
            chain_id=self.chain_id,
            raw=tx,
        )

    def finalized_receipt(
        self,
        transaction_reference: Any,
        *,
        contract_address: str,
        decision_method: str = "get_job",
        decision_args: Sequence[Any] | None = None,
        observed_at: int | None = None,
    ) -> ProoflineReceipt:
        """Build a receipt only after finality and verified submit-call provenance."""
        reference = _tx_reference(transaction_reference)
        if not decision_args or not isinstance(decision_args[0], str):
            raise ProtocolError("decision readback needs an agreement id")
        try:
            from genlayer_py.types import ExecutionResult, TransactionHashVariant

            wait_for_receipt = self.sdk_client.wait_for_transaction_receipt
            parameters = inspect.signature(wait_for_receipt).parameters
            wait_kwargs: dict[str, Any] = {
                "transaction_hash": transaction_reference,
                "full_transaction": True,
            }
            if "wait_until" in parameters:
                wait_kwargs["wait_until"] = "finalized"
            else:
                try:
                    from genlayer_py.types import TransactionStatus

                    wait_kwargs["status"] = TransactionStatus.FINALIZED
                except ImportError:
                    wait_kwargs["status"] = "FINALIZED"
            tx = wait_for_receipt(**wait_kwargs)
            finalized_name = _transaction_status_name(tx) if isinstance(tx, Mapping) else "UNKNOWN"
            execution_name = _execution_name_from_transaction(tx) if isinstance(tx, Mapping) else None
            if finalized_name != "FINALIZED":
                raise ProtocolError("transaction did not reach FINALIZED")
            if execution_name != ExecutionResult.FINISHED_WITH_RETURN.value:
                raise ProtocolError("finalized transaction execution did not return successfully")
            if not isinstance(tx, Mapping):
                raise ProtocolError("transaction provenance is unavailable")
            target = tx.get("recipient", tx.get("to_address"))
            if not isinstance(target, str) or target.lower() != contract_address.lower():
                raise ProtocolError("transaction recipient does not match readback contract")
            call = _transaction_call(tx)
            if call is None:
                raise ProtocolError("transaction calldata is unavailable")
            method, args = call
            if method != "submit_job":
                raise ProtocolError("transaction did not submit a job")
            if not args or args[0] != decision_args[0]:
                raise ProtocolError("transaction job does not match decision readback")
            effect_decisions = _transaction_effect_decisions(tx)
            if not effect_decisions:
                raise ProtocolError("transaction decision effect is unavailable")
            decision_raw = self.sdk_client.read_contract(
                address=contract_address,
                function_name=decision_method,
                args=list(decision_args or []),
                transaction_hash_variant=TransactionHashVariant.LATEST_FINAL,
            )
        except ProtocolError:
            raise
        except Exception as exc:  # noqa: BLE001 - no application verdict on SDK failure
            raise ProtocolError("finalized transaction/readback failed") from exc
        try:
            if isinstance(decision_raw, (str, bytes)):
                decision_value = strict_json_loads(decision_raw)
            else:
                decision_value = decision_raw
            decision = ProoflineDecision.from_dict(decision_value)
        except Exception as exc:  # noqa: BLE001 - malformed contract state is technical
            raise ProtocolError("contract readback is not a canonical decision") from exc
        if decision.contract_address.lower() != contract_address.lower():
            raise ProtocolError("decision contract address does not match readback target")
        if decision.agreement_id != decision_args[0]:
            raise ProtocolError("decision job does not match readback")
        if decision.chain_id != self.chain_id:
            raise ProtocolError("decision chain_id does not match lifecycle client")
        if not any(effect.decision_digest == decision.decision_digest for effect in effect_decisions):
            raise ProtocolError("transaction decision effect does not match final readback")
        finalized_at = _finality_timestamp(tx) if isinstance(tx, Mapping) else None
        if finalized_at is None:
            try:
                observed_tx = self.sdk_client.get_transaction(transaction_hash=transaction_reference)
            except Exception:  # noqa: BLE001 - wait result remains the source of truth
                observed_tx = None
            if isinstance(observed_tx, Mapping):
                finalized_at = _finality_timestamp(observed_tx)
        return ProoflineReceipt._from_finalized_decision(
            decision,
            transaction_reference=reference,
            execution_result=execution_name,
            finalized_at=finalized_at,
        )
