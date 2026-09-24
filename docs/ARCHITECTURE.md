# Architecture

Proofline is a narrow semantic acceptance adapter. Its authoritative flow is:

1. The contract owner registers the exact versioned agreement and acceptance
   policy for a job.
2. The authorized evidence submitter sends the exact evidence bytes. The
   contract authenticates the transaction sender with
   `gl.message.sender_address`, rechecks registered digests and deterministic
   constraints. Validation is state-free: replay state is committed only when a
   deterministic rejection or valid semantic decision is stored.
3. Only a structurally valid submission reaches one bounded GenLayer semantic
   evaluation. The contract stores a `proofline.decision.v1` record.
4. A read-only lifecycle client observes protocol status, execution result,
   and final-state contract readback. Only then does it construct a
   `proofline.receipt.v1` finalized receipt. Before applying a hosted signal,
   the adapter re-observes and matches that lifecycle result.

## Trust boundaries

- The contract owns registration authority, sender binding, replay protection,
  deterministic checks, semantic invocation, and the decision record.
- Off-chain intake owns EIP-191 detached-signature preflight, retrieval policy,
  and convenience validation. Detached signatures are not contract authority.
- Evidence is untrusted bytes and is passed to the evaluator as data, never
  instructions. The MVP accepts exactly one complete `response` artifact with
  `application/json` media type. Its URI must match the owner-registered
  `embedded://evidence/...` allowlist and its bytes are digest-bound.
- Evaluator invocation failure and malformed or contradictory evaluator output
  are technical failures. They store no decision or nonce and may be retried;
  `UNDETERMINED` is available only in valid structured evaluator output.
- The lifecycle client owns transaction polling and finality/readback checks;
  it binds the receipt to the producing `submit_job` transaction, contract,
  job, returned decision effect, and matching `LATEST_FINAL` digest. Its
  finalization timestamp comes from the authoritative Studio lifecycle record.
- The adapter emits readiness signals only. It never claims payment, escrow,
  bridge, or token movement.

## Local versus hosted

The direct runner executes the leader path and mocks evaluator output. It proves
state transitions, input boundaries, structured-output handling, and exercises
the captured v0.6 RC validator callback using `gl.vm.Return.calldata`. The
comparator validates both payloads and requires matching verdict, reason code,
policy digest, and evidence digest; rationale may differ. Direct tests cover
valid conflicting verdict/reason results, matching decisions, malformed
payloads, and technical failure. This does not prove validator committee
consensus, appeals, or hosted finality. The corrected comparator source is
deployed at `0x30829d13D0d86a9ae83Dc5e832Fb4AADd43Df26b`; hosted semantic
success on that deployment is separately evidenced, but no valid committee
disagreement was observed. A fresh Production browser flow on that address is
recorded at `evidence/milestone4-browser-verification-20260924.json`.
`DIRECT_MODE` and `LOCAL_ONLY` are local provenance labels. Hosted finalized
receipts require `FINALIZED`, SDK execution result `FINISHED_WITH_RETURN`, a
matching configured chain/contract identity, the successful decision effect of
the producing submission transaction, a matching `LATEST_FINAL` state read,
and `consensus_history.current_monitoring.FINALIZED` rather than acceptance or
transaction creation time.

Base, escrow, bridges, tokens, reputation, frontend, and application-level
appeals remain outside this core.
