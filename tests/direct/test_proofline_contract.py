from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("gltest")

from proofline.canonical import digest_json
from proofline.errors import ReceiptError
from proofline.receipt import ProoflineDecision, ProoflineReceipt
from proofline.fixtures import FIXTURE_NOW, FIXTURE_SIGNER, make_fixture
from proofline.schemas import AcceptancePolicy
from proofline.validation import validate_submission


CONTRACT_PATH = str(Path(__file__).parents[2] / "contracts" / "proofline.py")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _fixture_sender() -> bytes:
    return bytes.fromhex(FIXTURE_SIGNER[2:])


def _deploy_registered(direct_vm, direct_deploy, fixture):
    # The contract owner and authorized evidence submitter are the same
    # deterministic fixture account for direct-mode tests.
    direct_vm.sender = _fixture_sender()
    contract = direct_deploy(CONTRACT_PATH)
    contract.register_job(
        fixture["job_id"], _json(fixture["policy"]), _json(fixture["agreement"])
    )
    return contract


def _submit(contract, fixture, envelope=None, evidence=None, agreement=None, policy=None):
    envelope = envelope or fixture["envelope"]
    uri = fixture["envelope"]["artifacts"][0]["uri"]
    evidence = fixture["evidence"][uri].decode("utf-8") if evidence is None else evidence
    return json.loads(
        contract.submit_job(
            fixture["job_id"],
            _json(fixture["policy"] if policy is None else policy),
            _json(fixture["agreement"] if agreement is None else agreement),
            _json(envelope),
            evidence,
        )
    )


def _semantic_result(fixture, verdict, reason_code, rationale):
    deterministic = validate_submission(
        fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"], now=FIXTURE_NOW
    )
    return {
        "verdict": verdict,
        "reason_code": reason_code,
        "evidence_digest": deterministic.evidence_digest,
        "policy_digest": AcceptancePolicy.from_dict(fixture["policy"]).digest,
        "rationale": rationale,
    }


def test_direct_contract_schema_and_read_views(direct_vm, direct_deploy):
    contract = direct_deploy(CONTRACT_PATH)
    assert contract.get_contract_schema_version() == "proofline.response.v1"
    assert contract.get_decision_schema_version() == "proofline.decision.v1"
    assert contract.get_job("missing-job") == ""
    assert contract.nonce_used("missing-nonce") is False


def test_direct_structural_failure_skips_llm(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    envelope = json.loads(json.dumps(fixture["envelope"]))
    envelope["artifacts"][0]["sha256"] = "sha256:" + "0" * 64
    envelope["artifacts"][0]["snapshot_id"] = envelope["artifacts"][0]["sha256"]
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    result = _submit(contract, fixture, envelope)
    assert result["verdict"] == "REJECT"
    assert result["evaluation_invoked"] is False
    assert result["reason_code"] == "EVIDENCE_HASH_MISMATCH"
    assert result["schema_version"] == "proofline.decision.v1"


def test_direct_semantic_acceptance_uses_bounded_llm_mock(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    deterministic = validate_submission(
        fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"], now=FIXTURE_NOW
    )
    policy_digest = AcceptancePolicy.from_dict(fixture["policy"]).digest
    direct_vm.mock_llm(
        r".*",
        _json(
            {
                "verdict": "ACCEPT",
                "reason_code": "CRITERION_SATISFIED",
                "evidence_digest": deterministic.evidence_digest,
                "policy_digest": policy_digest,
                "rationale": "fixture criterion satisfied",
            }
        ),
    )
    result = _submit(_deploy_registered(direct_vm, direct_deploy, fixture), fixture)
    assert result["verdict"] == "ACCEPT"
    assert result["evaluation_invoked"] is True
    assert result["schema_version"] == "proofline.decision.v1"


def test_direct_semantic_prompt_separates_digest_from_criterion(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    deterministic = validate_submission(
        fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"], now=FIXTURE_NOW
    )
    policy_digest = AcceptancePolicy.from_dict(fixture["policy"]).digest
    direct_vm.mock_llm(
        r"Use the matching reason_code exactly: ACCEPT=CRITERION_MET, "
        r"REJECT=CRITERION_NOT_MET, UNDETERMINED=INSUFFICIENT_EVIDENCE\.\n"
        r"Copy both expected digest values exactly\. Do not calculate or alter them\. "
        r"Rationale must be one factual sentence of at most 120 characters\.\n"
        r"expected_policy_digest=sha256:[0-9a-f]{64}\n"
        r"expected_evidence_digest=sha256:[0-9a-f]{64}\ncriterion=",
        _json(
            {
                "verdict": "ACCEPT",
                "reason_code": "CRITERION_SATISFIED",
                "evidence_digest": deterministic.evidence_digest,
                "policy_digest": policy_digest,
                "rationale": "fixture criterion satisfied",
            }
        ),
    )
    result = _submit(_deploy_registered(direct_vm, direct_deploy, fixture), fixture)
    assert result["verdict"] == "ACCEPT"


def test_direct_semantic_output_failure_has_safe_category_and_does_not_store(
    direct_vm, direct_deploy
):
    fixture = make_fixture("job-pass")
    deterministic = validate_submission(
        fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"], now=FIXTURE_NOW
    )
    direct_vm.mock_llm(
        r".*",
        _json({
            "verdict": "ACCEPT",
            "reason_code": "CRITERION_MET",
            "evidence_digest": deterministic.evidence_digest,
            "policy_digest": AcceptancePolicy.from_dict(fixture["policy"]).digest,
            "rationale": "x" * 501,
        }),
    )
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    with direct_vm.expect_revert(
        "[LLM_ERROR] MALFORMED_EVALUATOR_OUTPUT:RATIONALE_INVALID"
    ):
        _submit(contract, fixture)
    assert contract.get_job(fixture["job_id"]) == ""
    assert contract.nonce_used(fixture["envelope"]["nonce"]) is False


def test_direct_captured_equivalence_validator_rejects_failed_leader(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    deterministic = validate_submission(
        fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"], now=FIXTURE_NOW
    )
    direct_vm.mock_llm(
        r".*",
        _json(
            {
                "verdict": "ACCEPT",
                "reason_code": "CRITERION_SATISFIED",
                "evidence_digest": deterministic.evidence_digest,
                "policy_digest": AcceptancePolicy.from_dict(fixture["policy"]).digest,
                "rationale": "fixture criterion satisfied",
            }
        ),
    )
    result = _submit(_deploy_registered(direct_vm, direct_deploy, fixture), fixture)
    assert result["verdict"] == "ACCEPT"

    # This exercises the captured comparator's technical-failure path only.
    # It is not evidence of a hosted validator committee or consensus.
    assert direct_vm.run_validator(leader_error=RuntimeError("leader failed")) is False


@pytest.mark.parametrize(
    ("conflicting_field", "conflicting_value", "conflicting_reason"),
    [
        ("verdict", "REJECT", "CRITERION_NOT_SATISFIED"),
        ("reason_code", "CRITERION_REVISED", "CRITERION_SATISFIED"),
    ],
)
def test_direct_captured_equivalence_rejects_valid_decision_disagreement(
    direct_vm, direct_deploy, conflicting_field, conflicting_value, conflicting_reason
):
    fixture = make_fixture("job-pass")
    validator_result = _semantic_result(
        fixture, "ACCEPT", "CRITERION_SATISFIED", "validator rationale"
    )
    direct_vm.mock_llm(r".*", _json(validator_result))
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    assert _submit(contract, fixture)["verdict"] == "ACCEPT"

    leader_result = _semantic_result(
        fixture, "ACCEPT", "CRITERION_SATISFIED", "leader rationale"
    )
    leader_result[conflicting_field] = conflicting_value
    if conflicting_field == "verdict":
        leader_result["reason_code"] = conflicting_reason
    assert set(leader_result) == {
        "verdict", "reason_code", "evidence_digest", "policy_digest", "rationale"
    }
    assert leader_result["evidence_digest"] == validator_result["evidence_digest"]
    assert leader_result["policy_digest"] == validator_result["policy_digest"]

    # Both payloads independently satisfy the evaluator schema and digest
    # bindings; the captured callback must reject their decision conflict.
    assert direct_vm.run_validator(leader_result=leader_result) is False


def test_direct_captured_equivalence_accepts_matching_decision_fields_with_distinct_rationale(
    direct_vm, direct_deploy
):
    fixture = make_fixture("job-pass")
    validator_result = _semantic_result(
        fixture, "ACCEPT", "CRITERION_SATISFIED", "validator explanation"
    )
    direct_vm.mock_llm(r".*", _json(validator_result))
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    assert _submit(contract, fixture)["verdict"] == "ACCEPT"

    leader_result = {**validator_result, "rationale": "different explanation"}
    assert direct_vm.run_validator(leader_result=leader_result) is True


def test_direct_captured_equivalence_rejects_malformed_leader_payload(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    valid_result = _semantic_result(
        fixture, "ACCEPT", "CRITERION_SATISFIED", "valid explanation"
    )
    direct_vm.mock_llm(r".*", _json(valid_result))
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    assert _submit(contract, fixture)["verdict"] == "ACCEPT"

    malformed_leader = {key: value for key, value in valid_result.items() if key != "rationale"}
    assert direct_vm.run_validator(leader_result=malformed_leader) is False


def test_direct_captured_equivalence_fails_closed_on_validator_evaluation_error(
    direct_vm, direct_deploy, monkeypatch
):
    fixture = make_fixture("job-pass")
    valid_result = _semantic_result(
        fixture, "ACCEPT", "CRITERION_SATISFIED", "valid explanation"
    )
    direct_vm.mock_llm(r".*", _json(valid_result))
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    assert _submit(contract, fixture)["verdict"] == "ACCEPT"

    _, _, validator_fn = direct_vm._captured_validators[-1]
    nondet = validator_fn.__globals__["gl"].nondet

    def technical_failure(*args, **kwargs):
        raise RuntimeError("validator evaluation unavailable")

    monkeypatch.setattr(nondet, "exec_prompt", technical_failure)
    assert direct_vm.run_validator(leader_result=valid_result) is False


def test_direct_malformed_evaluator_output_fails_closed(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    direct_vm.mock_llm(r".*", _json({"verdict": "ACCEPT"}))
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    with direct_vm.expect_revert("[LLM_ERROR] MALFORMED_EVALUATOR_OUTPUT"):
        _submit(contract, fixture)
    assert contract.get_job(fixture["job_id"]) == ""
    assert contract.nonce_used(fixture["envelope"]["nonce"]) is False


def test_direct_registration_is_owner_authorized(direct_vm, direct_deploy, direct_bob):
    fixture = make_fixture("job-pass")
    direct_vm.sender = _fixture_sender()
    contract = direct_deploy(CONTRACT_PATH)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("UNAUTHORIZED_REGISTRATION"):
        contract.register_job(fixture["job_id"], _json(fixture["policy"]), _json(fixture["agreement"]))
    assert contract.get_job(fixture["job_id"]) == ""

    # The failed front-running attempt did not reserve the job ID.
    direct_vm.sender = _fixture_sender()
    contract.register_job(fixture["job_id"], _json(fixture["policy"]), _json(fixture["agreement"]))


def test_direct_unregistered_job_cannot_be_submitted(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    direct_vm.sender = _fixture_sender()
    contract = direct_deploy(CONTRACT_PATH)
    with direct_vm.expect_revert("JOB_NOT_REGISTERED"):
        _submit(contract, fixture)
    assert contract.get_job(fixture["job_id"]) == ""
    assert contract.nonce_used(fixture["envelope"]["nonce"]) is False


def test_direct_sender_mismatch_and_forged_signature_revert(direct_vm, direct_deploy, direct_bob):
    fixture = make_fixture("job-pass")
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    direct_vm.sender = direct_bob
    with direct_vm.expect_revert("SIGNER_AUTHENTICATION_FAILED"):
        _submit(contract, fixture)
    assert contract.get_job(fixture["job_id"]) == ""
    assert contract.nonce_used(fixture["envelope"]["nonce"]) is False

    forged = json.loads(json.dumps(fixture["envelope"]))
    forged["signer"]["signature"] = "0x" + "00" * 65
    with direct_vm.expect_revert("SIGNER_AUTHENTICATION_FAILED"):
        _submit(contract, fixture, forged)
    assert contract.nonce_used(forged["nonce"]) is False


def test_direct_caller_claim_cannot_self_authorize_or_store_rejection(
    direct_vm, direct_deploy, direct_bob
):
    fixture = make_fixture("job-pass")
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    direct_vm.sender = direct_bob

    claimed_policy = json.loads(json.dumps(fixture["policy"]))
    claimed_policy["signer_address"] = "0x" + "11" * 20
    claimed_envelope = json.loads(json.dumps(fixture["envelope"]))
    claimed_envelope["signer"]["address"] = claimed_policy["signer_address"]
    claimed_envelope["signer"]["signature"] = "0x" + "00" * 65
    claimed_envelope["policy_digest"] = digest_json(claimed_policy)
    with direct_vm.expect_revert("REGISTRATION_BINDING_MISMATCH"):
        _submit(contract, fixture, claimed_envelope, policy=claimed_policy)

    deterministic_reject = json.loads(json.dumps(fixture["envelope"]))
    deterministic_reject["artifacts"][0]["sha256"] = "sha256:" + "0" * 64
    deterministic_reject["artifacts"][0]["snapshot_id"] = deterministic_reject["artifacts"][0]["sha256"]
    deterministic_reject["signer"]["signature"] = "0x" + "00" * 65
    with direct_vm.expect_revert("SIGNER_AUTHENTICATION_FAILED"):
        _submit(contract, fixture, deterministic_reject)

    assert contract.get_job(fixture["job_id"]) == ""
    assert contract.nonce_used(fixture["envelope"]["nonce"]) is False


def test_direct_replay_is_rejected_without_new_record(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    direct_vm.mock_llm(r".*", _json({"verdict": "UNDETERMINED", "reason_code": "x", "evidence_digest": validate_submission(fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"], now=FIXTURE_NOW).evidence_digest, "policy_digest": AcceptancePolicy.from_dict(fixture["policy"]).digest, "rationale": "uncertain"}))
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    first = _submit(contract, fixture)
    assert first["verdict"] == "UNDETERMINED"
    with direct_vm.expect_revert("NONCE_REPLAY"):
        _submit(contract, fixture)


@pytest.mark.parametrize(
    ("job_id", "verdict", "reason"),
    [
        ("job-pass", "ACCEPT", "CRITERION_SATISFIED"),
        ("job-semantic-fail", "REJECT", "CRITERION_NOT_SATISFIED"),
        ("job-ambiguous", "UNDETERMINED", "EVIDENCE_INSUFFICIENT"),
    ],
)
def test_direct_all_semantic_fixture_outcomes(direct_vm, direct_deploy, job_id, verdict, reason):
    fixture = make_fixture(job_id)
    deterministic = validate_submission(
        fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"], now=FIXTURE_NOW
    )
    direct_vm.mock_llm(
        r".*",
        _json(
            {
                "verdict": verdict,
                "reason_code": reason,
                "evidence_digest": deterministic.evidence_digest,
                "policy_digest": AcceptancePolicy.from_dict(fixture["policy"]).digest,
                "rationale": reason,
            }
        ),
    )
    result = _submit(_deploy_registered(direct_vm, direct_deploy, fixture), fixture)
    assert result["verdict"] == verdict
    assert result["evaluation_invoked"] is True
    assert result["schema_version"] == "proofline.decision.v1"


def test_direct_unavailable_evidence_is_deterministic_reject(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    result = json.loads(
        contract.submit_job(
            fixture["job_id"], _json(fixture["policy"]), _json(fixture["agreement"]),
            _json(fixture["envelope"]), "",
        )
    )
    assert result["verdict"] == "REJECT"
    assert result["evaluation_invoked"] is False
    assert result["reason_code"] == "EVIDENCE_UNAVAILABLE"
    assert contract.nonce_used(fixture["envelope"]["nonce"]) is True


@pytest.mark.parametrize("evidence", [None, [], {}, 0, False])
def test_direct_non_string_evidence_reverts_without_consuming_state(
    direct_vm, direct_deploy, evidence
):
    fixture = make_fixture("job-pass")
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    with direct_vm.expect_revert("MALFORMED_PAYLOAD"):
        contract.submit_job(
            fixture["job_id"], _json(fixture["policy"]), _json(fixture["agreement"]),
            _json(fixture["envelope"]), evidence,
        )
    assert contract.get_job(fixture["job_id"]) == ""
    assert contract.nonce_used(fixture["envelope"]["nonce"]) is False


@pytest.mark.parametrize(
    ("missing_field", "reason"),
    [("request", "REQUEST_DATA_MISSING"), ("response", "RESPONSE_DATA_MISSING")],
)
def test_direct_missing_request_or_response_is_rejected_before_evaluation(
    direct_vm, direct_deploy, missing_field, reason
):
    fixture = make_fixture("job-pass")
    uri = fixture["envelope"]["artifacts"][0]["uri"]
    artifact = json.loads(fixture["evidence"][uri].decode("utf-8"))
    artifact.pop(missing_field)
    evidence = _json(artifact)
    envelope = json.loads(json.dumps(fixture["envelope"]))
    envelope["artifacts"][0]["sha256"] = "sha256:" + __import__("hashlib").sha256(evidence.encode()).hexdigest()
    envelope["artifacts"][0]["snapshot_id"] = envelope["artifacts"][0]["sha256"]
    envelope[missing_field + "_hash"] = digest_json(None)
    result = _submit(_deploy_registered(direct_vm, direct_deploy, fixture), fixture, envelope, evidence)
    assert result["verdict"] == "REJECT"
    assert result["reason_code"] == reason
    assert result["evaluation_invoked"] is False


def test_direct_stale_evidence_is_a_stateful_deterministic_reject(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    stale = json.loads(json.dumps(fixture["envelope"]))
    stale["timestamp"] = "2026-09-01T00:00:00Z"
    stale_result = _submit(contract, fixture, stale)
    assert stale_result["verdict"] == "REJECT"
    assert stale_result["reason_code"] == "STALE_EVIDENCE"
    assert contract.nonce_used(stale["nonce"]) is True


def test_direct_agreement_digest_mismatch_is_technical_and_stateless(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    envelope = json.loads(json.dumps(fixture["envelope"]))
    envelope["agreement_digest"] = "sha256:" + "1" * 64
    with direct_vm.expect_revert("MALFORMED_PAYLOAD"):
        _submit(contract, fixture, envelope)
    assert contract.get_job(fixture["job_id"]) == ""
    assert contract.nonce_used(envelope["nonce"]) is False


def test_direct_registered_evidence_allowlist_is_enforced(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    policy = json.loads(json.dumps(fixture["policy"]))
    policy["evidence_allowlist"] = ["embedded://evidence/allowed"]
    envelope = json.loads(json.dumps(fixture["envelope"]))
    envelope["policy_digest"] = digest_json(policy)
    envelope["artifacts"][0]["uri"] = "embedded://evidence/outside.json"

    direct_vm.sender = _fixture_sender()
    contract = direct_deploy(CONTRACT_PATH)
    contract.register_job(fixture["job_id"], _json(policy), _json(fixture["agreement"]))
    result = _submit(contract, fixture, envelope, policy=policy)
    assert result["verdict"] == "REJECT"
    assert result["reason_code"] == "INVALID_ARTIFACT_URI"
    assert result["evaluation_invoked"] is False


def test_direct_evaluator_execution_failure_is_technical(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    # No LLM mock is installed. The direct runner raises its infrastructure
    # mock error; the contract must not turn that failure into a verdict.
    with pytest.raises(Exception):
        _submit(contract, fixture)
    assert contract.get_job(fixture["job_id"]) == ""
    assert contract.nonce_used(fixture["envelope"]["nonce"]) is False


def test_contract_decision_parser_does_not_establish_finality(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    deterministic = validate_submission(
        fixture["policy"], fixture["agreement"], fixture["envelope"], fixture["evidence"], now=FIXTURE_NOW
    )
    direct_vm.mock_llm(
        r".*",
        _json(
            {
                "verdict": "ACCEPT",
                "reason_code": "CRITERION_SATISFIED",
                "evidence_digest": deterministic.evidence_digest,
                "policy_digest": AcceptancePolicy.from_dict(fixture["policy"]).digest,
                "rationale": "accepted",
            }
        ),
    )
    raw = _submit(_deploy_registered(direct_vm, direct_deploy, fixture), fixture)
    decision = ProoflineDecision.from_dict(raw)
    assert decision.verdict == "ACCEPT"
    with pytest.raises(ReceiptError, match="lifecycle verification"):
        ProoflineReceipt.from_decision(
            decision,
            transaction_reference="0x" + "ab" * 32,
            protocol_status="FINALIZED",
            finality_status="FINALIZED",
            execution_result="FINISHED_WITH_RETURN",
            finalized_at=FIXTURE_NOW,
        )


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("nonce", 7, "MALFORMED_PAYLOAD"),
        ("timestamp", 7, "MALFORMED_PAYLOAD"),
        ("timestamp", "not-a-time", "MALFORMED_PAYLOAD"),
        ("request_hash", "sha256:null", "MALFORMED_PAYLOAD"),
        ("request_hash", "sha256:" + "A" * 64, "MALFORMED_PAYLOAD"),
    ],
)
def test_direct_malformed_envelope_reverts_before_evaluation(direct_vm, direct_deploy, field, value, error):
    fixture = make_fixture("job-pass")
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    envelope = json.loads(json.dumps(fixture["envelope"]))
    envelope[field] = value
    with direct_vm.expect_revert(error):
        _submit(contract, fixture, envelope)
    assert contract.get_job(fixture["job_id"]) == ""


def test_direct_missing_artifact_metadata_reverts_before_evaluation(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    contract = _deploy_registered(direct_vm, direct_deploy, fixture)
    envelope = json.loads(json.dumps(fixture["envelope"]))
    envelope["artifacts"][0].pop("media_type")
    with direct_vm.expect_revert("MALFORMED_PAYLOAD"):
        _submit(contract, fixture, envelope)
    assert contract.get_job(fixture["job_id"]) == ""
    assert contract.nonce_used(envelope["nonce"]) is False


def test_direct_missing_subjective_criterion_rejected_at_registration(direct_vm, direct_deploy):
    fixture = make_fixture("job-pass")
    policy = json.loads(json.dumps(fixture["policy"]))
    del policy["subjective_criterion"]
    direct_vm.sender = _fixture_sender()
    contract = direct_deploy(CONTRACT_PATH)
    with direct_vm.expect_revert("INVALID_REGISTRATION"):
        contract.register_job(fixture["job_id"], _json(policy), _json(fixture["agreement"]))


@pytest.mark.parametrize("mutation", ["unknown_requirement", "unsupported_artifact"])
def test_direct_registration_rejects_unenforceable_policy_fields(
    direct_vm, direct_deploy, mutation
):
    fixture = make_fixture("job-pass")
    policy = json.loads(json.dumps(fixture["policy"]))
    if mutation == "unknown_requirement":
        policy["deterministic_requirements"]["future_rule"] = True
    else:
        policy["required_artifacts"] = ["response", "logs"]
    direct_vm.sender = _fixture_sender()
    contract = direct_deploy(CONTRACT_PATH)
    with direct_vm.expect_revert("INVALID_REGISTRATION"):
        contract.register_job(fixture["job_id"], _json(policy), _json(fixture["agreement"]))
