from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from frontend.server import (
    CHAIN_ID,
    CONTRACT_ADDRESS,
    RPC_URL,
    READ_ADDRESS,
    _build_envelope_response,
    _build_submission_material,
    _fee_profile_options,
    _fixture_templates,
    _post_route,
    _prepare_register,
    _safe_error_detail,
    _status_label,
    _unsigned_write,
    _validated_contract_inputs,
    wsgi_app,
)
from proofline.canonical import digest_json, sha256_bytes


ROOT = Path(__file__).parents[2]


def test_frontend_status_mapping_keeps_processing_distinct_from_finality():
    assert _status_label(SimpleNamespace(protocol_status="PENDING", execution_result=None)) == "pending"
    assert _status_label(SimpleNamespace(protocol_status="PROPOSING", execution_result=None)) == "consensus"
    assert _status_label(SimpleNamespace(protocol_status="ACCEPTED", execution_result=None)) == "evaluating"
    assert _status_label(SimpleNamespace(protocol_status="FINALIZED", execution_result="FINISHED_WITH_RETURN")) == "finalized"
    assert _status_label(SimpleNamespace(protocol_status="FINALIZED", execution_result="ERROR")) == "technical_error"


def test_frontend_has_no_signing_material_or_public_secret_configuration():
    source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "frontend").iterdir() if path.is_file())
    lowered = source.lower()
    for marker in ("private_key", "private key", "mnemonic", "seed phrase", "api_key", "password", "next_public", "vite_"):
        assert marker not in lowered
    assert re.search(r"(^|[/\\])\.env(?:$|[._:/])", lowered) is None
    assert "eth_sendtransaction" in lowered
    assert "window.ethereum" in lowered


def test_browser_final_view_requires_verified_receipt():
    app = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    assert 'if (data.receipt) renderResult(data);' in app
    assert "data.finality_observed" in app
    assert "/api/transaction?tx=" in app
    assert "Registration finalized" in app
    assert 'id="receipt-json"' in (ROOT / "frontend" / "app.html").read_text(encoding="utf-8")
    assert "receipt_verification" in app
    assert 'textContent = JSON.stringify(r, null, 2)' in app
    assert "download-receipt" in app
    assert "localStorage" not in app and "sessionStorage" not in app
    assert READ_ADDRESS == "0x0000000000000000000000000000000000000001"


def test_submission_builder_derives_matching_digests_from_evidence():
    policy = {
        "schema_version": "proofline.policy.v1",
        "policy_version": "acceptance-policy.v1",
        "agreement_id": "fresh-job",
        "deadline": 4102444800,
        "signer_address": "0x3211d1419709682b81c53CC51cb63622E25488d3",
        "required_artifacts": ["response"],
        "deterministic_requirements": {"require_snapshot": True},
        "subjective_criterion": {"id": "criterion", "statement": "Assess the response.", "allowed_verdicts": ["ACCEPT", "REJECT", "UNDETERMINED"]},
        "evidence_allowlist": ["embedded://evidence"],
    }
    agreement = {"schema_version": "proofline.agreement.v1", "agreement_id": "fresh-job", "policy_version": "acceptance-policy.v1", "parties": {"buyer": "buyer", "worker": "worker"}}
    evidence = {"request": {"question": "What happened?"}, "response": {"status": 200, "body": {"answer": "Observed evidence."}}}
    result = _build_submission_material({"job_id": "fresh-job", "policy": policy, "agreement": agreement, "evidence_content": json.dumps(evidence)})
    envelope = result["envelope"]
    content_digest = sha256_bytes(result["evidence_content"].encode("utf-8"))
    assert envelope["artifacts"][0]["sha256"] == content_digest
    assert envelope["artifacts"][0]["snapshot_id"] == content_digest
    assert envelope["request_hash"] == digest_json(evidence["request"])
    assert envelope["response_hash"] == digest_json(evidence["response"])
    assert result["evidence_digest"] == digest_json({"artifacts": [{"artifact_id": "response", "sha256": content_digest}]})
    assert "replace-with-real-digest" not in json.dumps(result)


def test_build_envelope_route_accepts_genuine_request_response_evidence():
    templates = _fixture_templates("browser-route-test", "0x3211d1419709682b81c53CC51cb63622E25488d3")
    status, result = _post_route("/api/build-envelope", templates)
    assert status == 200
    assert result["envelope"]["job_id"] == "browser-route-test"
    assert result["envelope"]["request_hash"] == digest_json(json.loads(templates["evidence_content"])["request"])
    assert result["envelope"]["response_hash"] == digest_json(json.loads(templates["evidence_content"])["response"])
    assert result["envelope"]["artifacts"][0]["sha256"].startswith("sha256:")
    assert "evidence_content" not in result


def test_registration_validation_rejects_evidence_in_policy_before_fee_preparation(monkeypatch):
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("fee preparation must not run for malformed input")

    monkeypatch.setattr("frontend.server._unsigned_write", fail_if_called)
    with pytest.raises(ValueError, match="invalid policy or agreement"):
        _prepare_register({
            "from": "0x3211d1419709682b81c53CC51cb63622E25488d3",
            "job_id": "valid-job",
            "policy": {"request": {"question": "wrong field"}},
            "agreement": {"request": {"question": "wrong field"}},
        })
    assert called is False


def test_registration_validation_requires_plain_job_id_and_matching_schema_identity(monkeypatch):
    templates = _fixture_templates("valid-job_01", "0x3211d1419709682b81c53CC51cb63622E25488d3")
    with pytest.raises(ValueError, match="plain identifier"):
        _validated_contract_inputs({**templates, "job_id": "{\"evidence\": true}"})
    bad_policy = dict(templates["policy"])
    bad_policy["agreement_id"] = "other-job"
    with pytest.raises(ValueError, match="policy.agreement_id"):
        _validated_contract_inputs({**templates, "policy": bad_policy})


def test_provider_error_detail_is_useful_but_redacts_credentials_and_urls():
    detail = _safe_error_detail(ValueError("sim_estimateTransactionFees failed (code=-32000): api_key=secret123 " + "https://" + "user:pass" + "@example.invalid/api"))
    assert "sim_estimateTransactionFees" in detail
    assert "secret123" not in detail
    assert "user:pass" not in detail
    assert "[REDACTED]" in detail
    assert "[provider-url]" in detail


def test_studio_dev_fee_path_uses_measured_profile_and_sdk_returned_quote(monkeypatch):
    captured = {}

    class Provider:
        def make_request(self, method, params):
            assert method == "eth_estimateGas"
            return {"result": "0x5208"}

    class Chain:
        default_consensus_max_rotations = 3
        consensus_main_contract = {"address": "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575"}

    class Client:
        chain = Chain()
        provider = Provider()

        def estimate_transaction_fees(self, *, options):
            captured["options"] = options
            return {
                "distribution": {
                    "leaderTimeunitsAllocation": options["leaderTimeunitsAllocation"],
                    "validatorTimeunitsAllocation": options["validatorTimeunitsAllocation"],
                    "appealRounds": 0,
                    "executionBudgetPerRound": options["executionBudgetPerRound"],
                    "executionConsumed": 0,
                    "totalMessageFees": options["totalMessageFees"],
                    "rotations": options["rotations"],
                    "maxPriceGenPerTimeUnit": 2,
                    "storageFeeMaxGasPrice": 300000000,
                    "receiptFeeMaxGasPrice": 300000000,
                },
                "feeValue": 123456789,
            }

        def estimate_transaction_fees_for_write(self, **kwargs):
            raise AssertionError("Studio fee path must not require target-write simulation")

    monkeypatch.setattr("frontend.server._client", lambda address: Client())
    monkeypatch.setattr("frontend.server._encode_add_transaction_data", lambda **kwargs: "0xencoded")
    result = _unsigned_write({"from": "0x3211d1419709682b81c53CC51cb63622E25488d3"}, "submit_job", [])
    assert captured["options"] == {
        "leaderTimeunitsAllocation": 125,
        "validatorTimeunitsAllocation": 250,
        "executionBudgetPerRound": 299000000000000,
        "totalMessageFees": 0,
        "rotations": [3],
    }
    assert result["fee_value_wei"] == 123456789
    assert result["fee_distribution"]["executionBudgetPerRound"] == 299000000000000
    assert result["fee_profile"]["method"] == "submit_job"


def test_studio_dev_fee_profile_is_chain_bound_and_contains_both_write_entries():
    register = _fee_profile_options("register_job")
    submit = _fee_profile_options("submit_job")
    assert register["executionBudgetPerRound"] == 98285000000000
    assert submit["executionBudgetPerRound"] == 299000000000000
    assert register["rotations"] == [3] == submit["rotations"]


def test_frontend_fee_target_is_the_official_studio_dev_identity():
    assert CHAIN_ID == 61997
    assert RPC_URL == "https://studio-dev.genlayer.com/api"
    assert CONTRACT_ADDRESS == "0x6eb8E208666694e9948E87aa46294aA349fD2014"


def test_vercel_wsgi_entrypoint_reuses_config_and_envelope_routes():
    from app import app as vercel_app

    assert vercel_app is wsgi_app
    responses = []

    def start_response(status, headers):
        responses.append((status, dict(headers)))

    config_body = b"".join(
        wsgi_app(
            {"REQUEST_METHOD": "GET", "PATH_INFO": "/api/config", "QUERY_STRING": ""},
            start_response,
        )
    )
    assert responses[-1][0] == "200 OK"
    assert json.loads(config_body)["chain_id"] == 61997

    templates = _fixture_templates("wsgi-audit-1", "0x3211d1419709682b81c53CC51cb63622E25488d3")
    body = json.dumps(templates).encode()
    environ = {
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": BytesIO(body),
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/api/build-envelope",
        "QUERY_STRING": "",
    }
    envelope_body = b"".join(wsgi_app(environ, start_response))
    assert responses[-1][0] == "200 OK"
    assert json.loads(envelope_body)["envelope"]["schema_version"] == "proofline.response.v1"


def test_public_root_and_app_routes_are_distinct_and_refreshable():
    responses = []

    def start_response(status, headers):
        responses.append((status, dict(headers)))

    root_body = b"".join(wsgi_app({"REQUEST_METHOD": "GET", "PATH_INFO": "/", "QUERY_STRING": ""}, start_response)).decode()
    assert responses[-1][0] == "200 OK"
    assert "Verifiable acceptance for agent work." in root_body
    assert "Launch App" in root_body
    assert 'id="job-id"' not in root_body

    app_body = b"".join(wsgi_app({"REQUEST_METHOD": "GET", "PATH_INFO": "/app", "QUERY_STRING": ""}, start_response)).decode()
    assert responses[-1][0] == "200 OK"
    assert 'id="job-id"' in app_body
    assert 'id="receipt-json"' in app_body
    assert "Define" in app_body and "Submit" in app_body and "Verify" in app_body

    for asset in ("/styles.css", "/home.js", "/verified-example.js", "/app.js"):
        body = b"".join(wsgi_app({"REQUEST_METHOD": "GET", "PATH_INFO": asset, "QUERY_STRING": ""}, start_response))
        assert responses[-1][0] == "200 OK"
        assert body


def test_frontend_documents_real_fixture_templates_and_safe_error_details():
    html = (ROOT / "frontend" / "app.html").read_text(encoding="utf-8")
    app = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    assert "Known-good Milestone 2 JSON shapes" in html
    assert "Known-good evidence JSON shape" in html
    assert "proofline.policy.v1" in html
    assert "proofline.agreement.v1" in html
    assert "/api/templates?job=" in app
    assert "Policy must match proofline.policy.v1" in app
    assert "data.detail" in app


def test_public_evidence_has_no_unredacted_secret_values_and_raw_hashes_are_separate():
    manifest = json.loads((ROOT / "evidence" / "secret-hygiene-manifest.json").read_text())
    markers = ("private_key", "privatekey", "api_key", "apikey", "access_token", "auth_token", "password", "mnemonic", "seed_phrase", "client_secret", "signing_secret")

    def is_secret_key(key: str) -> bool:
        lowered = key.lower()
        return lowered in {"secret", "seed", "credential", "credentials"} or any(
            lowered == marker or lowered.endswith("_" + marker) for marker in markers
        )

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if is_secret_key(str(key)):
                    assert item == "[REDACTED BY SECRET-HYGIENE]"
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)

    assert manifest["entries"]
    assert all(entry["original_file_sha256"] != entry["sanitized_file_sha256"] for entry in manifest["entries"] if entry["secret_key_occurrences_redacted"])
    for entry in manifest["entries"]:
        public = ROOT / entry["sanitized_path"]
        list(walk(json.loads(public.read_text(encoding="utf-8"))))
