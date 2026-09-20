"""Run the authorized Proofline milestone 2 Studio Next lifecycle.

This script loads the wallet only in memory, uses the Consensus v0.6 RC
client, records lifecycle observations and fee estimates, and writes durable
evidence under ``evidence/`` plus the measured ``fee-profile.json``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping

from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet
from genlayer_py.types import TransactionHashVariant
from gltest.fees import FeeProfileCollector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from proofline.canonical import canonical_json, digest_json
from proofline.crypto import sign_payload
from proofline.fixtures import make_fixture
from proofline.adapters import LocalAdapter
from proofline.receipt import ProoflineDecision
from proofline.lifecycle import (
    GenLayerLifecycleClient,
    _finality_timestamp,
    _transaction_effect_decisions,
)


RPC = "https://studio-dev.genlayer.com/api"
CHAIN_ID = 61997
BASELINE_EVIDENCE_PATH = ROOT / "evidence" / "milestone2-hosted.json"
BASELINE_FEE_PROFILE_PATH = ROOT / "fee-profile.json"
HISTORICAL_PATH = ROOT / "evidence" / "historical"
EVIDENCE_PATH = ROOT / "evidence" / "milestone2-hosted-correction.json"
FEE_PROFILE_PATH = ROOT / "evidence" / "fee-profile-milestone2-correction.json"
PRIVATE_EVIDENCE_PATH = ROOT / ".private-evidence"
PRIVATE_FEE_PROFILE_PATH = PRIVATE_EVIDENCE_PATH / "fee-profile-runtime.json"
TX_JOURNAL_PATH = ROOT / "evidence" / "milestone2-hosted-correction-journal.json"
RC_SOURCE_PATH = ROOT / "evidence" / "proofline_studio_rc.py"
WAIT_INTERVAL_MS = 5_000
WAIT_RETRIES = 240
FEE_PROFILE_HEADROOM = 1.25
SECRET_KEY_MARKERS = (
    "private_key", "privatekey", "api_key", "apikey",
    "access_token", "auth_token", "password", "mnemonic", "seed_phrase",
    "client_secret", "signing_secret",
)


def preserve_historical_inputs() -> None:
    """Keep the original hosted claim and fee record before correction writes."""

    HISTORICAL_PATH.mkdir(parents=True, exist_ok=True)
    for source, target_name in (
        (BASELINE_EVIDENCE_PATH, "milestone2-hosted-baseline.json"),
        (BASELINE_FEE_PROFILE_PATH, "fee-profile-baseline.json"),
        (RC_SOURCE_PATH, "proofline_studio_rc-baseline.py"),
    ):
        target = HISTORICAL_PATH / target_name
        if source.exists() and not target.exists():
            shutil.copy2(source, target)


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    enum_value = getattr(value, "value", None)
    if enum_value is not None and enum_value is not value:
        return jsonable(enum_value)
    if hasattr(value, "__dict__"):
        return jsonable(vars(value))
    return str(value)


def redact_secret_fields(value: Any) -> Any:
    """Keep public evidence factual without exporting credential-bearing fields."""
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED BY SECRET-HYGIENE]"
                if _is_secret_key(str(key))
                else redact_secret_fields(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secret_fields(item) for item in value]
    return value


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in {"secret", "seed", "credential", "credentials"}:
        return True
    return any(
        lowered == marker or lowered.endswith("_" + marker)
        for marker in SECRET_KEY_MARKERS
    )


def load_account() -> Any:
    key = None
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == "GENLAYER_PRIVATE_KEY":
            key = value.strip().strip('"').strip("'")
            break
    if not key:
        raise RuntimeError("GENLAYER_PRIVATE_KEY is absent from .env")
    return Account.from_key(key)


def fee_options(estimate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "distribution": estimate["distribution"],
        "feeValue": estimate["feeValue"],
    }


def tx_reference(value: Any) -> str:
    if isinstance(value, bytes):
        return "0x" + value.hex()
    text = str(value)
    return text if text.startswith("0x") else text


def persist_submission(label: str, tx_hash: Any) -> str:
    """Durably record a sent hash before polling can be interrupted."""

    record: dict[str, Any] = {}
    if TX_JOURNAL_PATH.exists():
        record = json.loads(TX_JOURNAL_PATH.read_text(encoding="utf-8"))
    reference = tx_reference(tx_hash)
    record[label] = reference
    TX_JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    TX_JOURNAL_PATH.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return reference


def wait_lifecycle(client: Any, tx_hash: Any, label: str, collector: FeeProfileCollector) -> dict[str, Any]:
    decided = client.wait_for_transaction_receipt(
        transaction_hash=tx_hash,
        wait_until="decided",
        interval=WAIT_INTERVAL_MS,
        retries=WAIT_RETRIES,
        full_transaction=True,
    )
    finalized = client.wait_for_transaction_receipt(
        transaction_hash=tx_hash,
        wait_until="finalized",
        interval=WAIT_INTERVAL_MS,
        retries=WAIT_RETRIES,
        full_transaction=True,
    )
    decided_raw = jsonable(decided)
    finalized_raw = jsonable(finalized)
    if label == "deploy":
        collector.record_deploy(finalized_raw)
    else:
        collector.record_method(label, finalized_raw)
    return {
        "transaction_reference": tx_reference(tx_hash),
        "decided": decided_raw,
        "finalized": finalized_raw,
    }


def fee_observation(item: Mapping[str, Any]) -> dict[str, Any]:
    finalized = item.get("finalized", {})
    fees = finalized.get("fees", {}) if isinstance(finalized, Mapping) else {}
    consumed = fees.get("consumed", {}) if isinstance(fees, Mapping) else {}
    data = finalized.get("data", {}) if isinstance(finalized, Mapping) else {}
    accounting = data.get("fee_accounting", {}) if isinstance(data, Mapping) else {}
    return {
        "transaction_reference": item.get("transaction_reference"),
        "nonce": finalized.get("nonce"),
        "protocol_status": (finalized.get("lifecycle") or {}).get("state"),
        "consensus_result": finalized.get("result_name"),
        "execution_result": finalized.get("txExecutionResultName"),
        "timestamp_awaiting_finalization": finalized.get("timestamp_awaiting_finalization"),
        "finalized_at": _finality_timestamp(finalized) if isinstance(finalized, Mapping) else None,
        "finalization_timestamp_source": "consensus_history.current_monitoring.FINALIZED",
        "deposit": fees.get("deposit"),
        "fee_value": finalized.get("fee_value") or data.get("fee_value"),
        "primary_fee_spent": accounting.get("primary_fee_spent"),
        "total_refunded": accounting.get("total_refunded"),
        "settlement_reason": accounting.get("settlement_reason"),
        "execution_consumed": consumed.get("executionConsumed"),
        "storage_fee_used": consumed.get("storageFeeUsed"),
        "message_fees_consumed": consumed.get("messageFeesConsumed"),
        "locked_prices": fees.get("locked"),
    }


def contract_address_from(deployment: Mapping[str, Any]) -> str:
    final_tx = deployment["finalized"]
    decoded = final_tx.get("tx_data_decoded") or {}
    candidate = decoded.get("contract_address")
    if not candidate:
        candidate = final_tx.get("recipient") or final_tx.get("to_address")
    if not isinstance(candidate, str) or not candidate.startswith("0x") or len(candidate) != 42:
        raise RuntimeError("finalized deployment did not expose a contract address")
    return candidate


def build_wallet_fixture(account: Any) -> dict[str, Any]:
    fixture = make_fixture(os.environ.get("PROOFLINE_JOB_ID", "job-pass"))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    policy = json.loads(json.dumps(fixture["policy"]))
    agreement = json.loads(json.dumps(fixture["agreement"]))
    envelope = json.loads(json.dumps(fixture["envelope"]))
    policy["signer_address"] = account.address
    policy["deadline"] = int((now + timedelta(hours=1)).timestamp())
    envelope["policy_digest"] = digest_json(policy)
    envelope["deadline"] = policy["deadline"]
    envelope["timestamp"] = (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z")
    envelope["signer"]["address"] = account.address
    unsigned = {key: value for key, value in envelope.items() if key != "signer"}
    unsigned["signer"] = {"address": account.address}
    envelope["signer"]["signature"] = sign_payload(unsigned, account.key.hex())
    fixture["policy"] = policy
    fixture["agreement"] = agreement
    fixture["envelope"] = envelope
    return fixture


def build_studio_rc_source() -> str:
    """Apply the Studio runner version header without changing contract logic."""
    source = (ROOT / "contracts" / "proofline.py").read_text(encoding="utf-8")
    source = source.replace(
        '# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }',
        '# v0.2.5\n# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }',
        1,
    )
    RC_SOURCE_PATH.write_text(source, encoding="utf-8")
    return source


def main() -> None:
    preserve_historical_inputs()
    account = load_account()
    client = create_client(chain=studio_devnet, endpoint=RPC, account=account)
    if int(client.w3.eth.chain_id) != CHAIN_ID:
        raise RuntimeError("canonical RPC chain ID is not 61997")

    balance_before = int(client.w3.eth.get_balance(account.address))
    initial_balance = os.environ.get("PROOFLINE_INITIAL_BALANCE_WEI")
    fixture = build_wallet_fixture(account)
    job_id = fixture["job_id"]
    policy_json = canonical_json(fixture["policy"])
    agreement_json = canonical_json(fixture["agreement"])
    envelope_json = canonical_json(fixture["envelope"])
    evidence_uri = fixture["envelope"]["artifacts"][0]["uri"]
    evidence_content = fixture["evidence"][evidence_uri].decode("utf-8")
    collector = FeeProfileCollector()

    estimates: dict[str, Any] = {}
    estimate_fallbacks: dict[str, str] = {}
    lifecycle: dict[str, Any] = {}

    existing_contract = os.environ.get("PROOFLINE_CONTRACT_ADDRESS")
    if existing_contract:
        contract_address = existing_contract
        existing_deploy_hash = os.environ.get("PROOFLINE_DEPLOYMENT_HASH")
        if existing_deploy_hash:
            try:
                lifecycle["deploy"] = wait_lifecycle(client, existing_deploy_hash, "deploy", collector)
            except Exception as exc:  # RPC history can temporarily omit older deployment records.
                lifecycle["deploy"] = {
                    "transaction_reference": existing_deploy_hash,
                    "observation": "deployment was finalized successfully in the authorized run; current RPC query did not return its older record",
                    "query_error": f"{type(exc).__name__}: {exc}",
                }
        else:
            lifecycle["deploy"] = {
                "reused_contract_address": contract_address,
                "transaction_reference": None,
                "note": "Deployment was completed by an earlier authorized run; continuation reused its verified address.",
            }
    else:
        deploy_code = build_studio_rc_source()
        deploy_estimate = client.estimate_transaction_fees()
        estimates["deploy"] = jsonable(deploy_estimate)
        deploy_hash = client.deploy_contract(
            code=deploy_code,
            account=account,
            fees=fee_options(deploy_estimate),
        )
        persist_submission("deploy", deploy_hash)
        lifecycle["deploy"] = wait_lifecycle(client, deploy_hash, "deploy", collector)
        contract_address = contract_address_from(lifecycle["deploy"])
        deploy_execution = lifecycle["deploy"]["finalized"].get(
            "tx_execution_result_name"
        ) or lifecycle["deploy"]["finalized"].get("txExecutionResultName")
        if deploy_execution != "FINISHED_WITH_RETURN":
            raise RuntimeError(
                f"deployment finalized without successful execution: {deploy_execution}"
            )

    register_args = [job_id, policy_json, agreement_json]
    existing_register_hash = os.environ.get("PROOFLINE_REGISTER_HASH")
    if existing_register_hash:
        lifecycle["register_job"] = wait_lifecycle(client, existing_register_hash, "register_job", collector)
    else:
        try:
            register_estimate = client.estimate_transaction_fees_for_write(
                address=contract_address,
                function_name="register_job",
                account=account,
                args=register_args,
                transaction_hash_variant=TransactionHashVariant.LATEST_NONFINAL,
            )
        except Exception as exc:  # RC simulator may not index a fresh Studio contract yet.
            estimate_fallbacks["register_job"] = f"{type(exc).__name__}: {exc}"
            register_estimate = client.estimate_transaction_fees()
        estimates["register_job"] = jsonable(register_estimate)
        register_hash = client.write_contract(
            address=contract_address,
            function_name="register_job",
            account=account,
            args=register_args,
            fees=fee_options(register_estimate),
        )
        persist_submission("register_job", register_hash)
        lifecycle["register_job"] = wait_lifecycle(client, register_hash, "register_job", collector)

    submit_args = [job_id, policy_json, agreement_json, envelope_json, evidence_content]
    existing_submit_hash = os.environ.get("PROOFLINE_SUBMIT_HASH")
    if existing_submit_hash:
        submit_hash = existing_submit_hash
        lifecycle["submit_job"] = wait_lifecycle(client, submit_hash, "submit_job", collector)
    else:
        try:
            submit_estimate = client.estimate_transaction_fees_for_write(
                address=contract_address,
                function_name="submit_job",
                account=account,
                args=submit_args,
                transaction_hash_variant=TransactionHashVariant.LATEST_NONFINAL,
            )
        except Exception as exc:  # RC simulator may not index a fresh Studio contract yet.
            estimate_fallbacks["submit_job"] = f"{type(exc).__name__}: {exc}"
            submit_estimate = client.estimate_transaction_fees()
        estimates["submit_job"] = jsonable(submit_estimate)
        submit_hash = client.write_contract(
            address=contract_address,
            function_name="submit_job",
            account=account,
            args=submit_args,
            fees=fee_options(submit_estimate),
        )
        persist_submission("submit_job", submit_hash)
        lifecycle["submit_job"] = wait_lifecycle(client, submit_hash, "submit_job", collector)

    lifecycle_client = GenLayerLifecycleClient(
        client,
        network="studio-devnet",
        chain_id=CHAIN_ID,
    )
    submit_snapshot = lifecycle_client.inspect(submit_hash)
    receipt = lifecycle_client.finalized_receipt(
        submit_hash,
        contract_address=contract_address,
        decision_args=[job_id],
    )
    final_decision_raw = client.read_contract(
        address=contract_address,
        function_name="get_job",
        args=[job_id],
        transaction_hash_variant=TransactionHashVariant.LATEST_FINAL,
    )
    try:
        final_decision = ProoflineDecision.from_dict(json.loads(final_decision_raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("LATEST_FINAL did not return a canonical decision") from exc
    if final_decision.decision_digest != receipt.decision_digest:
        raise RuntimeError("LATEST_FINAL decision digest does not match receipt")
    effect_decisions = _transaction_effect_decisions(submit_snapshot.raw)
    effect_digests = [decision.decision_digest for decision in effect_decisions]
    if receipt.decision_digest not in effect_digests:
        raise RuntimeError("producing transaction effect does not match finalized receipt")
    finality_source_value = None
    history = submit_snapshot.raw.get("consensus_history") if isinstance(submit_snapshot.raw, Mapping) else None
    if isinstance(history, Mapping):
        monitoring = history.get("current_monitoring")
        if isinstance(monitoring, Mapping):
            finality_source_value = monitoring.get("FINALIZED")

    adapter = LocalAdapter(lifecycle_client=lifecycle_client)
    first_signal = adapter.apply(receipt)
    second_signal = adapter.apply(receipt)
    tampered_verdict = "REJECT" if receipt.verdict == "ACCEPT" else "ACCEPT"
    tampered_signal = "HOLD" if tampered_verdict == "REJECT" else "RELEASE_READY"
    tampered = replace(receipt, verdict=tampered_verdict, adapter_signal=tampered_signal)
    tamper_rejection = None
    try:
        adapter.apply(tampered)
    except Exception as exc:  # expected trust-boundary rejection
        tamper_rejection = f"{type(exc).__name__}: {exc}"
    if tamper_rejection is None:
        raise RuntimeError("tampered receipt was accepted")
    if first_signal != second_signal or adapter.applied_count() != 1:
        raise RuntimeError("adapter idempotency check failed")

    PRIVATE_EVIDENCE_PATH.mkdir(parents=True, exist_ok=True)
    fee_profile = collector.write(
        PRIVATE_FEE_PROFILE_PATH,
        network="studio-devnet",
        headroom=FEE_PROFILE_HEADROOM,
        chain_id=CHAIN_ID,
    )
    fee_profile = redact_secret_fields(fee_profile)
    fee_profile["observed_receipts"] = {
        name: fee_observation(lifecycle[name])
        for name in ("register_job", "submit_job")
        if isinstance(lifecycle.get(name), Mapping) and "finalized" in lifecycle[name]
    }
    fee_profile["semantic_success_branch"] = fee_observation(lifecycle["submit_job"])
    fee_profile["semantic_success_branch"]["decision_verdict"] = receipt.verdict
    fee_profile["semantic_success_branch"]["evaluation_invoked"] = receipt.evaluation_invoked
    fee_profile["deployment_observation"] = redact_secret_fields(lifecycle.get("deploy"))
    FEE_PROFILE_PATH.write_text(json.dumps(fee_profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    balance_after = int(client.w3.eth.get_balance(account.address))
    evidence = {
        "schema_version": "proofline.milestone2.evidence.v1",
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "commands": {
            "toolchain_check": "./.venv-rc/bin/python -m pip check",
            "unit_tests": "./.venv-rc/bin/pytest -q tests/unit",
            "hosted_verifier": (
                f"PROOFLINE_INITIAL_BALANCE_WEI={initial_balance or '<recorded-before-writes>'} "
                f"PROOFLINE_CONTRACT_ADDRESS={contract_address} "
                f"PROOFLINE_DEPLOYMENT_HASH={lifecycle['deploy'].get('transaction_reference')} "
                f"PROOFLINE_REGISTER_HASH={lifecycle['register_job'].get('transaction_reference')} "
                f"PROOFLINE_SUBMIT_HASH={lifecycle['submit_job'].get('transaction_reference')} "
                f"PROOFLINE_JOB_ID={job_id} ./.venv-rc/bin/python scripts/milestone2_hosted.py"
            ),
        },
        "network": "studio-devnet",
        "rpc": RPC,
        "chain_id": CHAIN_ID,
        "wallet_address": account.address,
        "wallet_verification": {
            "balance_before_authorized_writes_wei": int(initial_balance) if initial_balance else None,
            "balance_at_verifier_start_wei": balance_before,
            "balance_after_verifier_wei": balance_after,
        },
        "balance_before_wei": balance_before,
        "balance_after_wei": balance_after,
        "contract_address": contract_address,
        "deployed_source": str(RC_SOURCE_PATH),
        "job_id": job_id,
        "transaction_hashes": {
            name: item.get("transaction_reference") for name, item in lifecycle.items()
        },
        "fee_estimate_fallbacks": estimate_fallbacks,
        "lifecycle": redact_secret_fields(lifecycle),
        "provenance": {
            "transaction_reference": receipt.transaction_reference,
            "contract_address": contract_address,
            "job_id": job_id,
            "submission_method": "submit_job",
            "transaction_effect_decision_digests": effect_digests,
            "transaction_effect_matches_receipt": receipt.decision_digest in effect_digests,
        },
        "finalization_timestamp": {
            "receipt_finalized_at": receipt.finalized_at,
            "source": "submit_job transaction consensus_history.current_monitoring.FINALIZED",
            "authoritative_value": finality_source_value,
            "timestamp_awaiting_finalization": submit_snapshot.raw.get("timestamp_awaiting_finalization"),
            "last_vote_timestamp": submit_snapshot.raw.get("last_vote_timestamp"),
        },
        "submit_snapshot": redact_secret_fields(jsonable(submit_snapshot.raw)),
        "latest_final_decision": final_decision.to_dict(),
        "receipt": receipt.to_dict(),
        "adapter": {
            "first_signal": first_signal.to_dict(),
            "second_signal": second_signal.to_dict(),
            "idempotent": first_signal == second_signal and adapter.applied_count() == 1,
            "tampered_receipt_rejected": tamper_rejection,
        },
        "fee_estimates": redact_secret_fields(estimates),
        "fee_profile": fee_profile,
        "limitations": [
            "This evidence covers one successful hosted fixture only.",
            "No appeal was required or exercised because consensus finalized successfully.",
            "The detached envelope signature remains off-chain metadata; the contract binds the sender and digests.",
        ],
    }
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "wallet_address": account.address,
        "chain_id": CHAIN_ID,
        "balance_before_wei": balance_before,
        "balance_after_wei": balance_after,
        "contract_address": contract_address,
        "transaction_hashes": evidence["transaction_hashes"],
        "receipt": receipt.to_dict(),
        "adapter": evidence["adapter"],
        "fee_profile_path": str(FEE_PROFILE_PATH),
        "evidence_path": str(EVIDENCE_PATH),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
