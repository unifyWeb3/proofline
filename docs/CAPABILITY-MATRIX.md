# Capability Matrix

The matrix distinguishes what the local/direct implementation proves from what
requires a hosted GenLayer transaction. A direct runner executes one leader
path; it is not a validator committee.

| Capability | Boundary | Status |
|---|---|---|
| Policy/agreement registration authority | Contract owner | `CONTRACT-ENFORCED`: only the deployer owner can register a job and exact policy/agreement digests. |
| Agreement binding | Contract | `CONTRACT-ENFORCED`: submissions must match the registered agreement bytes and digest. |
| Acceptance-policy binding | Contract | `CONTRACT-ENFORCED`: submissions must match the registered policy bytes and digest. |
| Authorized evidence submitter | Contract | `CONTRACT-ENFORCED`: registered `signer_address` must equal the authenticated transaction sender. |
| Transaction-sender authentication | GenLayer runtime | `SUPPORTED`: uses `gl.message.sender_address`; direct tests set the VM sender. |
| Signer binding | Contract | `CONTRACT-ENFORCED`: envelope address must equal the registered authorized submitter and transaction sender. |
| Detached signature verification | Contract | `UNSUPPORTED/BLOCKED`: no verified ECDSA recovery primitive is used in this contract. The signature field is intake metadata only. |
| Detached signature verification | Off-chain intake | `SUPPORTED`: `eth-account` EIP-191 verification is preflight and cannot authorize an onchain submission by itself. |
| Job-ID squatting | Contract | `CONTRACT-ENFORCED`: unregistered callers cannot submit; owner registration is one-time. |
| Nonce/replay protection | Contract | `CONTRACT-ENFORCED`: authenticated policy-valid deterministic rejections and stored semantic decisions consume a nonce. Malformed, authentication, binding, replay, and evaluator/protocol failures do not. |
| Schema and unknown-field validation | Off-chain + contract | `CONTRACT-ENFORCED` for acceptance-relevant fields; exact versioned parsing rejects unknown fields. |
| Deadline/timestamp/freshness | Off-chain + contract | `CONTRACT-ENFORCED` for deadline, timezone, future, and configured age checks. |
| Required artifacts and media/content shape | Off-chain + contract | `CONTRACT-ENFORCED`: the MVP accepts exactly one complete `response` artifact, requires `application/json`, enforces the registered nonempty embedded-path allowlist, hashes/snapshot metadata, and request/response object bindings. Unknown deterministic requirements and unsupported artifact sets are rejected at registration. |
| Evidence retrieval | Intake/client | `OFFCHAIN`: the contract receives exact bytes; it does not fetch mutable web content. |
| Hosted-compatible evidence | Fixture/client | `SUPPORTED`: deterministic `embedded://evidence/...` bytes are hash-bound; uncontrolled live URLs are out of scope. |
| Leader/direct execution | Direct runner | `DIRECT-PROVEN`: bounded evaluator call occurs only after deterministic success. |
| Structured evaluator-output validation | Contract + unit/direct tests | `DIRECT-PROVEN`: exact fields, verdicts, digest bindings, and rationale limits are checked. Malformed or contradictory output fails technically and leaves decision/replay state untouched; only valid evaluator output may return semantic `UNDETERMINED`. |
| Captured equivalence validator behavior | Direct runner | `DIRECT-TESTED`: RC callback `Return.calldata` is parsed and compared against an independent evaluation; regressions cover verdict/reason disagreement rejection, matching decision fields with rationale differences, malformed payload rejection, and technical failure. This is not committee execution. |
| Real validator consensus | GenLayer protocol | `HOSTED_ONLY / NOT PROVEN BY DIRECT MODE`: direct mode executes a leader function and can unit-test a captured comparator, but it does not exercise a committee. |
| Validator disagreement | GenLayer protocol | Corrected comparator source is deployed at `0x30829d13D0d86a9ae83Dc5e832Fb4AADd43Df26b`; pinned-runner direct regressions reject tested decision-field conflicts. No committee-level valid decision disagreement was observed, so hosted disagreement behavior remains unproven. |
| Appeal behavior | GenLayer protocol | `HOSTED_ONLY / NOT PROVEN`: lifecycle client exposes observed appeal status but no write is made here. |
| Decision record | Contract + parser | `SUPPORTED`: canonical `proofline.decision.v1`; raw contract JSON parses directly. |
| Finalized receipt | Lifecycle client | `SUPPORTED, HOSTED-ONLY`: `proofline.receipt.v1` is built only after `FINALIZED`, `FINISHED_WITH_RETURN`, matching chain/contract provenance, `LATEST_FINAL` readback, and a true finalization observation timestamp. Public `from_decision` cannot claim hosted finality. |
| Adapter consumption | Local adapter | `HOSTED-VERIFIED`: current `0x30829…Df26b` semantic result produced `RELEASE_READY`; repeat delivery is idempotent and tampering is rejected. Direct receipts require explicit test-only opt-in. |
| Transaction/status client | Consensus v0.6 RC Python SDK | `IMPLEMENTED, READ-ONLY`: snake_case `get_transaction`, `wait_for_transaction_receipt`, `read_contract`, and `ExecutionResult`. |
| Hosted transaction | Studio Next chain 61997 | `VERIFIED`: corrected deployment `0x30829…Df26b`; fresh owner registration and source-backed semantic submission finalized successfully. `LATEST_FINAL`, receipt provenance, and adapter were independently verified. Evidence is not external customer work, and no valid committee disagreement was observed. |
| Connected public app | Existing Vercel project and injected wallet | `HOSTED-VERIFIED`: User-performed wallet registration/submission for `browser-1790227078107` on current contract `0x30829d13D0d86a9ae83Dc5e832Fb4AADd43Df26b`; both calls finalized, `LATEST_FINAL` matched `ACCEPT / CRITERION_MET`, receipt passed, and LocalAdapter returned `RELEASE_READY`. Chain/API verification is independent; the verifier did not witness the wallet prompt. |

## Terminology

- `ACCEPT`, `REJECT`, and `UNDETERMINED` are application outcomes.
- RPC, evaluator, transaction, timeout, SDK, and runtime failures are technical
  failures and are never rewritten as `UNDETERMINED` or `REJECT`.
- `DIRECT_MODE` and `LOCAL_ONLY` are local provenance labels, never hosted
  finality.
