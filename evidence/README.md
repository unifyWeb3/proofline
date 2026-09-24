# Public verification records

## Current deployment

- [`milestone4-browser-verification-20260924.json`](milestone4-browser-verification-20260924.json)
  records the user-performed Production browser flow on chain `61997` and
  contract `0x30829d13D0d86a9ae83Dc5e832Fb4AADd43Df26b`. It contains hashes and
  verified lifecycle facts, not raw evidence bytes or wallet signatures.
- [`fee-profile-milestone4-semanticfix.json`](fee-profile-milestone4-semanticfix.json)
  is the measured fee input used by the current frontend API.
- [`milestone4-genuine-semanticfix-independent-verification.json`](milestone4-genuine-semanticfix-independent-verification.json)
  backs the existing verified homepage example on this contract. It is a
  source-comparison demonstration, not external customer work.

## Historical deployments

The Milestone 2/3 records below are retained as historical evidence for the
then-deployed contract `0x6eb8E208666694e9948E87aa46294aA349fD2014`. They do
not establish the current contract's behavior:

- `milestone2-hosted-correction.json`
- `fee-profile-milestone2-correction.json`
- `milestone3-fee-preflight.json`
- `milestone3-frontend-verification.json`
- `historical/milestone3-browser-independent-verification.json` (independent
  readback for that earlier flow)

Older pre-correction records remain historical as well. Current Production
status is documented in the Milestone 4 browser record and the linked project
documentation. `RELEASE_READY` is an adapter readiness signal; it does not
indicate payment, settlement, custody, or funds movement.
