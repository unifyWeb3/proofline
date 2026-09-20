"""Application orchestration for local/direct Proofline runs."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .adapters import signal_for_verdict
from .canonical import is_digest, strict_json_loads
from .errors import SchemaError
from .evaluation import SemanticEvaluation, evaluate_semantics
from .receipt import ProoflineDecision, ProoflineReceipt
from .validation import DeterministicResult, validate_submission


APPLICATION_REJECTION_REASONS = frozenset(
    {
        "TIMESTAMP_IN_FUTURE",
        "STALE_EVIDENCE",
        "DEADLINE_EXPIRED",
        "ARTIFACT_MISSING",
        "ARTIFACT_NOT_MARKED_REQUIRED",
        "INVALID_ARTIFACT_URI",
        "EVIDENCE_UNAVAILABLE",
        "EVIDENCE_MALFORMED",
        "EVIDENCE_HASH_MISMATCH",
        "SNAPSHOT_MISMATCH",
        "REQUEST_HASH_MISMATCH",
        "RESPONSE_HASH_MISMATCH",
    }
)


@dataclass(frozen=True)
class SubmissionResult:
    deterministic: DeterministicResult
    semantic: SemanticEvaluation | None
    receipt: ProoflineReceipt


def fixture_evaluator(prompt: str, evidence: Mapping[str, Any] | str) -> str:
    """Deterministic mock evaluator used by four generated fixtures.

    This is intentionally a test adapter, not a claim about model behavior.
    The contract's direct tests provide the equivalent response through the
    official GenLayer LLM mock hook.
    """

    try:
        value = strict_json_loads(evidence) if isinstance(evidence, str) else evidence
    except (json.JSONDecodeError, ValueError):
        value = {}
    answer = json.dumps(value, sort_keys=True).lower()
    if "cannot be determined" in answer or "unavailable" in answer:
        verdict, reason = "UNDETERMINED", "EVIDENCE_INSUFFICIENT"
    elif "root cause" in answer and "remediation" in answer:
        verdict, reason = "ACCEPT", "CRITERION_SATISFIED"
    else:
        verdict, reason = "REJECT", "CRITERION_NOT_SATISFIED"
    # The caller replaces these bound digests before validation.
    return json.dumps(
        {
            "evidence_digest": "",
            "policy_digest": "",
            "rationale": reason,
            "reason_code": reason,
            "verdict": verdict,
        }
    )


def _bound_evaluator(
    evaluator: Callable[[str, Mapping[str, Any] | str], Any],
    *,
    evidence_digest: str,
    policy_digest: str,
) -> Callable[[str, Mapping[str, Any] | str], Any]:
    bind_fixture_digests = evaluator is fixture_evaluator

    def call(prompt: str, evidence: Mapping[str, Any] | str) -> Any:
        raw = evaluator(prompt, evidence)
        if isinstance(raw, str):
            try:
                value = strict_json_loads(raw)
            except (json.JSONDecodeError, ValueError):
                return raw
        else:
            value = copy.deepcopy(dict(raw)) if isinstance(raw, Mapping) else raw
        if bind_fixture_digests and isinstance(value, dict):
            if not value.get("evidence_digest"):
                value["evidence_digest"] = evidence_digest
            if not value.get("policy_digest"):
                value["policy_digest"] = policy_digest
            return value
        return raw

    return call


class LocalEngine:
    """Run the complete local path with explicit replay protection."""

    def __init__(self) -> None:
        self.used_nonces: set[str] = set()
        self.seen_jobs: set[str] = set()

    def submit(
        self,
        policy: Mapping[str, Any],
        agreement: Mapping[str, Any],
        envelope: Mapping[str, Any],
        evidence_store: Mapping[str, Any],
        *,
        now: int,
        evaluator: Callable[[str, Mapping[str, Any] | str], Any] = fixture_evaluator,
        mode: str = "LOCAL_PREFLIGHT",
    ) -> SubmissionResult:
        deterministic = validate_submission(
            policy,
            agreement,
            envelope,
            evidence_store,
            now=now,
            used_nonces=self.used_nonces,
            seen_jobs=self.seen_jobs,
            mode=mode,
        )
        if not deterministic.passed:
            if deterministic.reason_code not in APPLICATION_REJECTION_REASONS:
                raise SchemaError(
                    "submission is not an authenticated policy-valid rejection: "
                    + deterministic.reason_code
                )
            agreement_obj = agreement if isinstance(agreement, Mapping) else {}
            envelope_obj = envelope if isinstance(envelope, Mapping) else {}

            def safe_digest(value: Any) -> str:
                return value if is_digest(value) else "sha256:" + "0" * 64

            decision = ProoflineDecision(
                agreement_id=str(agreement_obj.get("agreement_id", envelope_obj.get("job_id", "unknown"))),
                agreement_digest=safe_digest(envelope_obj.get("agreement_digest")),
                policy_version=str(envelope_obj.get("policy_version", "unknown")),
                policy_digest=safe_digest(envelope_obj.get("policy_digest")),
                evidence_digest=deterministic.evidence_digest or "sha256:" + "0" * 64,
                verdict="REJECT",
                reason_code=deterministic.reason_code,
                adjudication_mode="DETERMINISTIC",
                evaluation_invoked=False,
                deterministic_result=deterministic.to_dict(),
                contract_address="0x" + "0" * 40,
                network="local",
                chain_id=0,
                adapter_signal=signal_for_verdict("REJECT"),
            )
            receipt = ProoflineReceipt.from_decision(
                decision,
                transaction_reference="local:" + str(envelope_obj.get("job_id", "unknown")),
                protocol_status="LOCAL_DETERMINISTIC_REJECT",
                finality_status="LOCAL_ONLY",
            )
            envelope_obj = envelope if isinstance(envelope, Mapping) else {}
            nonce = envelope_obj.get("nonce")
            job_id = envelope_obj.get("job_id")
            if isinstance(nonce, str) and isinstance(job_id, str):
                self.used_nonces.add(nonce)
                self.seen_jobs.add(job_id)
            return SubmissionResult(deterministic, None, receipt)

        from .schemas import AcceptancePolicy

        parsed_policy = AcceptancePolicy.from_dict(policy)
        response_artifact = next(
            (item for item in envelope["artifacts"] if item.get("artifact_id") == "response"),
            None,
        )
        if response_artifact is None:
            raise ValueError("deterministic validation passed without response artifact")
        raw_artifact = evidence_store[response_artifact["uri"]]
        if isinstance(raw_artifact, bytes):
            semantic_input: Mapping[str, Any] | str = raw_artifact.decode("utf-8")
        else:
            semantic_input = raw_artifact
        semantic = evaluate_semantics(
            parsed_policy,
            semantic_input,
            evidence_digest=deterministic.evidence_digest,
            evaluator=_bound_evaluator(
                evaluator,
                evidence_digest=deterministic.evidence_digest,
                policy_digest=parsed_policy.digest,
            ),
        )
        decision = ProoflineDecision(
            agreement_id=parsed_policy.agreement_id,
            agreement_digest=envelope["agreement_digest"],
            policy_version=parsed_policy.policy_version,
            policy_digest=parsed_policy.digest,
            evidence_digest=deterministic.evidence_digest,
            verdict=semantic.verdict,
            reason_code=semantic.reason_code,
            adjudication_mode="GENLAYER_DIRECT",
            evaluation_invoked=True,
            deterministic_result=deterministic.to_dict(),
            contract_address="0x" + "0" * 40,
            network="local-direct",
            chain_id=0,
            adapter_signal=signal_for_verdict(semantic.verdict),
        )
        receipt = ProoflineReceipt.from_decision(
            decision,
            transaction_reference="local:" + parsed_policy.agreement_id,
            protocol_status="LOCAL_DIRECT_RESULT",
            finality_status="DIRECT_MODE",
        )
        self.used_nonces.add(envelope["nonce"])
        self.seen_jobs.add(envelope["job_id"])
        return SubmissionResult(deterministic, semantic, receipt)
