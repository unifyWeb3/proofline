# Proofline

**Verifiable acceptance for agent work.** Define what “done” means, submit the
completed work, and get a GenLayer-finalized verdict with a receipt bound to the
transaction that produced it.

[Launch Proofline](https://proofline-nu.vercel.app) · [GitHub](https://github.com/unifyWeb3/proofline)

## How it works

1. The contract owner registers a job’s policy and agreement.
2. The authorized submitter sends the request and completed work as evidence.
3. GenLayer evaluates the submission and reaches consensus.
4. Proofline reads the authoritative finalized result, verifies its producing
   transaction, and constructs a `proofline.receipt.v1`.
5. LocalAdapter reports whether the result is ready for a downstream policy.

The latest verified browser run returned **ACCEPT · CRITERION_MET** for job
`browser-1790227078107`. Its transactions finalized on Studio Next with
`FINISHED_WITH_RETURN / MAJORITY_AGREE`; the receipt passed verification and
LocalAdapter returned `RELEASE_READY`. This is a readiness signal, not evidence
of settlement or funds movement. See the
[sanitized run record](evidence/milestone4-browser-verification-20260924.json).

## Verified deployment

- App: <https://proofline-nu.vercel.app>
- Network: Studio Next, chain `61997`
- Contract: `0x30829d13D0d86a9ae83Dc5e832Fb4AADd43Df26b`
- RPC: `https://studio-dev.genlayer.com/api`

## Run locally

Python 3.12+ and the Consensus v0.6 RC toolchain are used by the connected API
and direct contract tests:

```bash
python -m venv .venv-rc
. .venv-rc/bin/activate
pip install -r requirements-rc.txt
python frontend/server.py --port 8787
```

Open `http://localhost:8787`. The API is stateless between requests. Browser
writes are authorized by an injected wallet; the server prepares unsigned
transactions and never receives a private key.

Run tests with:

```bash
pytest -q tests/unit
GENVM_VERSION=v0.6.0-rc5 pytest -q tests/direct
python -m compileall -q proofline frontend tests
python -m pip check
```

## Architecture and limits

`contracts/` contains the GenLayer contract; `proofline/` implements canonical
schemas, lifecycle checks, receipts, and LocalAdapter; `frontend/` contains the
static public site, verification workspace, and Python API. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
[`docs/API.md`](docs/API.md), and the
[verification record index](evidence/README.md).

Studio Next is a development network. No mainnet deployment, payment, custody,
or settlement is claimed. Direct tests exercise the pinned validator comparison
but do not prove committee-level disagreement; none was observed in the hosted
runs. A project license has not been selected.
