"""Deterministic, fail-closed submission validation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .canonical import digest_json, sha256_bytes, strict_json_loads
from .crypto import verify_signature
from .errors import ProoflineError, SchemaError
from .schemas import AcceptancePolicy, Agreement, EvidenceEnvelope


@dataclass(frozen=True)
class DeterministicResult:
    passed: bool
    reason_code: str
    checks: dict[str, bool] = field(default_factory=dict)
    evaluation_invoked: bool = False
    evidence_digest: str = ""
    errors: tuple[str, ...] = ()
    mode: str = "LOCAL_PREFLIGHT"

    @property
    def verdict(self) -> str:
        return "ACCEPT" if self.passed else "REJECT"

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": dict(self.checks),
            "errors": list(self.errors),
            "evaluation_invoked": self.evaluation_invoked,
            "evidence_digest": self.evidence_digest,
            "mode": self.mode,
            "passed": self.passed,
            "reason_code": self.reason_code,
            "verdict": self.verdict,
        }


def parse_timestamp(value: str) -> int:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return int(parsed.astimezone(timezone.utc).timestamp())


def _uri_allowed(uri: str, allowlist: tuple[str, ...]) -> bool:
    if uri.startswith("embedded://"):
        # Embedded URIs are intentionally opaque and only match complete
        # path segments so an evil sibling cannot satisfy an allowlist entry.
        try:
            fixture_parts = urlsplit(uri)
        except ValueError:
            return False
        if fixture_parts.query or fixture_parts.fragment or fixture_parts.username:
            return False
        return any(
            uri == item or uri.startswith(item.rstrip("/") + "/")
            for item in allowlist
            if item.startswith("embedded://")
        )
    try:
        parsed = urlsplit(uri)
        parsed_port = parsed.port or 443
    except ValueError:
        return False
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return False
    parsed_host = parsed.hostname.lower()
    for allowed in allowlist:
        try:
            allowed_parts = urlsplit(allowed)
            allowed_port = allowed_parts.port or 443
        except ValueError:
            continue
        if (
            allowed_parts.scheme.lower() != "https"
            or not allowed_parts.hostname
            or allowed_parts.username is not None
            or allowed_parts.password is not None
            or allowed_parts.query
            or allowed_parts.fragment
        ):
            continue
        allowed_host = allowed_parts.hostname.lower()
        # An allowlist entry names one exact host.  Subdomains require their
        # own explicit entry rather than being trusted implicitly.
        if parsed_host != allowed_host:
            continue
        if parsed_port != allowed_port:
            continue
        allowed_path = allowed_parts.path.rstrip("/")
        if not allowed_path or allowed_path == "/":
            return True
        if parsed.path == allowed_path or parsed.path.startswith(allowed_path + "/"):
            return True
    return False


def _bytes_for(evidence_store: Mapping[str, Any], uri: str) -> bytes | None:
    value = evidence_store.get(uri)
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    if isinstance(value, Mapping) and "body" in value:
        return _bytes_for({uri: value["body"]}, uri)
    return None


def _load_artifact(data: bytes) -> dict[str, Any] | None:
    try:
        parsed = strict_json_loads(data)
    except (TypeError, ValueError, json.JSONDecodeError, SchemaError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _check_artifact_bindings(
    envelope: EvidenceEnvelope,
    artifacts: dict[str, bytes],
) -> tuple[dict[str, bool], str, list[str], dict[str, Any] | None]:
    checks: dict[str, bool] = {}
    errors: list[str] = []
    parsed_reference: dict[str, Any] | None = None
    for artifact in envelope.artifacts:
        raw = artifacts.get(artifact.artifact_id)
        if raw is None:
            checks[f"artifact:{artifact.artifact_id}:present"] = False
            if artifact.required:
                errors.append("EVIDENCE_UNAVAILABLE")
            continue
        checks[f"artifact:{artifact.artifact_id}:present"] = True
        actual = sha256_bytes(raw)
        matches = actual == artifact.sha256
        checks[f"artifact:{artifact.artifact_id}:hash"] = matches
        if not matches:
            errors.append("EVIDENCE_HASH_MISMATCH")
        parsed = _load_artifact(raw)
        valid_json = parsed is not None
        checks[f"artifact:{artifact.artifact_id}:json"] = valid_json
        if not valid_json:
            errors.append("EVIDENCE_MALFORMED")
        elif artifact.artifact_id == "response":
            parsed_reference = parsed
    manifest = [
        {"artifact_id": item.artifact_id, "sha256": item.sha256}
        for item in sorted(envelope.artifacts, key=lambda item: item.artifact_id)
    ]
    # Keep the digest shape explicit and identical in the contract and
    # off-chain paths.  The wrapper also leaves room for versioned manifest
    # metadata without changing the meaning of the artifact list itself.
    return checks, digest_json({"artifacts": manifest}), errors, parsed_reference


def validate_submission(
    policy_data: AcceptancePolicy | Mapping[str, Any],
    agreement_data: Agreement | Mapping[str, Any],
    envelope_data: EvidenceEnvelope | Mapping[str, Any],
    evidence_store: Mapping[str, Any],
    *,
    now: int,
    used_nonces: set[str] | None = None,
    seen_jobs: set[str] | None = None,
    mode: str = "LOCAL_PREFLIGHT",
    verify_signatures: bool = True,
    signature_verifier: Callable[[Mapping[str, Any], str, str], bool] = verify_signature,
) -> DeterministicResult:
    """Return a structured result; never send a failed payload to evaluation."""

    checks: dict[str, bool] = {}
    errors: list[str] = []
    if not isinstance(evidence_store, Mapping):
        return DeterministicResult(
            False,
            "MALFORMED_PAYLOAD",
            {"schema": False},
            errors=("evidence_store must be a mapping",),
            mode=mode,
        )
    try:
        policy = (
            policy_data
            if isinstance(policy_data, AcceptancePolicy)
            else AcceptancePolicy.from_dict(policy_data)
        )
        agreement = (
            agreement_data
            if isinstance(agreement_data, Agreement)
            else Agreement.from_dict(agreement_data)
        )
        envelope = (
            envelope_data
            if isinstance(envelope_data, EvidenceEnvelope)
            else EvidenceEnvelope.from_dict(envelope_data)
        )
    except (ProoflineError, TypeError, KeyError) as exc:
        return DeterministicResult(
            False,
            "MALFORMED_PAYLOAD",
            {"schema": False},
            errors=(str(exc),),
            mode=mode,
        )

    checks["schema"] = True
    checks["policy_version"] = envelope.policy_version == policy.policy_version
    if not checks["policy_version"]:
        errors.append("POLICY_VERSION_MISMATCH")
    checks["agreement_id"] = agreement.agreement_id == policy.agreement_id
    if not checks["agreement_id"]:
        errors.append("AGREEMENT_ID_MISMATCH")
    checks["envelope_job_id"] = envelope.job_id == policy.agreement_id
    if not checks["envelope_job_id"]:
        errors.append("JOB_ID_MISMATCH")
    checks["agreement_policy_version"] = agreement.policy_version == policy.policy_version
    if not checks["agreement_policy_version"]:
        errors.append("AGREEMENT_POLICY_VERSION_MISMATCH")
    checks["policy_digest"] = envelope.policy_digest == policy.digest
    if not checks["policy_digest"]:
        errors.append("POLICY_DIGEST_MISMATCH")
    checks["agreement_digest"] = envelope.agreement_digest == agreement.computed_digest
    if not checks["agreement_digest"]:
        errors.append("AGREEMENT_DIGEST_MISMATCH")
    checks["signer_binding"] = envelope.signer.address.lower() == policy.signer_address.lower()
    if not checks["signer_binding"]:
        errors.append("SIGNER_MISMATCH")

    try:
        timestamp = parse_timestamp(envelope.timestamp)
        checks["timestamp_format"] = True
        checks["timestamp_not_future"] = timestamp <= now
        if not checks["timestamp_not_future"]:
            errors.append("TIMESTAMP_IN_FUTURE")
        max_age = policy.deterministic_requirements.get("max_evidence_age_seconds", 0)
        checks["timestamp_fresh"] = max_age <= 0 or now - timestamp <= max_age
        if not checks["timestamp_fresh"]:
            errors.append("STALE_EVIDENCE")
    except (TypeError, ValueError, OverflowError):
        checks["timestamp_format"] = False
        errors.append("INVALID_TIMESTAMP")

    checks["deadline_binding"] = envelope.deadline == policy.deadline
    checks["deadline_valid"] = now <= policy.deadline
    if not checks["deadline_binding"]:
        errors.append("DEADLINE_MISMATCH")
    if not checks["deadline_valid"]:
        errors.append("DEADLINE_EXPIRED")

    used_nonces = used_nonces if used_nonces is not None else set()
    seen_jobs = seen_jobs if seen_jobs is not None else set()
    checks["nonce_unique"] = envelope.nonce not in used_nonces
    checks["job_unique"] = envelope.job_id not in seen_jobs
    if not checks["nonce_unique"]:
        errors.append("NONCE_REPLAY")
    if not checks["job_unique"]:
        errors.append("DUPLICATE_SUBMISSION")

    required_ids = set(policy.required_artifacts)
    submitted_ids = {item.artifact_id for item in envelope.artifacts}
    checks["required_artifacts"] = required_ids.issubset(submitted_ids)
    if not checks["required_artifacts"]:
        errors.append("ARTIFACT_MISSING")
    required_declarations = {
        item.artifact_id: item.required
        for item in envelope.artifacts
        if item.artifact_id in required_ids
    }
    checks["required_artifact_flags"] = all(
        required_declarations.get(item_id, False) for item_id in required_ids
    )
    if not checks["required_artifact_flags"]:
        errors.append("ARTIFACT_NOT_MARKED_REQUIRED")

    checks["artifact_urls"] = all(
        _uri_allowed(item.uri, policy.evidence_allowlist) for item in envelope.artifacts
    )
    if not checks["artifact_urls"]:
        errors.append("INVALID_ARTIFACT_URI")

    artifacts_by_id: dict[str, bytes] = {}
    unavailable = False
    for artifact in envelope.artifacts:
        raw = _bytes_for(evidence_store, artifact.uri)
        if raw is None:
            if artifact.uri in evidence_store and evidence_store[artifact.uri] is not None:
                errors.append("MALFORMED_PAYLOAD")
            if artifact.required:
                unavailable = True
        else:
            artifacts_by_id[artifact.artifact_id] = raw
    checks["evidence_available"] = not unavailable
    if unavailable:
        errors.append("EVIDENCE_UNAVAILABLE")

    binding_checks, evidence_digest, binding_errors, parsed = _check_artifact_bindings(
        envelope, artifacts_by_id
    )
    checks.update(binding_checks)
    errors.extend(binding_errors)

    if parsed is not None:
        request = parsed.get("request")
        response = parsed.get("response")
        try:
            checks["request_hash"] = request is not None and _hash_json(request) == envelope.request_hash
        except (TypeError, ValueError, SchemaError):
            checks["request_hash"] = False
        try:
            checks["response_hash"] = response is not None and _hash_json(response) == envelope.response_hash
        except (TypeError, ValueError, SchemaError):
            checks["response_hash"] = False
        if not checks["request_hash"]:
            errors.append("REQUEST_HASH_MISMATCH")
        if not checks["response_hash"]:
            errors.append("RESPONSE_HASH_MISMATCH")
        if policy.deterministic_requirements.get("require_snapshot"):
            checks["snapshot"] = all(
                item.snapshot_id and item.snapshot_id == item.sha256 for item in envelope.artifacts
            )
            if not checks["snapshot"]:
                errors.append("SNAPSHOT_MISMATCH")
    else:
        checks["request_hash"] = False
        checks["response_hash"] = False

    if verify_signatures:
        try:
            checks["signature"] = bool(signature_verifier(
                envelope.signing_payload(), envelope.signer.signature, envelope.signer.address
            ))
        except Exception:  # noqa: BLE001 - malformed/unsupported crypto fails closed
            checks["signature"] = False
        if not checks["signature"]:
            errors.append("SIGNATURE_INVALID")
    else:
        checks["signature"] = bool(envelope.signer.signature)
        if not checks["signature"]:
            errors.append("SIGNATURE_MISSING")

    # Keep the first reason deterministic and make the short-circuit explicit.
    if errors:
        priority = (
            "MALFORMED_PAYLOAD",
            "POLICY_JOB_ID_MISMATCH",
            "POLICY_VERSION_MISMATCH",
            "AGREEMENT_ID_MISMATCH",
            "JOB_ID_MISMATCH",
            "AGREEMENT_POLICY_VERSION_MISMATCH",
            "POLICY_DIGEST_MISMATCH",
            "AGREEMENT_DIGEST_MISMATCH",
            "SIGNER_MISMATCH",
            "SIGNATURE_INVALID",
            "INVALID_TIMESTAMP",
            "TIMESTAMP_IN_FUTURE",
            "STALE_EVIDENCE",
            "DEADLINE_MISMATCH",
            "DEADLINE_EXPIRED",
            "NONCE_REPLAY",
            "DUPLICATE_SUBMISSION",
            "ARTIFACT_MISSING",
            "ARTIFACT_NOT_MARKED_REQUIRED",
            "INVALID_ARTIFACT_URI",
            "EVIDENCE_UNAVAILABLE",
            "EVIDENCE_MALFORMED",
            "EVIDENCE_HASH_MISMATCH",
            "SNAPSHOT_MISMATCH",
            "REQUEST_HASH_MISMATCH",
            "RESPONSE_HASH_MISMATCH",
        )
        reason = next((item for item in priority if item in errors), errors[0])
        return DeterministicResult(
            False,
            reason,
            checks,
            evaluation_invoked=False,
            evidence_digest=evidence_digest,
            errors=tuple(dict.fromkeys(errors)),
            mode=mode,
        )

    checks["all_deterministic"] = True
    return DeterministicResult(
        True,
        "DETERMINISTIC_VALID",
        checks,
        evaluation_invoked=False,
        evidence_digest=evidence_digest,
        mode=mode,
    )


def _hash_json(value: Any) -> str:
    return digest_json(value)
