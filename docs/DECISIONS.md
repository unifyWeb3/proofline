# Decisions

1. `VERIFIED`: Proofline remains a narrow semantic acceptance adapter, not a
   generic court, proof rail, custody system, bridge, or frontend.
2. `VERIFIED`: The contract owner is the canonical authority for agreement,
   policy, authorized submitter, and job ID through one-time registration.
3. `VERIFIED`: Transaction-sender authentication uses the supported
   `gl.message.sender_address` primitive. Detached ECDSA verification is
   off-chain only and explicitly unsupported in the contract.
4. `VERIFIED`: The contract emits `proofline.decision.v1`; the lifecycle client
   alone constructs `proofline.receipt.v1` after hosted finality, successful
   execution, and final-state readback.
5. `VERIFIED`: Deterministic validation runs before semantic evaluation. Valid
   deterministic failures are `REJECT`; malformed/authentication failures are
   technical `UserError` failures. Replay state is committed only with a stored
   deterministic rejection or semantic decision; evaluator failures are
   stateless and retryable.
6. `VERIFIED`: Application outcomes are `ACCEPT`, `REJECT`, and
   `UNDETERMINED`; evaluator invocation and malformed/contradictory evaluator
   output, RPC, protocol, and runtime failures are not application verdicts.
7. `VERIFIED`: Evidence is exact hash-bound `embedded://` fixture content for
   the MVP. The accepted policy is deliberately narrow: exactly one complete
   `application/json` response artifact under the registered embedded path.
   Mutable web retrieval and private storage are deferred.
8. `VERIFIED`: Stable local/direct dependencies are `genlayer-py==0.16.3` and
   `genlayer-test==0.29.2`; `requirements-rc.txt` is a separate RC lane.
9. `VERIFIED`: Direct mode proves leader execution, parsing, and state
   boundaries only. Hosted consensus, disagreement, appeal, and finality are
   `HOSTED_ONLY / NOT PROVEN BY DIRECT MODE`.
10. `VERIFIED`: No deployment, wallet operation, private-key read, fee payment,
    or hosted write is authorized in this session.
11. `VERIFIED`: A hosted receipt requires `FINALIZED`,
    `FINISHED_WITH_RETURN`, matching chain/contract readback, and a true
    finalization observation timestamp; transaction creation time is not
    relabeled as finalization.
