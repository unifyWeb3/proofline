"""Canonical Proofline decision and finalized receipt models.

The contract can emit a decision record, but it cannot know its transaction
hash or hosted finality while executing. A finalized receipt is therefore
constructed only by the lifecycle client after protocol and execution checks.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from .canonical import digest_json, is_digest, require_digest
from .errors import ReceiptError
from .schemas import RECEIPT_SCHEMA, VERDICTS, validate_address

DECISION_SCHEMA = "proofline.decision.v1"
OFFICIAL_PROTOCOL_STATUSES = frozenset(
    {
        "UNINITIALIZED", "PENDING", "PROPOSING", "COMMITTING", "REVEALING",
        "ACCEPTED", "UNDETERMINED", "FINALIZED", "CANCELED",
        "APPEAL_REVEALING", "APPEAL_COMMITTING", "READY_TO_FINALIZE",
        "VALIDATORS_TIMEOUT", "LEADER_TIMEOUT",
    }
)
LOCAL_PROTOCOL_STATUSES = frozenset({"LOCAL_DETERMINISTIC_REJECT", "LOCAL_DIRECT_RESULT"})
PROTOCOL_STATUSES = OFFICIAL_PROTOCOL_STATUSES | LOCAL_PROTOCOL_STATUSES
LOCAL_FINALITY_STATUSES = frozenset({"LOCAL_ONLY", "DIRECT_MODE"})
FINALITY_STATUSES = frozenset({"FINALIZED"}) | LOCAL_FINALITY_STATUSES
EXPECTED_ADAPTER_SIGNALS = {
    "ACCEPT": "RELEASE_READY",
    "REJECT": "HOLD",
    "UNDETERMINED": "MANUAL_REVIEW_REQUIRED",
}
SUCCESSFUL_EXECUTION_RESULT = "FINISHED_WITH_RETURN"


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReceiptError(f"{field} must be a non-empty string")
    return value


def _is_transaction_hash(value: str) -> bool:
    if len(value) != 66 or not value.startswith("0x"):
        return False
    try:
        int(value[2:], 16)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class ProoflineDecision:
    """The exact acceptance decision record emitted by the contract."""

    agreement_id: str
    agreement_digest: str
    policy_version: str
    policy_digest: str
    evidence_digest: str
    verdict: str
    reason_code: str
    adjudication_mode: str
    evaluation_invoked: bool
    deterministic_result: dict[str, Any]
    contract_address: str
    network: str
    chain_id: int
    adapter_signal: str
    schema_version: str = DECISION_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "deterministic_result", copy.deepcopy(self.deterministic_result))
        for value, field in (
            (self.agreement_id, "agreement_id"), (self.policy_version, "policy_version"),
            (self.reason_code, "reason_code"), (self.adjudication_mode, "adjudication_mode"),
            (self.contract_address, "contract_address"), (self.network, "network"),
        ):
            _required_string(value, field)
        if self.schema_version != DECISION_SCHEMA:
            raise ReceiptError("unsupported decision schema")
        if self.verdict not in VERDICTS:
            raise ReceiptError("decision verdict is invalid")
        if self.adapter_signal != EXPECTED_ADAPTER_SIGNALS[self.verdict]:
            raise ReceiptError("decision adapter_signal does not match verdict")
        if not isinstance(self.evaluation_invoked, bool):
            raise ReceiptError("decision evaluation_invoked must be boolean")
        if not isinstance(self.deterministic_result, dict):
            raise ReceiptError("decision deterministic_result must be an object")
        if not is_digest(self.agreement_digest) or not is_digest(self.policy_digest):
            raise ReceiptError("decision agreement/policy digests are invalid")
        if not is_digest(self.evidence_digest):
            raise ReceiptError("decision evidence_digest is invalid")
        if isinstance(self.chain_id, bool) or not isinstance(self.chain_id, int) or self.chain_id < 0:
            raise ReceiptError("decision chain_id must be a non-negative integer")
        try:
            validate_address(self.contract_address, "decision.contract_address")
        except Exception as exc:
            raise ReceiptError(str(exc)) from exc

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        value = {
            "adapter_signal": self.adapter_signal, "adjudication_mode": self.adjudication_mode,
            "agreement_digest": self.agreement_digest, "agreement_id": self.agreement_id,
            "chain_id": self.chain_id, "contract_address": self.contract_address,
            "deterministic_result": copy.deepcopy(self.deterministic_result),
            "evidence_digest": self.evidence_digest, "evaluation_invoked": self.evaluation_invoked,
            "network": self.network, "policy_digest": self.policy_digest,
            "policy_version": self.policy_version, "reason_code": self.reason_code,
            "schema_version": self.schema_version, "verdict": self.verdict,
        }
        if include_digest:
            value["decision_digest"] = self.decision_digest
        return value

    @property
    def decision_digest(self) -> str:
        return digest_json(self.to_dict(include_digest=False))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProoflineDecision":
        if not isinstance(value, Mapping):
            raise ReceiptError("decision must be an object")
        expected = {
            "adapter_signal", "adjudication_mode", "agreement_digest", "agreement_id", "chain_id",
            "contract_address", "deterministic_result", "evidence_digest", "evaluation_invoked",
            "network", "policy_digest", "policy_version", "reason_code", "schema_version", "verdict",
        }
        unknown = set(value) - expected - {"decision_digest"}
        missing = expected - set(value)
        if missing:
            raise ReceiptError(f"decision is missing fields: {', '.join(sorted(missing))}")
        if unknown:
            raise ReceiptError(f"decision has unknown fields: {', '.join(sorted(unknown))}")
        decision = cls(
            agreement_id=_required_string(value["agreement_id"], "agreement_id"),
            agreement_digest=_required_string(value["agreement_digest"], "agreement_digest"),
            policy_version=_required_string(value["policy_version"], "policy_version"),
            policy_digest=_required_string(value["policy_digest"], "policy_digest"),
            evidence_digest=_required_string(value["evidence_digest"], "evidence_digest"),
            verdict=_required_string(value["verdict"], "verdict"),
            reason_code=_required_string(value["reason_code"], "reason_code"),
            adjudication_mode=_required_string(value["adjudication_mode"], "adjudication_mode"),
            evaluation_invoked=value["evaluation_invoked"],
            deterministic_result=copy.deepcopy(value["deterministic_result"]),
            contract_address=_required_string(value["contract_address"], "contract_address"),
            network=_required_string(value["network"], "network"),
            chain_id=value["chain_id"],
            adapter_signal=_required_string(value["adapter_signal"], "adapter_signal"),
            schema_version=value["schema_version"],
        )
        supplied = value.get("decision_digest")
        if supplied is not None:
            require_digest(supplied, "decision_digest")
            if supplied != decision.decision_digest:
                raise ReceiptError("decision_digest does not match canonical decision")
        return decision


@dataclass(frozen=True)
class ProoflineReceipt:
    """A lifecycle-finalized receipt bound to one exact decision digest."""

    agreement_id: str
    agreement_digest: str
    policy_version: str
    policy_digest: str
    evidence_digest: str
    verdict: str
    reason_code: str
    adjudication_mode: str
    evaluation_invoked: bool
    deterministic_result: dict[str, Any]
    protocol_status: str
    finality_status: str
    execution_result: str | None
    transaction_reference: str
    contract_address: str
    network: str
    chain_id: int
    decision_digest: str
    finalized_at: int | None
    adapter_signal: str
    schema_version: str = RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "deterministic_result", copy.deepcopy(self.deterministic_result))
        if self.schema_version != RECEIPT_SCHEMA:
            raise ReceiptError("unsupported receipt schema")
        for value, field in (
            (self.agreement_id, "agreement_id"), (self.policy_version, "policy_version"),
            (self.reason_code, "reason_code"), (self.adjudication_mode, "adjudication_mode"),
            (self.protocol_status, "protocol_status"), (self.finality_status, "finality_status"),
            (self.transaction_reference, "transaction_reference"), (self.contract_address, "contract_address"),
            (self.network, "network"),
        ):
            _required_string(value, field)
        if self.finality_status not in FINALITY_STATUSES:
            raise ReceiptError("receipt finality_status is invalid")
        if self.protocol_status not in PROTOCOL_STATUSES:
            raise ReceiptError("receipt protocol_status is invalid")
        if self.verdict not in VERDICTS:
            raise ReceiptError("receipt verdict is invalid")
        if self.adapter_signal != EXPECTED_ADAPTER_SIGNALS[self.verdict]:
            raise ReceiptError("receipt adapter_signal does not match verdict")
        if not isinstance(self.evaluation_invoked, bool):
            raise ReceiptError("evaluation_invoked must be boolean")
        if not isinstance(self.deterministic_result, dict):
            raise ReceiptError("deterministic_result must be an object")
        for value, field in (
            (self.agreement_digest, "agreement_digest"), (self.policy_digest, "policy_digest"),
            (self.evidence_digest, "evidence_digest"), (self.decision_digest, "decision_digest"),
        ):
            require_digest(value, f"receipt {field}")
        if isinstance(self.chain_id, bool) or not isinstance(self.chain_id, int) or self.chain_id < 0:
            raise ReceiptError("receipt chain_id must be a non-negative integer")
        if self.finalized_at is not None and (
            isinstance(self.finalized_at, bool) or not isinstance(self.finalized_at, int) or self.finalized_at < 0
        ):
            raise ReceiptError("receipt finalized_at must be a non-negative integer or null")
        try:
            validate_address(self.contract_address, "receipt.contract_address")
        except Exception as exc:
            raise ReceiptError(str(exc)) from exc
        if self.finality_status == "FINALIZED":
            if self.protocol_status != "FINALIZED":
                raise ReceiptError("hosted finalized receipt needs finality")
            if self.execution_result != SUCCESSFUL_EXECUTION_RESULT:
                raise ReceiptError("hosted finalized receipt needs successful execution")
            if not _is_transaction_hash(self.transaction_reference):
                raise ReceiptError("hosted finalized receipt needs a transaction hash")
            if self.chain_id == 0 or int(self.contract_address[2:], 16) == 0:
                raise ReceiptError("hosted finalized receipt needs chain and contract provenance")
        elif self.protocol_status == "LOCAL_DETERMINISTIC_REJECT":
            if self.finality_status != "LOCAL_ONLY" or self.finalized_at is not None:
                raise ReceiptError("deterministic local result needs LOCAL_ONLY finality")
            if self.execution_result is not None:
                raise ReceiptError("local receipt cannot claim hosted execution")
        elif self.protocol_status == "LOCAL_DIRECT_RESULT":
            if self.finality_status != "DIRECT_MODE" or self.finalized_at is not None:
                raise ReceiptError("direct local result needs DIRECT_MODE finality")
            if self.execution_result is not None:
                raise ReceiptError("local receipt cannot claim hosted execution")
        else:
            raise ReceiptError("proofline.receipt.v1 represents only local results or hosted finality")

    @classmethod
    def from_decision(
        cls,
        decision: ProoflineDecision | Mapping[str, Any],
        *,
        transaction_reference: str,
        protocol_status: str,
        finality_status: str,
        execution_result: str | None = None,
        finalized_at: int | None = None,
    ) -> "ProoflineReceipt":
        if protocol_status == "FINALIZED" or finality_status == "FINALIZED":
            raise ReceiptError("hosted finality requires lifecycle verification")
        parsed = decision if isinstance(decision, ProoflineDecision) else ProoflineDecision.from_dict(decision)
        return cls(
            agreement_id=parsed.agreement_id, agreement_digest=parsed.agreement_digest,
            policy_version=parsed.policy_version, policy_digest=parsed.policy_digest,
            evidence_digest=parsed.evidence_digest, verdict=parsed.verdict,
            reason_code=parsed.reason_code, adjudication_mode=parsed.adjudication_mode,
            evaluation_invoked=parsed.evaluation_invoked, deterministic_result=parsed.deterministic_result,
            protocol_status=protocol_status, finality_status=finality_status,
            execution_result=execution_result,
            transaction_reference=transaction_reference, contract_address=parsed.contract_address,
            network=parsed.network, chain_id=parsed.chain_id, decision_digest=parsed.decision_digest,
            finalized_at=finalized_at, adapter_signal=parsed.adapter_signal,
        )

    @classmethod
    def _from_finalized_decision(
        cls,
        decision: ProoflineDecision,
        *,
        transaction_reference: str,
        execution_result: str,
        finalized_at: int | None,
    ) -> "ProoflineReceipt":
        """Construct hosted provenance for the lifecycle client only."""
        return cls(
            agreement_id=decision.agreement_id, agreement_digest=decision.agreement_digest,
            policy_version=decision.policy_version, policy_digest=decision.policy_digest,
            evidence_digest=decision.evidence_digest, verdict=decision.verdict,
            reason_code=decision.reason_code, adjudication_mode=decision.adjudication_mode,
            evaluation_invoked=decision.evaluation_invoked, deterministic_result=decision.deterministic_result,
            protocol_status="FINALIZED", finality_status="FINALIZED",
            execution_result=execution_result, transaction_reference=transaction_reference,
            contract_address=decision.contract_address, network=decision.network,
            chain_id=decision.chain_id, decision_digest=decision.decision_digest,
            finalized_at=finalized_at, adapter_signal=decision.adapter_signal,
        )

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        value: dict[str, Any] = {
            "adapter_signal": self.adapter_signal, "adjudication_mode": self.adjudication_mode,
            "agreement_digest": self.agreement_digest, "agreement_id": self.agreement_id,
            "chain_id": self.chain_id, "contract_address": self.contract_address,
            "decision_digest": self.decision_digest, "deterministic_result": copy.deepcopy(self.deterministic_result),
            "evidence_digest": self.evidence_digest, "evaluation_invoked": self.evaluation_invoked,
            "execution_result": self.execution_result,
            "finality_status": self.finality_status, "finalized_at": self.finalized_at,
            "network": self.network, "policy_digest": self.policy_digest,
            "policy_version": self.policy_version, "protocol_status": self.protocol_status,
            "reason_code": self.reason_code, "schema_version": self.schema_version,
            "transaction_reference": self.transaction_reference, "verdict": self.verdict,
        }
        if include_digest:
            value["receipt_digest"] = self.receipt_digest
        return value

    @property
    def receipt_digest(self) -> str:
        return digest_json(self.to_dict(include_digest=False))

    def decision(self) -> ProoflineDecision:
        return ProoflineDecision(
            agreement_id=self.agreement_id, agreement_digest=self.agreement_digest,
            policy_version=self.policy_version, policy_digest=self.policy_digest,
            evidence_digest=self.evidence_digest, verdict=self.verdict,
            reason_code=self.reason_code, adjudication_mode=self.adjudication_mode,
            evaluation_invoked=self.evaluation_invoked, deterministic_result=self.deterministic_result,
            contract_address=self.contract_address, network=self.network, chain_id=self.chain_id,
            adapter_signal=self.adapter_signal,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProoflineReceipt":
        if not isinstance(value, Mapping):
            raise ReceiptError("receipt must be an object")
        expected = {
            "adapter_signal", "adjudication_mode", "agreement_digest", "agreement_id", "chain_id",
            "contract_address", "decision_digest", "deterministic_result", "evidence_digest",
            "evaluation_invoked", "execution_result", "finality_status", "finalized_at", "network", "policy_digest",
            "policy_version", "protocol_status", "reason_code", "schema_version", "transaction_reference",
            "verdict",
        }
        unknown = set(value) - expected - {"receipt_digest"}
        missing = expected - set(value)
        if missing:
            raise ReceiptError(f"receipt is missing fields: {', '.join(sorted(missing))}")
        if unknown:
            raise ReceiptError(f"receipt has unknown fields: {', '.join(sorted(unknown))}")
        receipt = cls(
            agreement_id=_required_string(value["agreement_id"], "agreement_id"),
            agreement_digest=_required_string(value["agreement_digest"], "agreement_digest"),
            policy_version=_required_string(value["policy_version"], "policy_version"),
            policy_digest=_required_string(value["policy_digest"], "policy_digest"),
            evidence_digest=_required_string(value["evidence_digest"], "evidence_digest"),
            verdict=_required_string(value["verdict"], "verdict"),
            reason_code=_required_string(value["reason_code"], "reason_code"),
            adjudication_mode=_required_string(value["adjudication_mode"], "adjudication_mode"),
            evaluation_invoked=value["evaluation_invoked"],
            deterministic_result=copy.deepcopy(value["deterministic_result"]),
            protocol_status=_required_string(value["protocol_status"], "protocol_status"),
            finality_status=_required_string(value["finality_status"], "finality_status"),
            execution_result=value["execution_result"],
            transaction_reference=_required_string(value["transaction_reference"], "transaction_reference"),
            contract_address=_required_string(value["contract_address"], "contract_address"),
            network=_required_string(value["network"], "network"),
            chain_id=value["chain_id"], decision_digest=_required_string(value["decision_digest"], "decision_digest"),
            finalized_at=value["finalized_at"], adapter_signal=_required_string(value["adapter_signal"], "adapter_signal"),
            schema_version=value["schema_version"],
        )
        if receipt.decision_digest != receipt.decision().decision_digest:
            raise ReceiptError("decision_digest does not match the receipt decision fields")
        supplied_digest = value.get("receipt_digest")
        if supplied_digest is not None:
            require_digest(supplied_digest, "receipt_digest")
            if supplied_digest != receipt.receipt_digest:
                raise ReceiptError("receipt_digest does not match canonical receipt")
        return receipt
