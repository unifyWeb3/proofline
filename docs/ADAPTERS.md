# Downstream Adapters

The local adapter consumes one canonical `proofline.receipt.v1`. Hosted
receipts require a supplied read-only `GenLayerLifecycleClient`; the adapter
re-observes the transaction, requires `FINALIZED` plus
`FINISHED_WITH_RETURN`, performs the `LATEST_FINAL` decision readback, and
compares the resulting canonical receipt digest before applying any signal.
This prevents caller-supplied finality fields or edited serialized receipts
from becoming adapter input. Direct-mode receipt acceptance is an explicit
test-only option and is never a hosted finality claim.

| Application verdict | Signal | Meaning |
|---|---|---|
| `ACCEPT` | `RELEASE_READY` | A downstream system may evaluate release policy. |
| `REJECT` | `HOLD` | A downstream system must not release automatically. |
| `UNDETERMINED` | `MANUAL_REVIEW_REQUIRED` | Human or policy workflow review is required. |

Signals are readiness projections only. They do not mean money moved, escrow
released, tokens transferred, a bridge completed, or another chain changed.
Idempotency is keyed by the finalized receipt digest; conflicting receipts for
one agreement are rejected before any external side effect.
