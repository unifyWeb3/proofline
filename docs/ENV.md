# Environment and local setup

The connected frontend uses Studio-dev configuration from the matching
Consensus v0.6 RC SDK. It does not require a `.env` file or server-side signing
credentials. In the browser, users authorize writes through an injected wallet;
the Python API prepares unsigned transactions and reads/verifies lifecycle
state.

## Verified network and toolchain

- Network: Studio Next / Studio-dev, chain `61997`.
- Canonical RPC: `https://studio-dev.genlayer.com/api`.
- Current contract: `0x30829d13D0d86a9ae83Dc5e832Fb4AADd43Df26b`.
- RC packages: `genlayer-py==0.19.0rc2`, `genlayer-test==0.30.0rc2`, and
  `genvm-linter==0.11.1rc2`.
- GenVM runner: `v0.6.0-rc5`.

Install the connected app and RC test dependencies with:

```bash
python -m venv .venv-rc
. .venv-rc/bin/activate
pip install -r requirements-rc.txt
```

Run the local API/UI with `python frontend/server.py --port 8787`. The same
stateless API is exposed on Vercel through the root `app.py` WSGI entrypoint.
No local filesystem persistence or long-running shared process is required
between API requests.

## Environment variables and signing

The current production app requires no Vercel environment variables. The
checked-in `.env.example` is a safe blank template for optional local tools; it
is not loaded by the browser app. Do not put wallet material, private keys,
validator keys, or signing credentials in `.env`, Vercel variables, frontend
configuration, or browser storage. In particular, `GENLAYER_PRIVATE_KEY` is
not used by the connected frontend and must remain unset for this flow.

The stable package lane is retained for historical local/direct evidence only.
It is not interchangeable with the v0.6 RC toolchain for Studio Next.
