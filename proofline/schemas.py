"""Versioned Proofline domain schemas.

The implementation deliberately uses small dataclasses instead of a runtime
schema dependency so the same validation rules can be reused by a CLI, tests,
and an eventual API service.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from .canonical import canonical_json, digest_json, is_digest, require_digest, strip_digest_field
from .errors import SchemaError

POLICY_SCHEMA = "proofline.policy.v1"
AGREEMENT_SCHEMA = "proofline.agreement.v1"
ENVELOPE_SCHEMA = "proofline.response.v1"
RECEIPT_SCHEMA = "proofline.receipt.v1"
ADAPTER_SIGNAL_SCHEMA = "proofline.adapter-signal.v1"

ACCEPT = "ACCEPT"
REJECT = "REJECT"
UNDETERMINED = "UNDETERMINED"
VERDICTS = frozenset({ACCEPT, REJECT, UNDETERMINED})


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaError(f"{name} must be an object")
    return dict(value)


def _exact_keys(
    obj: Mapping[str, Any],
    required: set[str],
    *,
    optional: set[str] | None = None,
    name: str,
) -> None:
    optional = optional or set()
    missing = required - set(obj)
    unknown = set(obj) - required - optional
    if missing:
        raise SchemaError(f"{name} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise SchemaError(f"{name} has unknown fields: {', '.join(sorted(unknown))}")


def _string(value: Any, field: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value.strip()):
        raise SchemaError(f"{field} must be a non-empty string")
    return value


def _integer(value: Any, field: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise SchemaError(f"{field} must be >= {minimum}")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise SchemaError(f"{field} must be an array")
    return value


def _copy_json_object(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    """Copy nested policy/agreement data before retaining it in a schema."""

    copied = copy.deepcopy(dict(value))
    # Validate the retained extension data now, rather than when a digest is
    # eventually requested.  This prevents non-JSON Python values from hiding
    # inside an otherwise valid envelope.
    try:
        canonical_json(copied)
    except SchemaError as exc:
        raise SchemaError(f"{field} is not canonical JSON: {exc}") from exc
    return copied


def validate_address(value: Any, field: str = "address") -> str:
    address = _string(value, field)
    if len(address) != 42 or not address.startswith("0x"):
        raise SchemaError(f"{field} must be a 20-byte 0x address")
    try:
        int(address[2:], 16)
    except ValueError as exc:
        raise SchemaError(f"{field} must be hexadecimal") from exc
    return address


def validate_signature(value: Any, field: str = "signature") -> str:
    signature = _string(value, field)
    if len(signature) != 132 or not signature.startswith("0x"):
        raise SchemaError(f"{field} must be a 65-byte 0x signature")
    try:
        int(signature[2:], 16)
    except ValueError as exc:
        raise SchemaError(f"{field} must be hexadecimal") from exc
    return signature


@dataclass(frozen=True)
class ArtifactReference:
    artifact_id: str
    uri: str
    sha256: str
    required: bool = True
    snapshot_id: str = ""
    media_type: str = "application/json"

    @classmethod
    def from_dict(cls, value: Any) -> "ArtifactReference":
        obj = _object(value, "artifact")
        _exact_keys(
            obj,
            {"artifact_id", "uri", "sha256", "required", "snapshot_id", "media_type"},
            name="artifact",
        )
        artifact_id = _string(obj.get("artifact_id"), "artifact.artifact_id")
        uri = _string(obj.get("uri"), "artifact.uri")
        digest = require_digest(obj.get("sha256"), "artifact.sha256")
        required = obj.get("required")
        if not isinstance(required, bool):
            raise SchemaError("artifact.required must be boolean")
        snapshot_id = obj.get("snapshot_id")
        media_type = obj.get("media_type")
        if not isinstance(snapshot_id, str) or not isinstance(media_type, str):
            raise SchemaError("artifact snapshot_id and media_type must be strings")
        if media_type != "application/json":
            raise SchemaError("artifact.media_type must be application/json")
        return cls(artifact_id, uri, digest, required, snapshot_id, media_type)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "media_type": self.media_type,
            "required": self.required,
            "sha256": self.sha256,
            "snapshot_id": self.snapshot_id,
            "uri": self.uri,
        }


@dataclass(frozen=True)
class SignerIdentity:
    address: str
    signature: str

    @classmethod
    def from_dict(cls, value: Any) -> "SignerIdentity":
        obj = _object(value, "signer")
        _exact_keys(obj, {"address", "signature"}, name="signer")
        return cls(
            validate_address(obj.get("address"), "signer.address"),
            validate_signature(obj.get("signature")),
        )

    def to_dict(self, *, include_signature: bool = True) -> dict[str, str]:
        result = {"address": self.address}
        if include_signature:
            result["signature"] = self.signature
        return result


@dataclass(frozen=True)
class AcceptancePolicy:
    policy_version: str
    agreement_id: str
    deadline: int
    signer_address: str
    required_artifacts: tuple[str, ...]
    deterministic_requirements: dict[str, Any]
    subjective_criterion: dict[str, Any]
    evidence_allowlist: tuple[str, ...]
    schema_version: str = POLICY_SCHEMA

    @classmethod
    def from_dict(cls, value: Any) -> "AcceptancePolicy":
        obj = _object(value, "policy")
        _exact_keys(
            obj,
            {
                "schema_version",
                "policy_version",
                "agreement_id",
                "deadline",
                "signer_address",
                "required_artifacts",
                "deterministic_requirements",
                "subjective_criterion",
                "evidence_allowlist",
            },
            name="policy",
        )
        if obj.get("schema_version") != POLICY_SCHEMA:
            raise SchemaError("unsupported policy schema_version")
        policy_version = _string(obj.get("policy_version"), "policy_version")
        agreement_id = _string(obj.get("agreement_id"), "agreement_id")
        deadline = _integer(obj.get("deadline"), "deadline", minimum=1)
        signer_address = validate_address(obj.get("signer_address"), "signer_address")
        required = _list(obj.get("required_artifacts"), "required_artifacts")
        if required != ["response"]:
            raise SchemaError("required_artifacts must be exactly ['response']")
        deterministic = _object(
            obj.get("deterministic_requirements", {}), "deterministic_requirements"
        )
        deterministic = _copy_json_object(deterministic, "deterministic_requirements")
        unknown_deterministic = set(deterministic) - {
            "max_evidence_age_seconds",
            "require_snapshot",
        }
        if unknown_deterministic:
            raise SchemaError(
                "deterministic_requirements has unknown fields: "
                + ", ".join(sorted(unknown_deterministic))
            )
        if "max_evidence_age_seconds" in deterministic:
            _integer(
                deterministic["max_evidence_age_seconds"],
                "deterministic_requirements.max_evidence_age_seconds",
                minimum=0,
            )
        if "require_snapshot" in deterministic and not isinstance(
            deterministic["require_snapshot"], bool
        ):
            raise SchemaError("deterministic_requirements.require_snapshot must be boolean")
        subjective = _object(obj.get("subjective_criterion"), "subjective_criterion")
        _exact_keys(
            subjective,
            {"id", "statement", "allowed_verdicts"},
            name="subjective_criterion",
        )
        _string(subjective.get("id"), "subjective_criterion.id")
        _string(subjective.get("statement"), "subjective_criterion.statement")
        allowed = subjective.get("allowed_verdicts", list(VERDICTS))
        if not isinstance(allowed, list):
            raise SchemaError("subjective_criterion.allowed_verdicts must be an array")
        try:
            allowed_set = set(allowed)
        except TypeError as exc:
            raise SchemaError(
                "subjective_criterion.allowed_verdicts must contain strings"
            ) from exc
        if len(allowed_set) != len(allowed) or allowed_set != set(VERDICTS):
            raise SchemaError("subjective_criterion.allowed_verdicts must include all verdicts")
        subjective = _copy_json_object(subjective, "subjective_criterion")
        allowlist = _list(obj.get("evidence_allowlist", []), "evidence_allowlist")
        if not allowlist or any(not isinstance(item, str) or not item for item in allowlist):
            raise SchemaError("evidence_allowlist must contain non-empty strings")
        if len(set(allowlist)) != len(allowlist):
            raise SchemaError("evidence_allowlist must be unique")
        return cls(
            policy_version,
            agreement_id,
            deadline,
            signer_address,
            tuple(required),
            deterministic,
            subjective,
            tuple(allowlist),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agreement_id": self.agreement_id,
            "deadline": self.deadline,
            "deterministic_requirements": self.deterministic_requirements,
            "evidence_allowlist": list(self.evidence_allowlist),
            "policy_version": self.policy_version,
            "required_artifacts": list(self.required_artifacts),
            "schema_version": self.schema_version,
            "signer_address": self.signer_address,
            "subjective_criterion": self.subjective_criterion,
        }

    @property
    def digest(self) -> str:
        return digest_json(self.to_dict())


@dataclass(frozen=True)
class Agreement:
    agreement_id: str
    policy_version: str
    parties: dict[str, Any]
    agreement_digest: str = ""
    schema_version: str = AGREEMENT_SCHEMA

    @classmethod
    def from_dict(cls, value: Any) -> "Agreement":
        obj = _object(value, "agreement")
        _exact_keys(
            obj,
            {"schema_version", "agreement_id", "policy_version", "parties"},
            optional={"agreement_digest"},
            name="agreement",
        )
        if obj.get("schema_version") != AGREEMENT_SCHEMA:
            raise SchemaError("unsupported agreement schema_version")
        agreement_id = _string(obj.get("agreement_id"), "agreement_id")
        policy_version = _string(obj.get("policy_version"), "policy_version")
        parties = _object(obj.get("parties", {}), "parties")
        parties = _copy_json_object(parties, "agreement.parties")
        digest = obj.get("agreement_digest", "")
        if digest and not is_digest(digest):
            raise SchemaError("agreement_digest must be a sha256 digest")
        result = cls(agreement_id, policy_version, parties, digest)
        if digest and digest != result.computed_digest:
            raise SchemaError("agreement_digest does not match agreement contents")
        return result

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "agreement_id": self.agreement_id,
            "parties": self.parties,
            "policy_version": self.policy_version,
            "schema_version": self.schema_version,
        }
        if include_digest and self.agreement_digest:
            result["agreement_digest"] = self.agreement_digest
        return result

    @property
    def computed_digest(self) -> str:
        return digest_json(strip_digest_field(self.to_dict(), "agreement_digest"))


@dataclass(frozen=True)
class EvidenceEnvelope:
    job_id: str
    policy_version: str
    agreement_digest: str
    policy_digest: str
    artifacts: tuple[ArtifactReference, ...]
    timestamp: str
    deadline: int
    nonce: str
    signer: SignerIdentity
    request_hash: str
    response_hash: str
    schema_version: str = ENVELOPE_SCHEMA

    @classmethod
    def from_dict(cls, value: Any) -> "EvidenceEnvelope":
        obj = _object(value, "envelope")
        _exact_keys(
            obj,
            {
                "schema_version",
                "job_id",
                "policy_version",
                "agreement_digest",
                "policy_digest",
                "artifacts",
                "timestamp",
                "deadline",
                "nonce",
                "signer",
                "request_hash",
                "response_hash",
            },
            name="envelope",
        )
        if obj.get("schema_version") != ENVELOPE_SCHEMA:
            raise SchemaError("unsupported envelope schema_version")
        artifacts_raw = _list(obj.get("artifacts"), "artifacts")
        artifacts = tuple(ArtifactReference.from_dict(item) for item in artifacts_raw)
        if len(artifacts) != 1:
            raise SchemaError("envelope.artifacts must contain exactly one artifact")
        artifact_ids = [item.artifact_id for item in artifacts]
        if len(set(artifact_ids)) != len(artifact_ids):
            raise SchemaError("envelope.artifacts must have unique artifact_id values")
        request_hash = require_digest(obj.get("request_hash"), "request_hash")
        response_hash = require_digest(obj.get("response_hash"), "response_hash")
        timestamp = _string(obj.get("timestamp"), "timestamp")
        return cls(
            _string(obj.get("job_id"), "job_id"),
            _string(obj.get("policy_version"), "policy_version"),
            require_digest(obj.get("agreement_digest"), "agreement_digest"),
            require_digest(obj.get("policy_digest"), "policy_digest"),
            artifacts,
            timestamp,
            _integer(obj.get("deadline"), "deadline", minimum=1),
            _string(obj.get("nonce"), "nonce"),
            SignerIdentity.from_dict(obj.get("signer")),
            request_hash,
            response_hash,
        )

    def to_dict(self, *, include_signature: bool = True) -> dict[str, Any]:
        return {
            "agreement_digest": self.agreement_digest,
            "artifacts": [item.to_dict() for item in self.artifacts],
            "deadline": self.deadline,
            "job_id": self.job_id,
            "nonce": self.nonce,
            "policy_digest": self.policy_digest,
            "policy_version": self.policy_version,
            "request_hash": self.request_hash,
            "response_hash": self.response_hash,
            "schema_version": self.schema_version,
            "signer": self.signer.to_dict(include_signature=include_signature),
            "timestamp": self.timestamp,
        }

    def signing_payload(self) -> dict[str, Any]:
        return self.to_dict(include_signature=False)


def policy_digest(policy: Mapping[str, Any] | AcceptancePolicy) -> str:
    if isinstance(policy, AcceptancePolicy):
        return policy.digest
    return AcceptancePolicy.from_dict(policy).digest


def agreement_digest(agreement: Mapping[str, Any] | Agreement) -> str:
    if isinstance(agreement, Agreement):
        return agreement.computed_digest
    return Agreement.from_dict(agreement).computed_digest
