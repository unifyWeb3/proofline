# Domain Model

All core objects use compact, recursively key-sorted JSON and lowercase
`sha256:<64 hex>` digests.

## Versioned objects

- `proofline.policy.v1`: owner-registered policy with `agreement_id`, deadline,
  authorized `signer_address`, the exact MVP artifact set `['response']`, only
  the supported deterministic requirements, one subjective criterion, and a
  nonempty unique embedded-evidence allowlist.
- `proofline.agreement.v1`: owner-registered agreement with ID, policy version,
  parties, and a digest excluding its own digest field.
- `proofline.response.v1`: submission envelope with job/policy/agreement
  bindings, exactly one `response` artifact reference with complete metadata
  and `application/json` media type, timestamp, deadline, nonce,
  request/response hashes, and exact signer metadata. Its detached signature is
  off-chain preflight metadata, not contract authorization.
- `proofline.decision.v1`: canonical onchain adjudication record. It contains
  deterministic result, application verdict, digests, contract/network
  provenance, adjudication mode, and adapter signal. It contains no transaction
  hash, finality, appeal, or fabricated finalization timestamp.
- `proofline.receipt.v1`: finalized lifecycle object containing the exact
  `decision_digest`, transaction reference, `FINALIZED` protocol/finality state,
  `execution_result=FINISHED_WITH_RETURN`, matching chain and contract
  provenance, a true finalization observation timestamp, and the same decision
  fields. It is created only by the lifecycle client after `LATEST_FINAL`
  readback.
- `proofline.adapter-signal.v1`: idempotent readiness projection from a
  finalized receipt.

Receipt construction is split at the trust boundary: `from_decision` is local
only and rejects hosted-looking provenance. Hosted construction is reserved for
the lifecycle client, and adapter consumption requires a fresh lifecycle
readback match.

Application outcomes are exactly `ACCEPT`, `REJECT`, and `UNDETERMINED`.
`UNDETERMINED` means a successfully executed adjudication cannot safely resolve
the evidence and is valid only when returned in a complete, digest-bound
evaluator object. RPC, evaluator invocation, malformed/contradictory evaluator
output, timeout, SDK, runtime, and transaction failures remain technical errors
and do not consume replay state.

Evidence digest is the canonical sorted artifact manifest digest. Each artifact
byte sequence is separately checked against its declared SHA-256; request and
response objects are checked against their envelope hashes. All SHA-256 fields
use lowercase hexadecimal, and artifact URIs must match the registered
embedded-evidence allowlist path.
