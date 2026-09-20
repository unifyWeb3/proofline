from __future__ import annotations

import json

import pytest

from proofline.adapters import (
    HOLD,
    MANUAL_REVIEW_REQUIRED,
    RELEASE_READY,
    LocalAdapter,
)
from proofline.canonical import canonical_json, digest_json, sha256_bytes
from proofline.engine import LocalEngine
from proofline.errors import ProtocolError, ReceiptError, SchemaError
from proofline.evaluation import validate_evaluator_output
from proofline.fixtures import FIXTURE_NOW, all_fixtures
from proofline.crypto import sign_payload
from proofline.fixtures import FIXTURE_SIGNING_KEY, FIXTURE_SIGNER
from proofline.receipt import ProoflineReceipt
from proofline.validation import validate_submission


def test_canonical_json_is_insertion_order_independent():
    assert canonical_json({"z": 1, "a": 2}) == '{"a":2,"z":1}'
    assert digest_json({"z": 1, "a": 2}) == digest_json({"a": 2, "z": 1})


def test_fixture_signatures_and_hashes_are_valid(fixtures, fixture_now):
    for fixture in fixtures.values():
        result = validate_submission(
            fixture["policy"],
            fixture["agreement"],
            fixture["envelope"],
            fixture["evidence"],
            now=fixture_now,
        )
        if fixture["job_id"] == "job-structural-fail":
            assert not result.passed
        else:
            assert result.passed, result.to_dict()


def test_structural_failure_short_circuits_evaluation(fixtures, fixture_now):
    engine = LocalEngine()
    calls = []

    def evaluator(prompt, evidence):
        calls.append(prompt)
        return {"verdict": "ACCEPT", "reason_code": "x"}

    fixture = fixtures["job-structural-fail"]
    result = engine.submit(
        fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"],
        now=fixture_now, evaluator=evaluator,
    )
    assert result.receipt.verdict == "REJECT"
    assert result.receipt.reason_code == "ARTIFACT_MISSING"
    assert result.receipt.evaluation_invoked is False
    assert calls == []
    assert result.receipt.finality_status == "LOCAL_ONLY"


@pytest.mark.parametrize(
    ("job_id", "verdict", "signal"),
    [
        ("job-pass", "ACCEPT", RELEASE_READY),
        ("job-semantic-fail", "REJECT", HOLD),
        ("job-ambiguous", "UNDETERMINED", MANUAL_REVIEW_REQUIRED),
    ],
)
def test_three_application_verdicts_are_reproducible(fixtures, fixture_now, job_id, verdict, signal):
    fixture = fixtures[job_id]
    result = LocalEngine().submit(
        fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"],
        now=fixture_now,
    )
    assert result.receipt.verdict == verdict
    assert result.receipt.adapter_signal == signal
    assert result.receipt.evaluation_invoked is True


def test_replay_and_duplicate_submission_are_rejected(fixtures, fixture_now):
    fixture = fixtures["job-pass"]
    engine = LocalEngine()
    first = engine.submit(
        fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"],
        now=fixture_now,
    )
    assert first.receipt.verdict == "ACCEPT"
    with pytest.raises(SchemaError, match="NONCE_REPLAY|DUPLICATE_SUBMISSION"):
        engine.submit(
            fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"],
            now=fixture_now,
        )


def test_deterministic_rejection_consumes_replay_guard(fixtures, fixture_now):
    fixture = fixtures["job-structural-fail"]
    engine = LocalEngine()
    first = engine.submit(
        fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"],
        now=fixture_now,
    )
    assert first.receipt.reason_code == "ARTIFACT_MISSING"
    with pytest.raises(SchemaError, match="NONCE_REPLAY|DUPLICATE_SUBMISSION"):
        engine.submit(
            fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"],
            now=fixture_now,
        )


def test_stale_and_unavailable_evidence_fail_before_evaluation(fixtures, fixture_now):
    fixture = fixtures["job-pass"]
    stale_policy = json.loads(json.dumps(fixture["policy"]))
    stale_policy["deterministic_requirements"]["max_evidence_age_seconds"] = 60
    stale_envelope = json.loads(json.dumps(fixture["envelope"]))
    stale_envelope["policy_digest"] = digest_json(stale_policy)
    stale = validate_submission(
        stale_policy,
        fixture["agreement"],
        stale_envelope,
        fixture["evidence"],
        now=fixture_now,
        verify_signatures=False,
    )
    assert not stale.passed
    assert stale.reason_code == "STALE_EVIDENCE"

    unavailable = validate_submission(
        fixture["policy"],
        fixture["agreement"],
        fixture["envelope"],
        {},
        now=fixture_now,
        verify_signatures=False,
    )
    assert not unavailable.passed
    assert unavailable.reason_code == "EVIDENCE_UNAVAILABLE"


def test_non_string_evidence_is_malformed_and_stateless(fixtures, fixture_now):
    fixture = fixtures["job-pass"]
    uri = fixture["envelope"]["artifacts"][0]["uri"]
    malformed = {uri: []}
    result = validate_submission(
        fixture["policy"], fixture["agreement"], fixture["envelope"], malformed,
        now=fixture_now,
    )
    assert result.reason_code == "MALFORMED_PAYLOAD"
    engine = LocalEngine()
    with pytest.raises(SchemaError, match="MALFORMED_PAYLOAD"):
        engine.submit(
            fixture["policy"], fixture["agreement"], fixture["envelope"], malformed,
            now=fixture_now,
        )
    assert engine.used_nonces == set()
    assert engine.seen_jobs == set()


def test_signer_mismatch_fails_preflight(fixtures, fixture_now):
    fixture = fixtures["job-pass"]
    wrong_signer = json.loads(json.dumps(fixture["envelope"]))
    wrong_signer["signer"]["address"] = "0x" + "11" * 20
    result = validate_submission(
        fixture["policy"], fixture["agreement"], wrong_signer, fixture["evidence"],
        now=fixture_now, verify_signatures=False,
    )
    assert not result.passed
    assert result.reason_code == "SIGNER_MISMATCH"


def test_unexpected_evaluator_fields_are_a_protocol_error(fixtures, fixture_now):
    fixture = fixtures["job-pass"]
    engine = LocalEngine()
    with pytest.raises(ProtocolError, match="malformed evaluator output"):
        engine.submit(
            fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"],
            now=fixture_now,
            evaluator=lambda prompt, evidence: {
                "verdict": "ACCEPT",
                "criterion_result": "REJECT",
                "reason_code": "CONTRADICTORY",
                "evidence_digest": "",
                "policy_digest": "",
                "rationale": "conflicting result",
            },
        )
    assert engine.used_nonces == set()
    assert engine.seen_jobs == set()


def test_bad_signature_hash_and_deadline_fail_closed(fixtures, fixture_now):
    fixture = fixtures["job-pass"]
    bad = json.loads(json.dumps(fixture["envelope"]))
    bad["signer"]["signature"] = "0x" + "00" * 65
    result = validate_submission(
        fixture["policy"], fixture["agreement"], bad, fixture["evidence"], now=fixture_now,
    )
    assert not result.passed
    assert result.reason_code == "SIGNATURE_INVALID"

    bad_hash = json.loads(json.dumps(fixture["envelope"]))
    bad_hash["artifacts"][0]["sha256"] = sha256_bytes(b"different")
    bad_hash["signer"]["signature"] = sign_payload(
        {key: value for key, value in bad_hash.items() if key != "signer"}
        | {"signer": {"address": FIXTURE_SIGNER}},
        FIXTURE_SIGNING_KEY,
    )
    result = validate_submission(
        fixture["policy"], fixture["agreement"], bad_hash, fixture["evidence"], now=fixture_now,
    )
    assert not result.passed
    assert result.reason_code == "EVIDENCE_HASH_MISMATCH"

    expired = json.loads(json.dumps(fixture["policy"]))
    expired["deadline"] = fixture_now - 1
    bad_deadline = json.loads(json.dumps(fixture["envelope"]))
    bad_deadline["deadline"] = expired["deadline"]
    bad_deadline["policy_digest"] = digest_json(expired)
    bad_deadline["signer"]["signature"] = sign_payload(
        {key: value for key, value in bad_deadline.items() if key != "signer"}
        | {"signer": {"address": FIXTURE_SIGNER}},
        FIXTURE_SIGNING_KEY,
    )
    result = validate_submission(
        expired, fixture["agreement"], bad_deadline, fixture["evidence"], now=fixture_now,
    )
    assert not result.passed
    assert result.reason_code == "DEADLINE_EXPIRED"


def test_unknown_fields_and_duplicate_artifacts_are_rejected(fixtures):
    fixture = fixtures["job-pass"]
    policy = json.loads(json.dumps(fixture["policy"]))
    policy["unexpected"] = True
    result = validate_submission(
        policy, fixture["agreement"], fixture["envelope"], fixture["evidence"], now=FIXTURE_NOW,
    )
    assert result.reason_code == "MALFORMED_PAYLOAD"

    envelope = json.loads(json.dumps(fixture["envelope"]))
    envelope["artifacts"].append(dict(envelope["artifacts"][0]))
    result = validate_submission(
        fixture["policy"], fixture["agreement"], envelope, fixture["evidence"], now=FIXTURE_NOW,
    )
    assert result.reason_code == "MALFORMED_PAYLOAD"


def test_malformed_evaluator_output_is_technical(fixtures):
    fixture = fixtures["job-pass"]
    policy_digest = digest_json(fixture["policy"])
    with pytest.raises(ProtocolError, match="malformed evaluator output"):
        validate_evaluator_output(
            "not-json", policy_digest=policy_digest, evidence_digest="sha256:" + "0" * 64
        )


def test_custom_evaluator_must_return_bound_digests(fixtures, fixture_now):
    fixture = fixtures["job-pass"]
    engine = LocalEngine()
    with pytest.raises(ProtocolError, match="malformed evaluator output"):
        engine.submit(
            fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"],
            now=fixture_now,
            evaluator=lambda prompt, evidence: {
                "verdict": "ACCEPT",
                "reason_code": "CRITERION_SATISFIED",
                "evidence_digest": "",
                "policy_digest": "",
                "rationale": "not actually bound",
            },
        )
    assert engine.used_nonces == set()
    assert engine.seen_jobs == set()


def test_adapter_is_finalized_only_and_idempotent(fixtures, fixture_now):
    fixture = fixtures["job-pass"]
    receipt = LocalEngine().submit(
        fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"],
        now=fixture_now,
    ).receipt
    adapter = LocalAdapter(allow_test_receipts=True)
    first = adapter.apply(receipt)
    second = adapter.apply(receipt)
    assert first.to_dict() == second.to_dict()
    assert adapter.applied_count() == 1

    with pytest.raises(ReceiptError):
        LocalAdapter().apply(receipt)

    with pytest.raises(ReceiptError, match="only local results or hosted finality"):
        ProoflineReceipt.from_decision(
            receipt.decision(),
            transaction_reference="local:pending",
            protocol_status="PENDING",
            finality_status="LOCAL_ONLY",
        )


def test_receipt_digest_validation_and_no_payment_claim(fixtures, fixture_now):
    fixture = fixtures["job-pass"]
    receipt = LocalEngine().submit(
        fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"],
        now=fixture_now,
    ).receipt
    value = receipt.to_dict()
    assert "payment" not in value
    assert "settlement" not in value
    assert ProoflineReceipt.from_dict(value).receipt_digest == receipt.receipt_digest
    value["receipt_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ReceiptError):
        ProoflineReceipt.from_dict(value)


def test_malformed_primitives_fail_closed_without_incidental_exceptions():
    result = validate_submission("bad", {}, {}, None, now=FIXTURE_NOW)
    assert result.passed is False
    assert result.reason_code == "MALFORMED_PAYLOAD"
    with pytest.raises(SchemaError, match="MALFORMED_PAYLOAD"):
        LocalEngine().submit("bad", {}, {}, None, now=FIXTURE_NOW)


def test_protocol_evaluator_failure_is_not_an_application_verdict(fixtures, fixture_now):
    fixture = fixtures["job-pass"]
    engine = LocalEngine()

    def unavailable(prompt, evidence):
        raise RuntimeError("RPC/evaluator unavailable")

    with pytest.raises(ProtocolError):
        engine.submit(
            fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"],
            now=fixture_now, evaluator=unavailable,
        )
    assert engine.used_nonces == set()
    assert engine.seen_jobs == set()

    retry = engine.submit(
        fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"],
        now=fixture_now,
    )
    assert retry.receipt.verdict == "ACCEPT"


def test_fixture_uri_is_not_a_hosted_compatible_evidence_source(fixtures, fixture_now):
    fixture = fixtures["job-pass"]
    policy = json.loads(json.dumps(fixture["policy"]))
    policy["evidence_allowlist"] = ["fixture://evidence"]
    envelope = json.loads(json.dumps(fixture["envelope"]))
    old_uri = envelope["artifacts"][0]["uri"]
    fixture_uri = "fixture://evidence/job-pass.json"
    envelope["artifacts"][0]["uri"] = fixture_uri
    envelope["policy_digest"] = digest_json(policy)
    result = validate_submission(
        policy,
        fixture["agreement"],
        envelope,
        {fixture_uri: fixture["evidence"][old_uri]},
        now=fixture_now,
        verify_signatures=False,
    )
    assert result.passed is False
    assert result.reason_code == "INVALID_ARTIFACT_URI"
