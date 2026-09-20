# Contract Interface

Source: [contracts/proofline.py](../contracts/proofline.py).
The contract uses the pinned GenVM runner hash required by the official
boilerplate.

## Authority and writes

`register_job(job_id, policy_json, agreement_json) -> None` is owner-only. The
deployer (`gl.message.sender_address` in the constructor) is the registration
authority for the agreement, policy, authorized submitter, and job ID. A job is
registered once and cannot be occupied by an arbitrary caller.

`submit_job(job_id, policy_json, agreement_json, envelope_json,
evidence_content) -> str` requires the exact registered policy/agreement and an
authenticated transaction sender equal to the registered `signer_address` and
envelope signer address. It returns a canonical `proofline.decision.v1` JSON
record. The detached envelope signature is not verified in the contract and is
not part of the authorization claim.

Malformed/authentication failures raise the supported GenVM `UserError`; they
do not consume a nonce or reserve a job. This includes any non-string
`evidence_content`, which is rejected as `MALFORMED_PAYLOAD` before validation.
An empty string is a schema-valid unavailable-evidence case. A schema-valid deterministic failure
stores a `REJECT` decision without semantic evaluation and consumes the nonce.
Evaluator invocation failure and malformed or contradictory evaluator output
are technical failures; neither stores a decision nor consumes the nonce, so a
valid retry remains possible.

For this MVP, registration permits exactly `required_artifacts=["response"]`,
only `max_evidence_age_seconds` and `require_snapshot` deterministic
requirements, one exact subjective-criterion object, and a nonempty unique
`embedded://evidence` allowlist. Submission requires exactly one response
artifact with all six metadata fields and `media_type="application/json"`.
Hashes are lowercase SHA-256 digests, the signer object has exactly `address`
and `signature`, and the artifact URI must match the registered allowlist path.

The semantic evaluator must return exactly `verdict`, `reason_code`,
`evidence_digest`, `policy_digest`, and `rationale`. Only a valid digest-bound
object may yield `UNDETERMINED`; malformed output raises
`[LLM_ERROR] MALFORMED_EVALUATOR_OUTPUT`.

## Read methods

- `get_job(job_id) -> str`: raw canonical decision JSON or empty string.
- `nonce_used(nonce) -> bool`: replay state.
- `get_contract_schema_version() -> str`: `proofline.response.v1` input schema.
- `get_decision_schema_version() -> str`: `proofline.decision.v1` output schema.

## Tooling lane

The verified local/direct lane is `genlayer-py==0.16.3` and
`genlayer-test==0.29.2`. The v0.6 release-candidate lane is isolated in
`requirements-rc.txt` and is not mixed into these results. Direct mode proves
leader-path behavior only; hosted validator consensus and appeals remain
`HOSTED_ONLY / NOT PROVEN BY DIRECT MODE`.

The lifecycle client supports the RC SDK's `wait_until="finalized"` shape and
the stable test doubles, using `get_transaction`,
`wait_for_transaction_receipt`, `read_contract`, and
`ExecutionResult.FINISHED_WITH_RETURN`. It rejects a decision whose chain ID or
contract address differs from the configured readback target. Hosted receipts
bind to the producing `submit_job` transaction effect and the canonical
`LATEST_FINAL` decision digest. Finality comes from
`consensus_history.current_monitoring.FINALIZED`, never acceptance, last-vote,
creation, or a moving observation timestamp.

The corrected authorized Studio Next proof uses contract
`0x6eb8E208666694e9948E87aa46294aA349fD2014` and is recorded in
`evidence/milestone2-hosted-correction.json`; the semantic fee profile is
`evidence/fee-profile-milestone2-correction.json`. Older baseline artifacts are
historical development material and are intentionally excluded from this public
release.
