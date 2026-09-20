# Proofline

[Live app](https://proofline-nu.vercel.app) · [GitHub](https://github.com/unifyWeb3/proofline)

Proofline turns policy, agreement, and evidence into a finalized GenLayer
decision that anyone can verify. It produces a provenance-bound receipt and a
clear LocalAdapter readiness signal.

The public app is live on Vercel and uses Studio Next chain `61997` with the
deployed contract `0x6eb8E208666694e9948E87aa46294aA349fD2014`.

## What it solves

Applications often need a decision that can be checked later, rather than a
local boolean or an opaque model response. Proofline binds policy, agreement,
evidence, evaluator output, protocol finality, and the producing transaction
into one auditable result.

## Lifecycle

1. Register a job with the exact policy and agreement.
2. Submit a canonical evidence envelope.
3. GenLayer executes the contract and reaches consensus.
4. Read the finalized result through `LATEST_FINAL`.
5. Construct `proofline.receipt.v1` only after successful execution and finality.
6. Verify the receipt and pass its verdict to `LocalAdapter`.

A technical execution failure remains a technical failure. It is not converted
into a semantic rejection.

## Architecture

- `contracts/proofline.py` contains the GenLayer intelligent contract.
- `proofline/` contains canonical schemas, hashing, lifecycle observation,
  receipt construction, and the idempotent LocalAdapter.
- `frontend/` provides a small browser flow using an injected wallet provider.
  Signing material never enters the browser or frontend configuration.
- `tests/` contains unit and GenLayer direct-mode coverage.

The frontend uses the matching Python RC client for fee preparation, lifecycle
observation, final readback, receipt verification, and adapter readiness. The
browser only authorizes unsigned transactions through `window.ethereum`.

## GenLayer integration

The verified hosted target is Studio Next chain `61997` using the canonical RPC
`https://studio-dev.genlayer.com/api`. The deployed Proofline contract is
`0x6eb8E208666694e9948E87aa46294aA349fD2014`.

The public evidence records a successful semantic run with
`FINISHED_WITH_RETURN`, `MAJORITY_AGREE`, `FINALIZED`, an `ACCEPT` decision, a
matching `LATEST_FINAL` readback, and a producing-transaction-bound receipt.

## Receipts and LocalAdapter

`proofline.receipt.v1` is created only from verified lifecycle observation. Its
provenance binds the producing `submit_job` transaction to the expected
contract, job, transaction effect, finalized decision, and canonical decision
digest. `LocalAdapter` re-observes hosted finality, is idempotent on repeated
delivery, and returns readiness signals such as `RELEASE_READY` or `HOLD`.
These signals do not imply settlement, custody, payments, or funds movement.

## Local setup

Use Python 3.12 or newer. For the connected frontend and Vercel runtime,
install the matching Consensus v0.6 RC client:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

For the historical stable direct-test lane, install:

```bash
pip install -r requirements-direct.txt
```

For the full Consensus v0.6 RC hosted/readback and test path, install the pinned RC lane:

```bash
python -m venv .venv-rc
. .venv-rc/bin/activate
pip install -r requirements-rc.txt
```

The frontend server expects the RC client and serves the browser surface on port
8787:

```bash
. .venv-rc/bin/activate
python frontend/server.py --port 8787
```

Vercel loads the same stateless API through the root `app.py` WSGI entrypoint.
The API reads authoritative lifecycle state from Studio Next on each request and
does not persist transaction or wallet state locally.

User signing is performed by an injected wallet. No private key belongs in
`.env`, frontend source, browser storage, or public configuration.

## Tests

```bash
pytest -q tests/unit
pytest -q tests/direct
python -m compileall -q proofline frontend tests
python -m pip check
```

The completed project verification recorded 49 unit tests and 35 direct tests
passing, plus compile, dependency, JavaScript syntax, and frontend security
checks.

## Verified status and limitations

Proofline Milestones 1–3 are complete. The hosted and browser verification
artifacts are under `evidence/`. The live browser flow has verified wallet
connection, job registration and submission, finalized readback, receipt
verification, and LocalAdapter readiness on Studio Next. Studio Next remains a
development preview; this project does not claim mainnet deployment, custody,
payments, settlement, or release hardening.

No license has been selected for this release yet.
