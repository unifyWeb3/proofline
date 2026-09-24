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
    REGISTRATION_OWNER_ADDRESS,
    RPC_URL,
    READ_ADDRESS,
    _build_envelope_response,
    _build_submission_material,
    _config_payload,
    _fee_profile_options,
    _fixture_templates,
    _get_route,
    _post_route,
    _prepare_register,
    _prepare_submit,
    _safe_error_detail,
    _status_label,
    _unsigned_write,
    _validated_contract_inputs,
    wsgi_app,
)
from proofline.canonical import digest_json, sha256_bytes


ROOT = Path(__file__).parents[2]
PUBLIC_FRONTEND = ROOT / "frontend" / "public-release"


def test_frontend_status_mapping_keeps_processing_distinct_from_finality():
    assert _status_label(SimpleNamespace(protocol_status="PENDING", execution_result=None)) == "pending"
    assert _status_label(SimpleNamespace(protocol_status="PROPOSING", execution_result=None)) == "consensus"
    assert _status_label(SimpleNamespace(protocol_status="ACCEPTED", execution_result=None)) == "evaluating"
    assert _status_label(SimpleNamespace(protocol_status="FINALIZED", execution_result="FINISHED_WITH_RETURN")) == "finalized"
    assert _status_label(SimpleNamespace(protocol_status="FINALIZED", execution_result="ERROR")) == "technical_error"


def test_frontend_has_no_signing_material_or_public_secret_configuration():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "frontend").rglob("*")
        if path.is_file() and path.suffix in {".html", ".js", ".css", ".py"}
    )
    lowered = source.lower()
    for marker in ("private_key", "private key", "mnemonic", "seed phrase", "api_key", "password", "next_public", "vite_"):
        assert marker not in lowered
    assert re.search(r"(^|[/\\])\.env(?:$|[._:/])", lowered) is None
    assert "eth_sendtransaction" in lowered
    assert "window.ethereum" in lowered


def test_public_pages_and_verified_example_use_the_current_authoritative_job():
    status, home, content_type = _get_route("/", {})
    assert status == 200 and content_type.startswith("text/html")
    assert b"Verifiable acceptance for agent work" in home
    status, workspace, content_type = _get_route("/app", {})
    assert status == 200 and content_type.startswith("text/html")
    assert b"Structured receipt" in workspace or b"View receipt" in workspace

    evidence = json.loads(
        (ROOT / "evidence" / "milestone4-genuine-semanticfix-independent-verification.json").read_text()
    )
    example_source = (ROOT / "frontend" / "public-release" / "verified-example.js").read_text()
    job = re.search(r'job: "([^\"]+)"', example_source).group(1)
    transaction = re.search(r'transaction: "([^\"]+)"', example_source).group(1)
    assert job == evidence["submission"]["job_id"]
    assert transaction == evidence["submission"]["transaction"]
    assert evidence["contract_address"] == CONTRACT_ADDRESS


def test_vercel_wsgi_entrypoint_serves_pages_and_current_config():
    def request(path, method="GET", query="", body=b""):
        result = {}

        def start_response(status, headers):
            result["status"] = status
            result["headers"] = dict(headers)

        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": BytesIO(body),
        }
        result["body"] = b"".join(wsgi_app(environ, start_response))
        return result

    home = request("/")
    app = request("/app")
    config = request("/api/config")
    assert home["status"].startswith("200") and b"Verifiable acceptance" in home["body"]
    assert app["status"].startswith("200") and b"view-example" in app["body"]
    payload = json.loads(config["body"])
    assert payload["chain_id"] == 61997
    assert payload["contract_address"] == CONTRACT_ADDRESS
    assert payload["registration_owner_address"] == REGISTRATION_OWNER_ADDRESS


def test_browser_final_view_requires_verified_receipt():
    app = (PUBLIC_FRONTEND / "app.js").read_text(encoding="utf-8")
    assert 'if (data.receipt) renderResult(data);' in app
    assert "data.finality_observed" in app
    assert "/api/transaction?tx=" in app
    assert "Registration finalized" in app
    assert 'id="receipt-json"' in (PUBLIC_FRONTEND / "app.html").read_text(encoding="utf-8")
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


def test_registration_preparation_allows_deployed_owner(monkeypatch):
    templates = _fixture_templates("owner-register-test", REGISTRATION_OWNER_ADDRESS)
    captured = {}

    def fake_unsigned_write(body, function_name, args):
        captured.update(from_address=body["from"], function=function_name, args=args)
        return {"prepared": True}

    monkeypatch.setattr("frontend.server._unsigned_write", fake_unsigned_write)
    status, response = _post_route(
        "/api/prepare-register", {**templates, "from": REGISTRATION_OWNER_ADDRESS}
    )
    assert (status, response) == (200, {"prepared": True})
    assert captured["from_address"] == REGISTRATION_OWNER_ADDRESS
    assert captured["function"] == "register_job"
    assert captured["args"][0] == "owner-register-test"


def test_registration_preparation_rejects_non_owner_before_fee_estimation(monkeypatch):
    templates = _fixture_templates("unauthorized-register-test", REGISTRATION_OWNER_ADDRESS)
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("unauthorized registration must stop before fee preparation")

    monkeypatch.setattr("frontend.server._unsigned_write", fail_if_called)
    with pytest.raises(ValueError, match="Only the contract owner can register jobs"):
        _post_route(
            "/api/prepare-register",
            {**templates, "from": "0x0000000000000000000000000000000000000001"},
        )
    assert called is False


def test_submit_preparation_remains_authorized_by_policy_signer_not_owner(monkeypatch):
    submitter = "0x00000000000000000000000000000000000000a2"
    templates = _fixture_templates("separate-submit-authority", submitter)
    envelope = _build_envelope_response(templates)["envelope"]
    captured = {}

    def fake_unsigned_write(body, function_name, args):
        captured.update(from_address=body["from"], function=function_name)
        return {"prepared": True}

    monkeypatch.setattr("frontend.server._unsigned_write", fake_unsigned_write)
    assert _prepare_submit({**templates, "from": submitter, "envelope": envelope}) == {"prepared": True}
    assert captured == {"from_address": submitter, "function": "submit_job"}


def test_prepared_submission_uses_the_same_canonical_evidence_bytes_as_envelope(monkeypatch):
    templates = _fixture_templates("canonical-browser-job", REGISTRATION_OWNER_ADDRESS)
    evidence = json.loads(templates["evidence_content"])
    pretty_evidence = json.dumps(evidence, indent=2)
    body = {**templates, "from": REGISTRATION_OWNER_ADDRESS, "evidence_content": pretty_evidence}
    envelope = _build_envelope_response(body)["envelope"]
    captured = {}

    def fake_unsigned_write(_body, function_name, args):
        captured.update(function=function_name, args=args)
        return {"prepared": True}

    monkeypatch.setattr("frontend.server._unsigned_write", fake_unsigned_write)
    assert _prepare_submit({**body, "envelope": envelope}) == {"prepared": True}
    assert captured["function"] == "submit_job"
    assert captured["args"][4] != pretty_evidence
    assert sha256_bytes(captured["args"][4].encode("utf-8")) == envelope["artifacts"][0]["sha256"]


def test_submit_rejects_wrong_job_or_stale_envelope_before_fee_preparation(monkeypatch):
    templates = _fixture_templates("canonical-browser-job", REGISTRATION_OWNER_ADDRESS)
    envelope = _build_envelope_response(templates)["envelope"]

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("fee preparation must not run for mismatched evidence")

    monkeypatch.setattr("frontend.server._unsigned_write", fail_if_called)
    wrong_job = json.loads(templates["evidence_content"])
    wrong_job["job_id"] = "another-job"
    with pytest.raises(ValueError, match="evidence_content.job_id must equal job_id"):
        _prepare_submit({**templates, "from": REGISTRATION_OWNER_ADDRESS, "envelope": envelope, "evidence_content": json.dumps(wrong_job)})
    changed_evidence = json.loads(templates["evidence_content"])
    changed_evidence["response"]["body"]["answer"] = "Changed after envelope construction"
    with pytest.raises(ValueError, match="rebuild the envelope"):
        _prepare_submit({**templates, "from": REGISTRATION_OWNER_ADDRESS, "envelope": envelope, "evidence_content": json.dumps(changed_evidence)})


def test_public_config_exposes_owner_for_registration_guard():
    assert _config_payload() == {
        "chain_id": CHAIN_ID,
        "rpc_url": RPC_URL,
        "contract_address": CONTRACT_ADDRESS,
        "registration_owner_address": REGISTRATION_OWNER_ADDRESS,
    }
    assert REGISTRATION_OWNER_ADDRESS == "0x3211d1419709682b81c53CC51cb63622E25488d3"


def test_browser_registration_is_owner_gated_but_submission_is_not_owner_gated():
    app = (ROOT / "frontend" / "public-release" / "app.js").read_text(encoding="utf-8")
    html = (ROOT / "frontend" / "public-release" / "app.html").read_text(encoding="utf-8")
    assert "registration_owner_address" in app
    assert "account.toLowerCase() === registrationOwner.toLowerCase()" in app
    assert "Only the contract owner" in app
    assert "Evidence submission is separately authorized by the policy signer." in app
    assert 'id="register"' in html and " disabled>Register with wallet" in html
    assert "if (body.policy.signer_address.toLowerCase() !== account.toLowerCase())" in app
    submit_handler = app.split("async function submit()", 1)[1].split("async function buildEnvelope()", 1)[0]
    assert "registrationOwner" not in submit_handler


def test_provider_error_detail_is_useful_but_redacts_credentials_and_urls():
    detail = _safe_error_detail(ValueError("sim_estimateTransactionFees failed (code=-32000): api_key=secret123 https://user:pass@example.invalid/api"))
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
        "leaderTimeunitsAllocation": 157,
        "validatorTimeunitsAllocation": 313,
        "executionBudgetPerRound": 99855000000000,
        "totalMessageFees": 0,
        "rotations": [3],
    }
    assert result["fee_value_wei"] == 123456789
    assert result["fee_distribution"]["executionBudgetPerRound"] == 99855000000000
    assert result["fee_profile"]["method"] == "submit_job"


def test_studio_dev_fee_profile_is_chain_bound_and_contains_both_write_entries():
    register = _fee_profile_options("register_job")
    submit = _fee_profile_options("submit_job")
    assert register["executionBudgetPerRound"] == 98285000000000
    assert submit["executionBudgetPerRound"] == 99855000000000
    assert register["rotations"] == [3] == submit["rotations"]


def test_frontend_fee_target_is_the_official_studio_dev_identity():
    assert CHAIN_ID == 61997
    assert RPC_URL == "https://studio-dev.genlayer.com/api"
    assert CONTRACT_ADDRESS == "0x30829d13D0d86a9ae83Dc5e832Fb4AADd43Df26b"


def test_frontend_documents_real_fixture_templates_and_safe_error_details():
    html = (PUBLIC_FRONTEND / "app.html").read_text(encoding="utf-8")
    app = (PUBLIC_FRONTEND / "app.js").read_text(encoding="utf-8")
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
