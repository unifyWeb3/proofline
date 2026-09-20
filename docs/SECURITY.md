# Security and Trust Boundaries

- The contract owner registers the agreement, policy digest, authorized
  evidence submitter, and job ID before submission.
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
  proofs. No private keys, wallet generation, hosted credentials, or real
  transaction values are stored here.
