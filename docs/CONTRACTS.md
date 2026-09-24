# Contract and hosted verification

Source: [`contracts/proofline.py`](../contracts/proofline.py).

## Authority and writes

`register_job(job_id, policy_json, agreement_json)` is owner-only. The
constructor records `gl.message.sender_address` as owner. Registration binds
the job, exact policy/agreement, and authorized evidence submitter.

`submit_job(job_id, policy_json, agreement_json, envelope_json,
evidence_content)` requires the registered policy and agreement and an
authenticated transaction sender equal to the registered policy signer. The
contract returns a canonical `proofline.decision.v1`. A detached envelope
signature is intake metadata, not an onchain authorization mechanism.

Malformed/authentication failures revert without consuming replay or job state.
Schema-valid deterministic failures can store `REJECT`; evaluator failures or
malformed/contradictory evaluator output are technical failures and do not
store a decision. A valid, digest-bound semantic result may be `ACCEPT`,
`REJECT`, or `UNDETERMINED`.

## Validator comparison

The Consensus v0.6 RC `run_nondet_unsafe` validator parses the successful
leader `gl.vm.Return.calldata` shape as a complete semantic result and performs
an independent evaluation. It rejects disagreement in verdict, reason code,
policy digest, or evidence digest. Rationale is validated but excluded from
the decision comparison. Malformed leader results and technical failures fail
closed. Pinned-runner direct tests cover conflicting verdicts and reason codes,
agreement with differing rationale, malformed output, and evaluator failure.
Direct tests do not prove committee-level disagreement or appeal behavior; no
valid committee disagreement has been observed in Studio Next.

## Current deployment

- Network: Studio Next, chain `61997`; canonical RPC
  `https://studio-dev.genlayer.com/api`.
- Contract: `0x30829d13D0d86a9ae83Dc5e832Fb4AADd43Df26b`.
- Deployment transaction:
  `0x015e6839ddaec1cd6f6ae9c8ae4d060da293b377de7f2a8e655588bc42c6049d`.
- Deployed contract source SHA-256:
  `6a4e8f7738a281887616f6f7d1004158c2b0b5fc0db88c413f45ed71197fdbab`.
- Runner: `v0.6.0-rc5`; owner initialized from the deployment sender:
  `0x3211d1419709682b81c53CC51cb63622E25488d3`.

The current Production app is <https://proofline-nu.vercel.app>. In the latest
user-performed browser flow, job `browser-1790227078107` registered through
`0xc7b4c6552e0d434515950bdaa799f30f59569d72b2d38e0338ac8fde41b61c96` and
submitted through
`0x9b071b100e011cb13a2f0056e56257c933bfa172457e05271275cb51180d26db`. Both
transactions finalized with `FINISHED_WITH_RETURN / MAJORITY_AGREE`.
`LATEST_FINAL` returned `ACCEPT / CRITERION_MET`; recomputed decision digest:
`sha256:c1dea2b97956ac766c464e0cb05adbb990c17e79d240ebe6a5e04d183f55a6df`.
The producing transaction's decision effect matched that readback, receipt
verification passed, and LocalAdapter returned `RELEASE_READY`. The
authoritative finalization timestamp was
`consensus_history.current_monitoring.FINALIZED = 1790227352.8684402`; the
receipt records integer seconds `1790227352`. The user performed wallet
authorization; the verifier checked chain and Production API records but did
not witness the wallet prompt. See
[`evidence/milestone4-browser-verification-20260924.json`](../evidence/milestone4-browser-verification-20260924.json).

## Receipt and readback boundary

`get_job(job_id)` returns the decision through the `LATEST_FINAL` read path.
The lifecycle client constructs `proofline.receipt.v1` only after protocol
`FINALIZED`, execution `FINISHED_WITH_RETURN`, matching contract/job/chain,
successful `submit_job` calldata and decision effect, matching `LATEST_FINAL`
digest, and the authoritative finalization timestamp. Acceptance time, last
vote time, transaction creation, and moving observation timestamps are not
finalization substitutes. `LocalAdapter` re-observes this lifecycle and
accepts only a matching receipt.

## Historical records and scope

The published Milestone 2/3 records refer to the then-deployed contract
`0x6eb8E208666694e9948E87aa46294aA349fD2014`; they remain historical and are
not proof for the current address. See [`evidence/README.md`](../evidence/README.md).
`RELEASE_READY` is a readiness signal. This project does not claim payments,
settlement, custody, or funds movement.
