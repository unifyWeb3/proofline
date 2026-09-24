# Security and Trust Boundaries

- The contract owner registers the agreement, policy digest, authorized
  evidence submitter, and job ID before submission.
- The frontend API rejects `register_job` preparation unless the supplied
  address matches the owner recorded by the current finalized deployment. This
  is an early fee-preparation guard, not proof that the caller controls that
  address; browser wallet signing and the onchain owner check remain required.
  Evidence submission authorization remains the registered policy signer's
  responsibility and is not tied to the registration-owner gate.
- Every submission is bound to `gl.message.sender_address`; a caller-supplied
  `signer_address` cannot self-authorize an unregistered or different job.
- The contract does not implement detached ECDSA recovery. The envelope
  signature is off-chain EIP-191 preflight metadata only and must not be reused
  as a contract-enforced security claim.
- Unknown fields, malformed JSON, invalid primitive types, invalid hashes,
  timestamps, deadlines, nonces, artifact references, and missing criterion or
  evidence are rejected before semantic evaluation.
- Authenticated, schema-valid deterministic failures store `REJECT` and consume
  replay protection. Malformed or unauthenticated attempts revert and consume
  neither nonce nor job ID. Validation itself is state-free; evaluator
  invocation failure or malformed/contradictory output also leaves replay and
  decision state untouched, permitting a valid retry.
- Evidence is untrusted, exact hash-bound data, never evaluator instructions.
  MVP evidence is exactly one complete `application/json` response artifact at
  a URI permitted by the registered `embedded://evidence/...` allowlist;
  mutable live URLs and private storage are not part of the hosted claim.
- `ACCEPT`, `REJECT`, and `UNDETERMINED` are application outcomes. RPC,
  evaluator, timeout, SDK, runtime, transaction, and validator failures remain
  technical failures and are never silently converted into an application
  verdict. Malformed/contradictory evaluator output is a protocol failure;
  `UNDETERMINED` is reserved for valid digest-bound semantic output expressing
  evidence ambiguity.
- Current source validates the RC callback's successful leader
  `gl.vm.Return.calldata`, independently evaluates a second valid result, and
  rejects disagreement in verdict, reason code, policy digest, or evidence
  digest. Rationale may differ because it does not drive or appear in the
  decision. Unknown/malformed callback shapes and technical failures fail
  closed. Pinned-runner direct regressions cover these cases.
- The corrected source is deployed at
  `0x30829d13D0d86a9ae83Dc5e832Fb4AADd43Df26b` on Studio Next chain `61997`.
  A fresh source-backed semantic run finalized `ACCEPT / CRITERION_MET` with
  `FINISHED_WITH_RETURN / MAJORITY_AGREE`, matching `LATEST_FINAL`, a verified
  producing-transaction-bound receipt, and LocalAdapter `RELEASE_READY`.
  The evidence tests the source-comparison explanation, not external customer
  work. No valid committee-level disagreement or appeal was observed. Earlier
  `0xCC48…aFE6`, `0xb003…393dF`, and `0x6eb8…2014` runs remain historical and
  their evidence is unchanged.
- A fresh user-performed browser flow against the current contract registered
  and submitted job `browser-1790227078107`. Both transactions finalized with
  `FINISHED_WITH_RETURN / MAJORITY_AGREE`; authoritative `LATEST_FINAL` matched
  `ACCEPT / CRITERION_MET`, receipt verification passed, and LocalAdapter
  returned `RELEASE_READY`. The agent independently checked the Production API
  and chain records but did not witness the wallet popup. The sanitized record
  is `evidence/milestone4-browser-verification-20260924.json`.
- A finalized receipt requires hosted protocol `FINALIZED`, execution result
  `FINISHED_WITH_RETURN`, a matching chain/contract `LATEST_FINAL` readback, a
  32-byte transaction hash, and a genuine finalization observation timestamp.
  `ProoflineReceipt.from_decision(...)` rejects hosted-looking provenance, and
  `LocalAdapter` requires a lifecycle client to re-observe and match that
  receipt before applying it. Direct/local labels are never hosted finality.
- A non-string contract `evidence_content` is malformed input and reverts
  before deterministic rejection storage, so it cannot consume nonce or job
  state. Empty text remains the explicit unavailable-evidence rejection case.
- Adapter signals are idempotent readiness projections and never settlement
  proofs. The frontend runtime receives no private signing key or credential;
  public transaction hashes in sanitized evidence are verification identifiers.
