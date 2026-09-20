"""Adapter-neutral downstream signals and an idempotent local adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from .canonical import is_digest
from .errors import ReceiptError
from .receipt import ProoflineReceipt, SUCCESSFUL_EXECUTION_RESULT
from .schemas import ADAPTER_SIGNAL_SCHEMA, VERDICTS

if TYPE_CHECKING:
    from .lifecycle import GenLayerLifecycleClient

RELEASE_READY = "RELEASE_READY"
HOLD = "HOLD"
MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


def signal_for_verdict(verdict: str) -> str:
    signal = {
        "ACCEPT": RELEASE_READY,
        "REJECT": HOLD,
        "UNDETERMINED": MANUAL_REVIEW_REQUIRED,
    }.get(verdict)
    if signal is None:
        raise ReceiptError(f"unsupported verdict: {verdict}")
    return signal


@dataclass(frozen=True)
class AdapterSignal:
    receipt_digest: str
    agreement_id: str
    verdict: str
    signal: str
    applied: bool = True
    schema_version: str = "proofline.adapter-signal.v1"

    def __post_init__(self) -> None:
        if self.schema_version != ADAPTER_SIGNAL_SCHEMA:
            raise ReceiptError("unsupported adapter signal schema")
        if not self.receipt_digest or not is_digest(self.receipt_digest):
            raise ReceiptError("adapter signal receipt_digest is invalid")
        if not self.agreement_id or self.verdict not in VERDICTS:
            raise ReceiptError("adapter signal identity is invalid")
        if self.signal != signal_for_verdict(self.verdict):
            raise ReceiptError("adapter signal does not match verdict")
        if not isinstance(self.applied, bool):
            raise ReceiptError("adapter signal applied must be boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "agreement_id": self.agreement_id,
            "applied": self.applied,
            "receipt_digest": self.receipt_digest,
            "schema_version": self.schema_version,
            "signal": self.signal,
            "verdict": self.verdict,
        }


class LocalAdapter:
    """Apply only finalized receipts; repeat delivery is idempotent."""

    def __init__(
        self,
        *,
        lifecycle_client: GenLayerLifecycleClient | None = None,
        allow_test_receipts: bool = False,
    ) -> None:
        self._applied: dict[str, AdapterSignal] = {}
        self._agreement_receipts: dict[str, str] = {}
        self._lifecycle_client = lifecycle_client
        self._allow_test_receipts = allow_test_receipts

    def apply(self, receipt: ProoflineReceipt) -> AdapterSignal:
        hosted_final = (
            receipt.finality_status == "FINALIZED"
            and receipt.protocol_status == "FINALIZED"
            and receipt.execution_result == SUCCESSFUL_EXECUTION_RESULT
            and bool(receipt.transaction_reference)
            and bool(receipt.contract_address)
            and receipt.chain_id is not None
        )
        test_final = (
            self._allow_test_receipts
            and receipt.finality_status == "DIRECT_MODE"
            and receipt.protocol_status == "LOCAL_DIRECT_RESULT"
            and receipt.adjudication_mode == "GENLAYER_DIRECT"
        )
        if not (hosted_final or test_final):
            raise ReceiptError("adapter accepts finalized receipts only")
        if hosted_final:
            if self._lifecycle_client is None:
                raise ReceiptError("hosted receipt requires lifecycle verification")
            verified = self._lifecycle_client.finalized_receipt(
                receipt.transaction_reference,
                contract_address=receipt.contract_address,
                decision_args=[receipt.agreement_id],
            )
            if verified.receipt_digest != receipt.receipt_digest:
                raise ReceiptError("hosted receipt does not match lifecycle readback")
        key = receipt.receipt_digest
        prior_key = self._agreement_receipts.get(receipt.agreement_id)
        if prior_key is not None and prior_key != key:
            raise ReceiptError("conflicting finalized receipt for agreement")
        previous = self._applied.get(key)
        if previous is not None:
            return previous
        signal = AdapterSignal(
            receipt_digest=key,
            agreement_id=receipt.agreement_id,
            verdict=receipt.verdict,
            signal=signal_for_verdict(receipt.verdict),
        )
        self._applied[key] = signal
        self._agreement_receipts[receipt.agreement_id] = key
        return signal

    def applied_count(self) -> int:
        return len(self._applied)
