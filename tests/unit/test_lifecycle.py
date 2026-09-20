from __future__ import annotations

import base64
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from proofline.adapters import LocalAdapter
from proofline.canonical import digest_json
from proofline.errors import ProtocolError, ReceiptError
from proofline.fixtures import make_fixture
from proofline.lifecycle import GenLayerLifecycleClient
from proofline.receipt import ProoflineDecision, ProoflineReceipt
from proofline.schemas import AcceptancePolicy


CONTRACT_ADDRESS = "0x" + "12" * 20


def _hosted_tx(job_id="job-pass", *, contract_address=CONTRACT_ADDRESS, method="submit_job", **extra):
    tx = {
        "status_name": "FINALIZED",
        "tx_execution_result_name": "FINISHED_WITH_RETURN",
        "recipient": contract_address,
        "tx_data_decoded": {"method": method, "args": [job_id]},
    }
    tx.update(extra)
    return tx


def _decision(fixture):
    policy = AcceptancePolicy.from_dict(fixture["policy"])
    evidence_digest = digest_json(
        {
            "artifacts": [
                {"artifact_id": item["artifact_id"], "sha256": item["sha256"]}
                for item in fixture["envelope"]["artifacts"]
            ]
        }
    )
    return ProoflineDecision(
        agreement_id=fixture["job_id"],
        agreement_digest=fixture["agreement"]["agreement_digest"],
        policy_version=policy.policy_version,
        policy_digest=policy.digest,
        evidence_digest=evidence_digest,
        verdict="ACCEPT",
        reason_code="CRITERION_SATISFIED",
        adjudication_mode="GENLAYER_HOSTED",
        evaluation_invoked=True,
        deterministic_result={"passed": True},
        contract_address=CONTRACT_ADDRESS,
        network="Studio dev",
        chain_id=61997,
        adapter_signal="RELEASE_READY",
    )


class FakeSDK:
    chain = SimpleNamespace(name="Studio dev", id=61997)

    def __init__(self, tx, decision=None, effect_decision=None):
        self.tx = tx
        self.decision = decision
        self.effect_decision = effect_decision
        self.read_kwargs = None

    def _with_effect(self, tx):
        tx = dict(tx)
        effect = self.effect_decision or self.decision
        if effect is not None and tx.get("tx_execution_result_name") == "FINISHED_WITH_RETURN":
            payload = json.dumps(
                effect.to_dict(include_digest=False),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            encoded = b"\x00" + _uleb((len(payload) << 3) | 4) + payload
            tx["consensus_data"] = {
                "leader_receipt": [{"result": {"status": "return", "raw": base64.b64encode(encoded).decode("ascii")}}]
            }
        return tx

    def _observed_tx(self):
        return self._with_effect(self.tx)

    def get_transaction(self, *, transaction_hash):
        return self._observed_tx()

    def wait_for_transaction_receipt(self, **kwargs):
        return self._observed_tx()

    def read_contract(self, **kwargs):
        self.read_kwargs = kwargs
        return self.decision.to_dict()


def _uleb(value):
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def test_inspect_exposes_protocol_consensus_appeal_and_network_state():
    tx = {
        "status_name": "APPEAL_COMMITTING",
        "tx_execution_result_name": "NOT_VOTED",
        "result_name": "DISAGREE",
        "appeal_status": "APPEAL_COMMITTING",
    }
    client = GenLayerLifecycleClient(FakeSDK(tx))
    state = client.inspect("0xabc")
    assert state.transaction_reference == "0xabc"
    assert state.protocol_status == "APPEAL_COMMITTING"
    assert state.execution_result == "NOT_VOTED"
    assert state.consensus_result == "DISAGREE"
    assert state.appeal_status == "APPEAL_COMMITTING"
    assert state.finality_observed is False
    assert state.chain_id == 61997


def test_finalized_receipt_requires_protocol_and_execution_success():
    fixture = make_fixture("job-pass")
    decision = _decision(fixture)
    tx = _hosted_tx(fixture["job_id"], current_timestamp="2026-09-13T12:00:00Z")
    sdk = FakeSDK(tx, decision)
    receipt = GenLayerLifecycleClient(sdk).finalized_receipt(
        "0x" + "ab" * 32,
        contract_address=CONTRACT_ADDRESS,
        decision_args=[fixture["job_id"]],
    )
    assert receipt.schema_version == "proofline.receipt.v1"
    assert receipt.finality_status == "FINALIZED"
    assert receipt.protocol_status == "FINALIZED"
    assert receipt.execution_result == "FINISHED_WITH_RETURN"
    assert receipt.decision_digest == decision.decision_digest
    assert sdk.read_kwargs["transaction_hash_variant"].value == "latest-final"
    assert LocalAdapter(lifecycle_client=GenLayerLifecycleClient(sdk)).apply(receipt).signal == "RELEASE_READY"


def test_caller_supplied_finality_cannot_reach_adapter():
    decision = _decision(make_fixture("job-pass"))
    with pytest.raises(ReceiptError, match="lifecycle verification"):
        ProoflineReceipt.from_decision(
            decision,
            transaction_reference="0x" + "ab" * 32,
            protocol_status="FINALIZED",
            finality_status="FINALIZED",
            execution_result="FINISHED_WITH_RETURN",
            finalized_at=1,
        )

    tx = _hosted_tx(decision.agreement_id, current_timestamp="2026-09-13T12:00:00Z")
    client = GenLayerLifecycleClient(FakeSDK(tx, decision))
    verified = client.finalized_receipt(
        "0x" + "ab" * 32,
        contract_address=CONTRACT_ADDRESS,
        decision_args=[decision.agreement_id],
    )
    with pytest.raises(ReceiptError, match="lifecycle verification"):
        LocalAdapter().apply(verified)

    forged_data = verified.to_dict(include_digest=False)
    forged_data["finalized_at"] = 1
    forged = ProoflineReceipt.from_dict(forged_data)
    adapter = LocalAdapter(lifecycle_client=client)
    with pytest.raises(ReceiptError, match="readback"):
        adapter.apply(forged)
    assert adapter.applied_count() == 0


def test_finalized_receipt_rejects_failed_execution_without_readback():
    fixture = make_fixture("job-pass")
    tx = {
        "status_name": "FINALIZED",
        "tx_execution_result_name": "FINISHED_WITH_ERROR",
    }
    sdk = FakeSDK(tx, None)
    with pytest.raises(ProtocolError, match="execution"):
        GenLayerLifecycleClient(sdk).finalized_receipt(
            "0x" + "ab" * 32,
            contract_address=CONTRACT_ADDRESS,
            decision_args=[fixture["job_id"]],
        )


def test_finalized_receipt_rejects_malformed_contract_readback():
    fixture = make_fixture("job-pass")
    tx = _hosted_tx(fixture["job_id"], current_timestamp="2026-09-13T12:00:00Z")

    class BadReadback(FakeSDK):
        def read_contract(self, **kwargs):
            return {"schema_version": "proofline.receipt.v1"}

    with pytest.raises(ProtocolError, match="readback"):
        GenLayerLifecycleClient(BadReadback(tx, _decision(fixture))).finalized_receipt(
            "0x" + "ab" * 32,
            contract_address=CONTRACT_ADDRESS,
            decision_args=[fixture["job_id"]],
        )


def test_receipt_model_does_not_accept_unverified_hosted_provenance():
    decision = _decision(make_fixture("job-pass"))
    with pytest.raises(ReceiptError, match="lifecycle verification"):
        ProoflineReceipt.from_decision(
            decision,
            transaction_reference="0x" + "ab" * 32,
            protocol_status="FINALIZED",
            finality_status="FINALIZED",
            finalized_at=1,
        )


def test_finalized_receipt_rejects_chain_mismatch():
    fixture = make_fixture("job-pass")
    decision = replace(_decision(fixture), chain_id=61999)
    tx = _hosted_tx(fixture["job_id"], current_timestamp="2026-09-13T12:00:00Z")
    with pytest.raises(ProtocolError, match="chain_id"):
        GenLayerLifecycleClient(FakeSDK(tx, decision)).finalized_receipt(
            "0x" + "ab" * 32,
            contract_address=CONTRACT_ADDRESS,
            decision_args=[fixture["job_id"]],
        )


def test_finalized_receipt_preserves_missing_finalization_timestamp():
    fixture = make_fixture("job-pass")
    decision = _decision(fixture)
    tx = _hosted_tx(fixture["job_id"], created_timestamp="2026-09-13T11:00:00Z")
    receipt = GenLayerLifecycleClient(FakeSDK(tx, decision)).finalized_receipt(
        "0x" + "ab" * 32,
        contract_address=CONTRACT_ADDRESS,
        decision_args=[fixture["job_id"]],
    )
    assert receipt.finalized_at is None


def test_finalized_receipt_uses_authoritative_finalized_monitoring_timestamp():
    fixture = make_fixture("job-pass")
    decision = _decision(fixture)
    tx = _hosted_tx(
        fixture["job_id"],
        timestamp_awaiting_finalization=100,
        last_vote_timestamp=100,
        consensus_history={"current_monitoring": {"FINALIZED": 123.75}},
    )
    receipt = GenLayerLifecycleClient(FakeSDK(tx, decision)).finalized_receipt(
        "0x" + "ab" * 32,
        contract_address=CONTRACT_ADDRESS,
        decision_args=[fixture["job_id"]],
    )
    assert receipt.finalized_at == 123


def test_finalized_receipt_keeps_missing_finality_timestamp_idempotent():
    fixture = make_fixture("job-pass")
    decision = _decision(fixture)

    class AdvancingSDK(FakeSDK):
        def __init__(self):
            super().__init__({}, decision)
            self.observation = 0

        def wait_for_transaction_receipt(self, **kwargs):
            self.observation += 1
            return self._with_effect(
                _hosted_tx(
                    fixture["job_id"],
                    current_timestamp=f"2026-09-13T12:0{self.observation}:00Z",
                )
            )

    sdk = AdvancingSDK()
    client = GenLayerLifecycleClient(sdk)
    first = client.finalized_receipt(
        "0x" + "ab" * 32,
        contract_address=CONTRACT_ADDRESS,
        decision_args=[fixture["job_id"]],
    )
    second = client.finalized_receipt(
        "0x" + "ab" * 32,
        contract_address=CONTRACT_ADDRESS,
        decision_args=[fixture["job_id"]],
    )
    assert first.finalized_at is None
    assert second.finalized_at is None
    assert first.receipt_digest == second.receipt_digest


@pytest.mark.parametrize(
    ("method", "contract_address", "job_id", "message"),
    [
        ("register_job", CONTRACT_ADDRESS, "job-pass", "did not submit a job"),
        ("submit_job", CONTRACT_ADDRESS, "other-job", "job does not match"),
        ("submit_job", "0x" + "34" * 20, "job-pass", "recipient does not match"),
    ],
)
def test_finalized_receipt_rejects_unrelated_transaction_provenance(
    method, contract_address, job_id, message
):
    fixture = make_fixture("job-pass")
    decision = _decision(fixture)
    tx = _hosted_tx(job_id, contract_address=contract_address, method=method)
    with pytest.raises(ProtocolError, match=message):
        GenLayerLifecycleClient(FakeSDK(tx, decision)).finalized_receipt(
            "0x" + "ab" * 32,
            contract_address=CONTRACT_ADDRESS,
            decision_args=[fixture["job_id"]],
        )


def test_finalized_receipt_rejects_transaction_with_different_decision_effect():
    fixture = make_fixture("job-pass")
    decision = _decision(fixture)
    other = replace(decision, verdict="REJECT", reason_code="CRITERION_NOT_SATISFIED", adapter_signal="HOLD")
    tx = _hosted_tx(fixture["job_id"])
    with pytest.raises(ProtocolError, match="decision effect"):
        GenLayerLifecycleClient(FakeSDK(tx, decision, effect_decision=other)).finalized_receipt(
            "0x" + "ab" * 32,
            contract_address=CONTRACT_ADDRESS,
            decision_args=[fixture["job_id"]],
        )
