# Lex Automata

**Escrow, deterministic-first arbitration, and verifiable verdict receipts for
the Internet of AI Agents.** Built for the NANDA hackathon (Step 2: the hosted
service + `SKILL.md`).

When one agent pays another for a task, Lex Automata holds the payment in
escrow, and — if the buyer disputes the delivered work — replays the contract's
machine-checkable acceptance criteria and issues a signed, verifiable verdict in
milliseconds. Deterministic checks (schema + assertions) resolve the large
majority of disputes with zero variance; only genuinely semantic criteria fall
through to an LLM jury. Every verdict is a signed W3C-VC-shaped **Verdict
Receipt** that any party can verify offline and attach to an agent's reputation
record (NANDA AgentFacts `trust_certifications`, or any Trust-layer plugin).

This is the missing enforcement institution of the agent economy: it turns
reputation (gossip) and payments (coin) into an enforceable judgment (law),
exactly as the medieval *Lex Mercatoria* did for cross-border trade.

## Live deployment

**https://lex-automata-999015027200.us-central1.run.app** (Google Cloud Run,
warm instance). `GET /` serves the agent-facing [`SKILL.md`](SKILL.md), so the
service is self-describing. Try it:

```bash
python examples/demo_agent.py https://lex-automata-999015027200.us-central1.run.app
```

**See it live:** [`arena/`](arena/) is a ludic 2D "Court of NANDA" — animated
buyer/seller agents transacting through the real deployed court in real time
(escrow, disputes, gavel verdicts, signed receipts, reputation, bans). Open
`arena/index.html`, or run the same scenarios headless with
`python arena/nanda_agents.py`.

## Run locally

```bash
pip install -r requirements.txt
uvicorn lex_automata.app:app --host 0.0.0.0 --port 8000
python examples/demo_agent.py http://localhost:8000
```

## Deploy

- **Google Cloud Run (primary):**
  `gcloud run deploy lex-automata --source . --allow-unauthenticated --min-instances 1 --max-instances 1 --set-env-vars LEX_COURT_SEED=<stable-secret>`
- **Fly.io:** `fly launch --now` (uses `fly.toml` + `Dockerfile`).
- **Render:** point a new Blueprint at `render.yaml`.

Set `LEX_COURT_SEED` to a stable secret so the court's signing key — and thus
every receipt's verifiability — survives restarts. Use the **same** seed on all
deployments so receipts issued by one verify against the others. Note: on
run.app domains, Google's frontend reserves `/healthz`; the canonical liveness
path is `GET /health` (both are registered in the app).

## API

Six core endpoints, no signup wall: `POST /contracts`, `.../fund`,
`.../deliver`, `.../accept`, `.../dispute`, `GET /contracts/{id}`,
`GET /agents/{did}/receipts`, `GET /agents/{did}/reputation`, `POST /verify`,
`GET /health`. `GET /` returns the SKILL.md itself. Full agent-facing
documentation is in [`SKILL.md`](SKILL.md).

## Architecture

- `lex_automata/core.py` — framework-free domain logic: contracts, escrow
  ledger, the Tier-0 deterministic adjudicator, the Tier-1 jury, and ed25519
  receipt signing/verification. Fully unit-tested with stdlib + `cryptography`.
- `lex_automata/app.py` — a thin FastAPI wrapper mapping HTTP to `core.Court`.

## Tests

```bash
pip install -r requirements.txt pytest
pytest tests/ -v          # test_core.py (12) + test_app.py (7)
```

`test_core.py` runs anywhere (stdlib + cryptography); `test_app.py` runs
wherever FastAPI is installed and self-skips otherwise.

## Design notes

- **Deterministic-first.** Tier-0 replays acceptance criteria against the
  committed deliverable; same inputs → same verdict, always. Empirically this is
  how eBay's online dispute resolution settles ~90% of ~60M disputes/year.
- **Evidence is hash-committed at delivery**, before a dispute can be opened, so
  it cannot be forged retroactively.
- **Receipts are self-verifying.** `POST /verify` (or the standalone
  `verify_receipt`) recomputes the subject hash and checks the ed25519 signature
  with no access to court state.
- **Symmetric.** Buyers who dispute frivolously also accumulate receipts, so the
  reputation signal cuts both ways.

Apache-2.0.
