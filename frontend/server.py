"""Small same-origin API for the Proofline connected frontend.

The browser receives unsigned transaction data and asks its injected wallet
provider to sign it. This process has no signing account. Readback and receipt
construction stay in the existing Python lifecycle and adapter modules.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from genlayer_py.chains.studio_devnet import studio_devnet  # noqa: E402
from genlayer_py.client.genlayer_client import GenLayerClient  # noqa: E402
from genlayer_py.consensus.consensus_main.encoder import encode_tx_data_call  # noqa: E402
from genlayer_py.contracts.actions import _encode_add_transaction_data  # noqa: E402
from genlayer_py.transactions.fees import normalize_transaction_fees  # noqa: E402
from genlayer_py.types import TransactionHashVariant  # noqa: E402

from proofline.adapters import LocalAdapter  # noqa: E402
from proofline.canonical import canonical_json, digest_json, sha256_bytes, strict_json_loads  # noqa: E402
from proofline.errors import ProtocolError, SchemaError  # noqa: E402
from proofline.lifecycle import GenLayerLifecycleClient  # noqa: E402
from proofline.receipt import ProoflineDecision, SUCCESSFUL_EXECUTION_RESULT  # noqa: E402
from proofline.schemas import AcceptancePolicy, Agreement  # noqa: E402

CONTRACT_ADDRESS = "0x30829d13D0d86a9ae83Dc5e832Fb4AADd43Df26b"
CHAIN_ID = 61997
# The corrected deployment transaction's finalized sender is the owner
# initialized onchain by the exact contract constructor. This is a preparation
# guard only; the contract remains the authority.
REGISTRATION_OWNER_ADDRESS = "0x3211d1419709682b81c53CC51cb63622E25488d3"
RPC_URL = studio_devnet.rpc_urls["default"]["http"][0]
if studio_devnet.id != CHAIN_ID or RPC_URL != "https://studio-dev.genlayer.com/api":
    raise RuntimeError("installed RC Studio-dev chain definition does not match Proofline target")
FEE_PROFILE_PATH = ROOT / "evidence" / "fee-profile-milestone4-semanticfix.json"
FRONTEND_ROOT = Path(__file__).resolve().parent
PUBLIC_FRONTEND_ROOT = FRONTEND_ROOT / "public-release"
MAX_BODY_BYTES = 512 * 1024
READ_ADDRESS = "0x0000000000000000000000000000000000000001"
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SENSITIVE_ERROR_FIELDS = (
    "private" + r"[_ -]?key",
    "api" + r"[_ -]?key",
    "access" + r"[_ -]?token",
    "auth" + r"[_ -]?token",
    "pass" + "word",
    "mnemo" + "nic",
    "seed",
    "secret",
    "credential",
)
_SENSITIVE_ERROR_PATTERN = re.compile(
    r"(?i)(" + "|".join(_SENSITIVE_ERROR_FIELDS) + r")(\s*[:=]\s*)('[^']*'|\"[^\"]*\"|[^,;\s]+)"
)
_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)


class BadRequest(ValueError):
    pass


def _safe_error_detail(exc: Exception) -> str:
    """Return useful provider detail without echoing credentials or request data."""
    detail = str(exc).strip() or type(exc).__name__
    detail = _SENSITIVE_ERROR_PATTERN.sub(r"\1\2[REDACTED]", detail)
    detail = _URL_PATTERN.sub("[provider-url]", detail)
    return detail[:600]


def _account(address: str) -> SimpleNamespace:
    if not isinstance(address, str) or len(address) != 42 or not address.startswith("0x"):
        raise BadRequest("from must be an EVM address")
    try:
        int(address[2:], 16)
    except ValueError as exc:
        raise BadRequest("from must be an EVM address") from exc
    return SimpleNamespace(address=address)


def _client(address: str | None = None) -> GenLayerClient:
    client = GenLayerClient(studio_devnet, account=_account(address) if address else None)
    client.initialize_consensus_smart_contract()
    return client


def _fee_profile_options(function_name: str) -> dict[str, Any]:
    """Load measured RC profile inputs; arithmetic stays inside the SDK."""
    try:
        profile = json.loads(FEE_PROFILE_PATH.read_text(encoding="utf-8"))
        if profile.get("chainId") != CHAIN_ID:
            raise ProtocolError("fee profile chain does not match Studio-dev")
        measured = profile["methods"][function_name]
        return {
            "leaderTimeunitsAllocation": int(measured["leaderTimeunitsAllocation"]),
            "validatorTimeunitsAllocation": int(measured["validatorTimeunitsAllocation"]),
            "executionBudgetPerRound": int(measured["executionBudgetPerRound"]),
            "totalMessageFees": int(measured["totalMessageFees"]),
            "rotations": [int(measured["rotationsPerRound"])],
        }
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ProtocolError(f"missing or invalid measured fee profile for {function_name}") from exc


def _json_object(value: Any, field: str) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise BadRequest(f"{field} must contain JSON") from exc
    if not isinstance(value, dict):
        raise BadRequest(f"{field} must be a JSON object")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _validated_job_id(value: Any) -> str:
    if not isinstance(value, str) or not JOB_ID_PATTERN.fullmatch(value):
        raise BadRequest(
            "job_id must be a plain identifier matching ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
        )
    return value


def _validated_contract_inputs(body: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Validate the exact policy/agreement schemas before any provider call."""
    job_id = _validated_job_id(_required(body, "job_id"))
    try:
        policy_value = strict_json_loads(_json_object(_required(body, "policy"), "policy"))
        agreement_value = strict_json_loads(_json_object(_required(body, "agreement"), "agreement"))
        policy = AcceptancePolicy.from_dict(policy_value)
        agreement = Agreement.from_dict(agreement_value)
    except (TypeError, SchemaError) as exc:
        raise BadRequest(f"invalid policy or agreement: {_safe_error_detail(exc)}") from exc
    if policy.agreement_id != job_id:
        raise BadRequest("policy.agreement_id must equal job_id")
    if agreement.agreement_id != job_id:
        raise BadRequest("agreement.agreement_id must equal job_id")
    if agreement.policy_version != policy.policy_version:
        raise BadRequest("agreement.policy_version must equal policy.policy_version")
    return job_id, policy_value, agreement_value


def _fixture_templates(job_id: str, signer_address: str) -> dict[str, Any]:
    """Build the exact job-pass fixture shape with fresh identity fields."""
    job_id = _validated_job_id(job_id)
    signer = _account(signer_address).address
    policy = {
        "schema_version": "proofline.policy.v1",
        "policy_version": "acceptance-policy.v1",
        "agreement_id": job_id,
        "deadline": int(datetime.now(timezone.utc).timestamp()) + 3600,
        "signer_address": signer,
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
    agreement_unsigned = {
        "schema_version": "proofline.agreement.v1",
        "agreement_id": job_id,
        "policy_version": "acceptance-policy.v1",
        "parties": {"buyer": "buyer-fixture", "worker": "worker-fixture"},
    }
    agreement = dict(agreement_unsigned)
    agreement["agreement_digest"] = digest_json(agreement_unsigned)
    evidence = {
        "job_id": job_id,
        "request": {
            "method": "POST",
            "path": "/v1/report",
            "body": {"question": "What failed and how should it be fixed?"},
        },
        "response": {
            "status": 200,
            "content_type": "application/json",
            "body": {
                "answer": "The report identifies the root cause, cites the observed failure, and gives a concrete remediation.",
                "evidence": ["fixture-log-001", "fixture-test-001"],
            },
        },
    }
    return {"job_id": job_id, "policy": policy, "agreement": agreement, "evidence_content": canonical_json(evidence)}


def _build_submission_material(body: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize user evidence and derive every envelope digest from it."""
    job_id, policy, agreement = _validated_contract_inputs(body)
    try:
        evidence = strict_json_loads(_required(body, "evidence_content"))
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"evidence_content must be strict JSON: {_safe_error_detail(exc)}") from exc
    if not isinstance(evidence, dict):
        raise BadRequest("evidence_content must be a JSON object")
    if not isinstance(evidence.get("request"), dict) or not isinstance(evidence.get("response"), dict):
        raise BadRequest("evidence_content must contain request and response objects")
    if "job_id" in evidence and evidence["job_id"] != job_id:
        raise BadRequest("evidence_content.job_id must equal job_id")
    policy_digest = digest_json(policy)
    agreement_digest = digest_json({key: value for key, value in agreement.items() if key != "agreement_digest"})
    if agreement.get("agreement_digest") not in (None, agreement_digest):
        raise BadRequest("agreement_digest does not match canonical agreement")
    evidence_content = canonical_json(evidence)
    content_digest = sha256_bytes(evidence_content.encode("utf-8"))
    artifact_uri = f"embedded://evidence/{job_id}.json"
    evidence_digest = digest_json({"artifacts": [{"artifact_id": "response", "sha256": content_digest}]})
    timestamp = datetime.now(timezone.utc)
    envelope = {
        "schema_version": "proofline.response.v1",
        "job_id": job_id,
        "policy_version": policy.get("policy_version"),
        "agreement_digest": agreement_digest,
        "policy_digest": policy_digest,
        "artifacts": [{
            "artifact_id": "response",
            "uri": artifact_uri,
            "sha256": content_digest,
            "required": True,
            "snapshot_id": content_digest,
            "media_type": "application/json",
        }],
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "deadline": policy.get("deadline"),
        "nonce": f"{job_id}-{int(timestamp.timestamp() * 1000)}",
        "request_hash": digest_json(evidence["request"]),
        "response_hash": digest_json(evidence["response"]),
        "signer": {"address": policy.get("signer_address"), "signature": ""},
    }
    return {
        "policy_digest": policy_digest,
        "agreement_digest": agreement_digest,
        "evidence_digest": evidence_digest,
        "evidence_content": evidence_content,
        "envelope": envelope,
    }


def _build_envelope_response(body: dict[str, Any]) -> dict[str, Any]:
    material = _build_submission_material(body)
    material.pop("evidence_content", None)
    return material


def _required(body: dict[str, Any], field: str) -> Any:
    value = body.get(field)
    if value is None or value == "":
        raise BadRequest(f"{field} is required")
    return value


def _unsigned_write(body: dict[str, Any], function_name: str, args: list[Any]) -> dict[str, Any]:
    sender = _account(_required(body, "from"))
    client = _client(sender.address)
    fee_profile_options = _fee_profile_options(function_name)
    estimate = client.estimate_transaction_fees(options=fee_profile_options)
    tx_data = encode_tx_data_call(function_name, False, args=args)
    fees = normalize_transaction_fees(estimate)
    encoded_data = _encode_add_transaction_data(
        self=client,
        sender_account=sender,
        recipient=CONTRACT_ADDRESS,
        consensus_max_rotations=client.chain.default_consensus_max_rotations,
        data=tx_data,
        transaction_fees=fees,
    )
    fee_value = int(fees.get("fee_value") or 0)
    tx = {
        "from": sender.address,
        "to": client.chain.consensus_main_contract["address"],
        "data": encoded_data,
        "value": hex(fee_value),
        "gasPrice": "0x0",
        "chainId": hex(CHAIN_ID),
    }
    tx["gas"] = client.provider.make_request("eth_estimateGas", params=[tx])["result"]
    return {
        "transaction": tx,
        "chain_id": CHAIN_ID,
        "contract_address": CONTRACT_ADDRESS,
        "function": function_name,
        "fee_distribution": fees["distribution"],
        "fee_value_wei": fee_value,
        "fee_profile": {
            "source": str(FEE_PROFILE_PATH.relative_to(ROOT)),
            "method": function_name,
            "options": fee_profile_options,
        },
    }


def _prepare_register(body: dict[str, Any]) -> dict[str, Any]:
    sender = _account(_required(body, "from"))
    # A caller-supplied address is not proof of wallet control. This avoids
    # fee preparation for known unauthorized callers; register_job's onchain
    # owner check is still the final authority after wallet signing.
    if sender.address.lower() != REGISTRATION_OWNER_ADDRESS.lower():
        raise BadRequest("Only the contract owner can register jobs on this deployed Proofline contract.")
    job_id, policy, agreement = _validated_contract_inputs(body)
    return _unsigned_write(
        body,
        "register_job",
        [job_id, _json_object(policy, "policy"), _json_object(agreement, "agreement")],
    )


def _config_payload() -> dict[str, Any]:
    return {
        "chain_id": CHAIN_ID,
        "rpc_url": RPC_URL,
        "contract_address": CONTRACT_ADDRESS,
        "registration_owner_address": REGISTRATION_OWNER_ADDRESS,
    }


def _prepare_submit(body: dict[str, Any]) -> dict[str, Any]:
    job_id, policy, agreement = _validated_contract_inputs(body)
    sender = _account(_required(body, "from"))
    if sender.address.lower() != policy["signer_address"].lower():
        raise BadRequest("from must match policy.signer_address for submit_job")
    material = _build_submission_material(body)
    envelope = strict_json_loads(_json_object(_required(body, "envelope"), "envelope"))
    expected = material["envelope"]
    for field in ("schema_version", "job_id", "policy_version", "agreement_digest", "policy_digest", "artifacts", "deadline", "request_hash", "response_hash", "signer"):
        if envelope.get(field) != expected[field]:
            raise BadRequest(f"envelope.{field} does not match canonical submission material; rebuild the envelope")
    args = [
        job_id,
        _json_object(policy, "policy"),
        _json_object(agreement, "agreement"),
        _json_object(envelope, "envelope"),
        material["evidence_content"],
    ]
    return _unsigned_write(body, "submit_job", args)


def _post_route(path: str, body: dict[str, Any]) -> tuple[int, Any]:
    """Dispatch POST API paths independently of the HTTP transport."""
    path = urlparse(path).path
    if path == "/api/prepare-register":
        return 200, _prepare_register(body)
    if path == "/api/prepare-submit":
        return 200, _prepare_submit(body)
    if path == "/api/build-envelope":
        return 200, _build_envelope_response(body)
    return 404, {"error": "not found"}


def _get_route(path: str, query: dict[str, list[str]]) -> tuple[int, Any, str]:
    """Serve current public pages/assets and existing read-only API routes."""
    if path == "/api/config":
        return 200, _config_payload(), "application/json"
    if path == "/api/templates":
        return 200, _fixture_templates(query["job"][0], query["signer"][0]), "application/json"
    if path == "/api/lifecycle":
        return 200, _lifecycle_response(query["tx"][0], query["job"][0]), "application/json"
    if path == "/api/transaction":
        return 200, _transaction_response(query["tx"][0]), "application/json"
    if path == "/api/readback":
        return 200, _job_readback(query["job"][0], query["from"][0]), "application/json"

    if path in {"/", "/index.html"}:
        file_path = PUBLIC_FRONTEND_ROOT / "index.html"
        content_type = "text/html; charset=utf-8"
    elif path in {"/app", "/app/", "/app.html"}:
        file_path = PUBLIC_FRONTEND_ROOT / "app.html"
        content_type = "text/html; charset=utf-8"
    else:
        static_assets = {
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/home.js": ("home.js", "text/javascript; charset=utf-8"),
            "/verified-example.js": ("verified-example.js", "text/javascript; charset=utf-8"),
            "/styles.css": ("styles.css", "text/css; charset=utf-8"),
        }
        asset = static_assets.get(path)
        if asset is None:
            return 404, {"error": "not found"}, "application/json"
        file_name, content_type = asset
        file_path = PUBLIC_FRONTEND_ROOT / file_name
    try:
        return 200, file_path.read_bytes(), content_type
    except FileNotFoundError:
        return 404, {"error": "not found"}, "application/json"


def _status_label(snapshot: Any) -> str:
    if snapshot.execution_result and snapshot.execution_result not in {
        "NOT_STARTED", "PENDING", "PROCESSING", "FINISHED_WITH_RETURN"
    } and "ERROR" in snapshot.execution_result.upper():
        return "technical_error"
    if snapshot.protocol_status == "FINALIZED":
        return "finalized"
    if snapshot.protocol_status in {"PROPOSING", "COMMITTING", "REVEALING", "APPEAL_REVEALING", "APPEAL_COMMITTING"}:
        return "consensus"
    if snapshot.protocol_status in {"ACCEPTED", "UNDETERMINED", "READY_TO_FINALIZE"}:
        return "evaluating"
    return "pending"


def _lifecycle_response(tx_hash: str, job_id: str) -> dict[str, Any]:
    client = _client(READ_ADDRESS)
    lifecycle = GenLayerLifecycleClient(client)
    snapshot = lifecycle.inspect(tx_hash)
    response: dict[str, Any] = {
        "transaction_hash": snapshot.transaction_reference,
        "protocol_status": snapshot.protocol_status,
        "execution_result": snapshot.execution_result,
        "consensus_result": snapshot.consensus_result,
        "appeal_status": snapshot.appeal_status,
        "state": _status_label(snapshot),
        "finality_observed": snapshot.finality_observed,
        "chain_id": snapshot.chain_id,
        "contract_address": CONTRACT_ADDRESS,
        "receipt_verification": "pending",
    }
    if not snapshot.finality_observed:
        return response
    try:
        receipt = lifecycle.finalized_receipt(
            tx_hash,
            contract_address=CONTRACT_ADDRESS,
            decision_args=[job_id],
        )
        signal = LocalAdapter(lifecycle_client=lifecycle).apply(receipt)
    except ProtocolError as exc:
        response.update({"state": "technical_error", "error": str(exc), "receipt_verification": "failed"})
        return response
    response.update(
        {
            "state": "semantic_rejection" if receipt.verdict == "REJECT" else "finalized",
            "decision": receipt.decision().to_dict(),
            "receipt": receipt.to_dict(),
            "receipt_verification": "passed",
            "adapter": signal.to_dict(),
            "adapter_readiness": signal.signal,
            "authoritative_finality": receipt.finalized_at,
            "successful_execution": receipt.execution_result == SUCCESSFUL_EXECUTION_RESULT,
        }
    )
    return response


def _transaction_response(tx_hash: str) -> dict[str, Any]:
    lifecycle = GenLayerLifecycleClient(_client(READ_ADDRESS))
    snapshot = lifecycle.inspect(tx_hash)
    state = _status_label(snapshot)
    if snapshot.finality_observed and snapshot.execution_result != SUCCESSFUL_EXECUTION_RESULT:
        state = "technical_error"
    return {
        "transaction_hash": snapshot.transaction_reference,
        "protocol_status": snapshot.protocol_status,
        "execution_result": snapshot.execution_result,
        "consensus_result": snapshot.consensus_result,
        "appeal_status": snapshot.appeal_status,
        "state": state,
        "finality_observed": snapshot.finality_observed,
        "chain_id": snapshot.chain_id,
    }


def _job_readback(job_id: str, address: str) -> dict[str, Any]:
    client = _client(address)
    raw = client.read_contract(
        address=CONTRACT_ADDRESS,
        function_name="get_job",
        args=[job_id],
        transaction_hash_variant=TransactionHashVariant.LATEST_FINAL,
    )
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    decision = ProoflineDecision.from_dict(json.loads(raw) if isinstance(raw, str) else raw)
    if decision.contract_address.lower() != CONTRACT_ADDRESS.lower() or decision.chain_id != CHAIN_ID:
        raise ProtocolError("LATEST_FINAL decision identity does not match configured contract")
    return {"job_id": job_id, "decision": decision.to_dict(), "read_variant": "latest-final"}


class Handler(BaseHTTPRequestHandler):
    server_version = "ProoflineFrontend/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, status: int, payload: Any, content_type: str = "application/json") -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY_BYTES:
            raise BadRequest("request body is too large")
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise BadRequest("request body must be JSON") from exc
        if not isinstance(value, dict):
            raise BadRequest("request body must be an object")
        return value

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            status, payload, content_type = _get_route(parsed.path, parse_qs(parsed.query))
            self._send(status, payload, content_type)
        except (BadRequest, KeyError, IndexError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # technical boundary; no verdict is invented
            self._send(502, {"error": f"provider or lifecycle error: {type(exc).__name__}"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            body = self._body()
            status, payload = _post_route(self.path, body)
            self._send(status, payload)
        except BadRequest as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:
            self._send(502, {
                "error": "provider or fee preparation error",
                "error_type": type(exc).__name__,
                "detail": _safe_error_detail(exc),
            })


def wsgi_app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    """Stateless WSGI adapter for Vercel; shares the local route dispatchers."""
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/") or "/"
    query = parse_qs(environ.get("QUERY_STRING", ""))
    try:
        if method == "GET":
            status, payload, content_type = _get_route(path, query)
        elif method == "POST":
            length = int(environ.get("CONTENT_LENGTH") or 0)
            if length > MAX_BODY_BYTES:
                raise BadRequest("request body is too large")
            raw_body = environ.get("wsgi.input").read(length) if length else b"{}"
            try:
                body = json.loads(raw_body)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise BadRequest("request body must be JSON") from exc
            if not isinstance(body, dict):
                raise BadRequest("request body must be an object")
            status, payload = _post_route(path, body)
            content_type = "application/json"
        else:
            status, payload, content_type = 405, {"error": "method not allowed"}, "application/json"
    except (BadRequest, KeyError, IndexError) as exc:
        status, payload, content_type = 400, {"error": str(exc)}, "application/json"
    except Exception as exc:
        status, payload, content_type = 502, {
            "error": "provider or fee preparation error" if method == "POST" else "provider or lifecycle error",
            "error_type": type(exc).__name__,
            "detail": _safe_error_detail(exc),
        }, "application/json"

    if isinstance(payload, bytes):
        response_body = payload
    else:
        response_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    phrase = HTTPStatus(status).phrase
    headers = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(response_body))),
        ("Cache-Control", "no-store"),
    ]
    if status == 405:
        headers.append(("Allow", "GET, POST"))
    start_response(f"{status} {phrase}", headers)
    return [response_body]


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run Proofline's connected frontend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
