"""Reproducible synthetic evidence fixtures used by tests and the CLI."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .canonical import digest_json, sha256_bytes
from .crypto import address_from_private_key, private_key_from_seed, sign_payload
from .schemas import (
    AGREEMENT_SCHEMA,
    ENVELOPE_SCHEMA,
    POLICY_SCHEMA,
)

# Synthetic test vector only. Derive it deterministically at runtime so no
# literal private-key-shaped credential is stored in the repository. Production
# callers must supply their own signer key through an approved secret mechanism.
FIXTURE_SIGNING_KEY = private_key_from_seed(b"proofline-fixture-signing-key-v1")
FIXTURE_SIGNER = address_from_private_key(FIXTURE_SIGNING_KEY)
FIXTURE_NOW = int(datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc).timestamp())
FIXTURE_TIMESTAMP = "2026-09-12T11:00:00Z"


def _policy(job_id: str, *, deadline: int = FIXTURE_NOW + 3600) -> dict[str, Any]:
    return {
        "schema_version": POLICY_SCHEMA,
        "policy_version": "acceptance-policy.v1",
        "agreement_id": job_id,
        "deadline": deadline,
        "signer_address": FIXTURE_SIGNER,
        "required_artifacts": ["response"],
        "deterministic_requirements": {
            "max_evidence_age_seconds": 7200,
            "require_snapshot": True,
        },
        "subjective_criterion": {
            "id": "quality",
            "statement": "The response must clearly answer the requested report question with actionable evidence.",
            "allowed_verdicts": ["ACCEPT", "REJECT", "UNDETERMINED"],
        },
        "evidence_allowlist": ["embedded://evidence"],
    }


def _agreement(job_id: str) -> dict[str, Any]:
    unsigned = {
        "schema_version": AGREEMENT_SCHEMA,
        "agreement_id": job_id,
        "policy_version": "acceptance-policy.v1",
        "parties": {
            "buyer": "buyer-fixture",
            "worker": "worker-fixture",
        },
    }
    unsigned["agreement_digest"] = digest_json(unsigned)
    return unsigned


def _response_body(job_id: str, flavor: str) -> dict[str, Any]:
    if flavor == "pass":
        response = {
            "status": 200,
            "content_type": "application/json",
            "body": {
                "answer": "The report identifies the root cause, cites the observed failure, and gives a concrete remediation.",
                "evidence": ["fixture-log-001", "fixture-test-001"],
            },
        }
    elif flavor == "semantic-fail":
        response = {
            "status": 200,
            "content_type": "application/json",
            "body": {
                "answer": "Everything looks fine.",
                "evidence": [],
            },
        }
    elif flavor == "ambiguous":
        response = {
            "status": 200,
            "content_type": "application/json",
            "body": {
                "answer": "The source is unavailable, so the result cannot be determined.",
                "evidence": [],
            },
        }
    else:
        response = {
            "status": 200,
            "content_type": "application/json",
            "body": {"answer": "fixture"},
        }
    return {
        "job_id": job_id,
        "request": {
            "method": "POST",
            "path": "/v1/report",
            "body": {"question": "What failed and how should it be fixed?"},
        },
        "response": response,
    }


def make_fixture(job_id: str) -> dict[str, Any]:
    """Return one complete fixture, including an evaluator expectation."""

    if job_id not in {
        "job-pass",
        "job-structural-fail",
        "job-semantic-fail",
        "job-ambiguous",
    }:
        raise KeyError(job_id)
    if job_id == "job-structural-fail":
        flavor = "pass"
    elif job_id == "job-semantic-fail":
        flavor = "semantic-fail"
    elif job_id == "job-ambiguous":
        flavor = "ambiguous"
    else:
        flavor = "pass"
    policy = _policy(job_id)
    agreement = _agreement(job_id)
    artifact_body = _response_body(job_id, flavor)
    artifact_bytes = (str(artifact_body).replace("'", '"')).encode("utf-8")
    # Use canonical JSON bytes, matching the exact bytes checked by validation.
    from .canonical import canonical_json

    artifact_bytes = canonical_json(artifact_body).encode("utf-8")
    artifact_digest = sha256_bytes(artifact_bytes)
    artifact_uri = f"embedded://evidence/{job_id}.json"
    request_digest = digest_json(artifact_body["request"])
    response_digest = digest_json(artifact_body["response"])
    envelope: dict[str, Any] = {
        "schema_version": ENVELOPE_SCHEMA,
        "job_id": job_id,
        "policy_version": policy["policy_version"],
        "agreement_digest": agreement["agreement_digest"],
        "policy_digest": digest_json(policy),
        "artifacts": [
            {
                "artifact_id": "response",
                "uri": artifact_uri,
                "sha256": artifact_digest,
                "required": True,
                "snapshot_id": artifact_digest,
                "media_type": "application/json",
            }
        ],
        "timestamp": FIXTURE_TIMESTAMP,
        "deadline": policy["deadline"],
        "nonce": f"{job_id}-nonce-v1",
        "request_hash": request_digest,
        "response_hash": response_digest,
        "signer": {"address": FIXTURE_SIGNER, "signature": ""},
    }
    if job_id == "job-structural-fail":
        # Deterministic failure: omit the policy-required response artifact.
        envelope["artifacts"][0] = {
            "artifact_id": "logs",
            "uri": artifact_uri,
            "sha256": artifact_digest,
            "required": False,
            "snapshot_id": artifact_digest,
            "media_type": "application/json",
        }
    envelope["signer"]["signature"] = sign_payload(
        {key: value for key, value in envelope.items() if key != "signer"}
        | {"signer": {"address": FIXTURE_SIGNER}},
        FIXTURE_SIGNING_KEY,
    )
    expected = {
        "job-pass": "ACCEPT",
        "job-structural-fail": "REJECT",
        "job-semantic-fail": "REJECT",
        "job-ambiguous": "UNDETERMINED",
    }[job_id]
    return {
        "job_id": job_id,
        "policy": policy,
        "agreement": agreement,
        "envelope": envelope,
        "evidence": {artifact_uri: artifact_bytes},
        "expected_verdict": expected,
        "expected_evaluation_invoked": job_id != "job-structural-fail",
        "expected_signal": {
            "ACCEPT": "RELEASE_READY",
            "REJECT": "HOLD",
            "UNDETERMINED": "MANUAL_REVIEW_REQUIRED",
        }[expected],
    }


def all_fixtures() -> dict[str, dict[str, Any]]:
    return {job_id: make_fixture(job_id) for job_id in (
        "job-pass",
        "job-structural-fail",
        "job-semantic-fail",
        "job-ambiguous",
    )}


def write_fixture_files(root: str | Path) -> None:
    """Write exact evidence bytes for inspection; used by the fixture runner."""

    target = Path(root)
    target.mkdir(parents=True, exist_ok=True)
    for fixture in all_fixtures().values():
        uri = fixture["envelope"]["artifacts"][0]["uri"]
        if not uri.startswith("embedded://evidence/"):
            continue
        (target / uri.removeprefix("embedded://evidence/")).write_bytes(
            fixture["evidence"][uri]
        )
